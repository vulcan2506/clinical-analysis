"""
ingest.py
─────────
Extracts a single PDF using Docling TableFormer, formats tables to JSONL, 
applies structural chunking, and runs LLM cohesion checks.
"""

import re
import os
import math
import json
import time
import base64
import logging
import multiprocessing as mp
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Optional
from pypdf import PdfReader, PdfWriter
from docling.datamodel.pipeline_options import AcceleratorOptions, PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption

from keybert import KeyBERT
from sentence_transformers import SentenceTransformer
from tqdm import tqdm
import torch
import fitz  # PyMuPDF — page-image rendering for vision OCR
import openai

import config
import llm_client

log = logging.getLogger(__name__)

# ── Singletons ─────────────────────────────────────────────────────────────────
_embedder:      Optional[SentenceTransformer] = None
_keybert_model: Optional[KeyBERT]             = None

def unload():
    """Free all GPU models held by this module."""
    global _embedder, _keybert_model
    del _embedder, _keybert_model
    _embedder = None
    _keybert_model = None
    _free_vram()


def _free_vram():
    import gc
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        log.info(f"Loading embedder: {config.EMBEDDING_MODEL}")
        _embedder = SentenceTransformer(config.EMBEDDING_MODEL, device="cpu")
    return _embedder

def _get_keybert() -> KeyBERT:
    global _keybert_model
    if _keybert_model is None:
        _keybert_model = KeyBERT(model=_get_embedder())
    return _keybert_model

# ── STEP 1: Docling Multiprocessing Extraction (Replaces pymupdf4llm) ──────────

def _docling_worker(chunk_idx: int, start_page: int, end_page: int, pdf_path: str) -> tuple[int, str]:
    """Isolated worker for Docling extraction."""
    try:
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"
        
        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        for page_num in range(start_page, end_page):
            writer.add_page(reader.pages[page_num])
            
        temp_path = Path(f"/tmp/docling_chunk_{os.getpid()}_{chunk_idx}.pdf")
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_path, "wb") as f:
            writer.write(f)
            
        accelerator = AcceleratorOptions(num_threads="16" ,device="cpu")
        pipeline_opts = PdfPipelineOptions(accelerator_options=accelerator)
        pipeline_opts.do_ocr = False 
        pipeline_opts.do_table_structure = True 
        pipeline_opts.table_structure_options.mode = TableFormerMode.ACCURATE
        pipeline_opts.generate_page_images = False 

        converter = DocumentConverter(
            format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_opts)}
        )
        
        result = converter.convert(temp_path)
        markdown_text = result.document.export_to_markdown()
        
        if temp_path.exists():
            temp_path.unlink()
            
        return chunk_idx, markdown_text
        
    except Exception as e:
        log.error(f"Docling worker failed on chunk {chunk_idx}: {e}")
        return chunk_idx, ""

def _extract_pdf_with_docling(pdf_path: Path) -> List[Dict]:
    """
    Orchestrates memory-safe multiprocessing.
    Returns a list of dicts matching the old pymupdf4llm format so existing code works.
    """
    try:
        mp.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    pages_per_chunk = getattr(config, 'PAGE_BATCH_SIZE', 4)
    workers = getattr(config, 'N_WORKERS', 5)
    total_chunks = math.ceil(total_pages / pages_per_chunk)
    
    tasks = []
    for i in range(total_chunks):
        start_page = i * pages_per_chunk
        end_page = min(start_page + pages_per_chunk, total_pages)
        tasks.append((i, start_page, end_page, str(pdf_path)))
        
    results_dict = {}
    with mp.Pool(processes=workers, maxtasksperchild=1) as pool:
        for idx, text in pool.starmap(_docling_worker, tasks):
            results_dict[idx] = text
            
    # Bridge to old pymupdf4llm format
    md_pages = []
    for i in range(total_chunks):
        if i in results_dict and results_dict[i].strip():
            md_pages.append({"text": results_dict[i]})
            
    return md_pages


# ── STEP -1: Mistral document OCR (primary — falls back to Groq, then Docling) ─

def _extract_pdf_with_mistral(pdf_path: Path) -> List[Dict]:
    """
    Primary OCR path. Mistral's dedicated document-OCR endpoint processes the
    entire PDF in a single request (not per-page, unlike the Groq fallback
    below) and returns per-page markdown plus a separate structured `tables`
    array. The page markdown references tables as `[tbl-N.md](tbl-N.md)`
    placeholder links rather than inlining them, so those links are spliced
    out here and replaced with the real table markdown before handing off to
    _chunk_by_structure, which expects tables inline as consecutive `|...|`
    lines — same contract the Groq/Docling paths already produce.
    """
    client = llm_client.get_mistral_client()
    b64 = base64.standard_b64encode(pdf_path.read_bytes()).decode("utf-8")

    resp = client.ocr.process(
        model=config.MISTRAL_OCR_MODEL,
        document={"type": "document_url", "document_url": f"data:application/pdf;base64,{b64}"},
        table_format="markdown",
    )

    md_pages = []
    for page in resp.pages:
        text = page.markdown
        for table in (page.tables or []):
            text = text.replace(f"[{table.id}]({table.id})", table.content)
        md_pages.append({"text": text})
    return md_pages


# ── STEP 0: Groq vision OCR (fallback 1 — falls back to Docling on exception) ──

_OCR_PROMPT = (
    "Transcribe this document page to clean Markdown. Preserve section headers "
    "as Markdown headers (#, ##, ###). Preserve every table as a GitHub-flavored "
    "Markdown table (| col | col |). Transcribe all body text verbatim — do not "
    "summarize, paraphrase, or omit any content, including footnotes and ticket "
    "references. If the page is blank or contains no extractable content, return "
    "an empty string. Output ONLY the Markdown — no commentary, no code fences."
)


def _render_page_png(pdf_path: Path, page_num: int, dpi: int) -> bytes:
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        return page.get_pixmap(matrix=mat).tobytes("png")
    finally:
        doc.close()


def _ocr_page_with_groq(pdf_path: Path, page_num: int) -> str:
    """
    Rate-limit-aware: Groq's on-demand tier TPM cap is small relative to a
    vision request's cost, so 429s are an expected steady-state condition on
    documents beyond ~10 pages, not an exceptional one. Retried here with a
    real backoff (long enough for the rolling 60s window to partially clear)
    rather than treated as a page failure — a single 429 shouldn't drop the
    whole document to the Docling fallback via _extract_pdf_primary. Any
    other exception (auth, malformed request, etc.) still propagates
    immediately, same as before.
    """
    client = llm_client.get_groq_client()
    image_b64 = base64.standard_b64encode(
        _render_page_png(pdf_path, page_num, config.OCR_PAGE_DPI)
    ).decode("utf-8")

    kwargs = dict(
        model=config.GROQ_VISION_MODEL,
        max_tokens=config.OCR_MAX_TOKENS,
        temperature=0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
                {"type": "text", "text": _OCR_PROMPT},
            ],
        }],
    )

    for attempt in range(config.GROQ_VISION_RATE_LIMIT_RETRIES):
        try:
            resp = client.chat.completions.create(**kwargs)
            return (resp.choices[0].message.content or "").strip()
        except openai.RateLimitError:
            if attempt == config.GROQ_VISION_RATE_LIMIT_RETRIES - 1:
                raise
            log.warning(
                f"Groq TPM limit hit on {pdf_path.name} page {page_num} — "
                f"waiting {config.GROQ_VISION_RATE_LIMIT_BACKOFF}s "
                f"(attempt {attempt + 1}/{config.GROQ_VISION_RATE_LIMIT_RETRIES})"
            )
            time.sleep(config.GROQ_VISION_RATE_LIMIT_BACKOFF)


def _extract_pdf_with_groq(pdf_path: Path) -> List[Dict]:
    """
    Primary OCR path: renders every page to an image and transcribes it via
    Groq's Llama-4 Scout vision model, in parallel (I/O-bound — threads, not
    the mp.Pool used for Docling's CPU-bound extraction). Any page failure
    (API error, timeout, refusal) propagates out of this function so the
    caller falls back to the whole-document Docling path — no per-page silent
    degrade, to avoid mixing inconsistent extraction quality within one
    document.
    """
    total_pages = len(PdfReader(pdf_path).pages)
    md_pages: List[Optional[Dict]] = [None] * total_pages

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.GROQ_VISION_PARALLEL_SLOTS) as pool:
        futures = {
            pool.submit(_ocr_page_with_groq, pdf_path, i): i
            for i in range(total_pages)
        }
        for fut in concurrent.futures.as_completed(futures):
            page_num = futures[fut]
            md_pages[page_num] = {"text": fut.result()}  # raises → caller falls back

    return md_pages


def _extract_pdf_primary(pdf_path: Path) -> List[Dict]:
    """
    3-tier OCR chain: Mistral document OCR (primary) → Groq Llama-4 Scout
    vision OCR (fallback 1) → Docling TableFormer (fallback 2, no external
    API dependency). Each tier is only tried if the previous one raises —
    no per-page/per-tier silent degrade based on output quality.
    """
    if not config.USE_VISION_OCR:
        return _extract_pdf_with_docling(pdf_path)

    try:
        log.info(f"Extracting {pdf_path.name} via Mistral OCR (primary)...")
        return _extract_pdf_with_mistral(pdf_path)
    except Exception as e_mistral:
        log.error(
            f"Mistral OCR failed for {pdf_path.name} ({type(e_mistral).__name__}: {e_mistral}) — "
            f"falling back to Groq vision OCR"
        )

    try:
        log.info(f"Extracting {pdf_path.name} via Groq vision OCR (fallback 1)...")
        return _extract_pdf_with_groq(pdf_path)
    except Exception as e_groq:
        log.error(
            f"Groq OCR failed for {pdf_path.name} ({type(e_groq).__name__}: {e_groq}) — "
            f"falling back to Docling extraction"
        )
        return _extract_pdf_with_docling(pdf_path)


# ── Cleaners & Converters (RESTORED EXACTLY AS PROVIDED) ───────────────────────

def _is_toc_page(md_text: str) -> bool:
    lines = md_text.splitlines()
    if not lines:
        return False
    toc_lines = sum(1 for ln in lines if re.search(r"\]\(#page=\d+\)|\.\.\.\.\s*\d+$", ln))
    return (toc_lines / len(lines)) > 0.35

def _is_alphabetical_index(md_text: str) -> bool:
    return bool(re.search(r"^#{1,3}\s+Index\b", md_text.strip(), re.IGNORECASE))

def _clean_text(text: str) -> str:
    text = text.replace("<br>", "\n")
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = re.sub(r" +", " ", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    return text.strip()

def _is_garbled_header(raw_headers: List[str]) -> bool:
    """
    A genuine column header ('Test', 'Result', 'Unit') never contains a digit.
    On borderless tables (e.g. ruling-free lab report grids), TableFormer can
    fuse the real header row into the first data row instead of emitting it
    on its own line — e.g. '14.5 Result' or '13.0 - 16.5 Biological Ref.
    Interval' as a "header" cell. Trusting that as a JSON key would silently
    mis-map every row beneath it under a nonsense column name.
    """
    return any(re.search(r"\d", h) for h in raw_headers)

def _md_table_to_jsonl(table_lines: List[str]) -> str:
    if len(table_lines) < 3:
        return "\n".join(table_lines)

    all_jsonl = []
    sep_indices = [i for i, line in enumerate(table_lines) if re.match(r"^\|[\s\-:|]+\|$", line.strip())]
    if not sep_indices:
        sep_indices = [1]

    for idx_count, sep_idx in enumerate(sep_indices):
        header_idx = sep_idx - 1
        if header_idx < 0: continue

        end_idx = sep_indices[idx_count+1] - 1 if idx_count + 1 < len(sep_indices) else len(table_lines)
        raw_headers = [h.strip() for h in table_lines[header_idx].strip().strip("|").split("|")]

        if _is_garbled_header(raw_headers):
            # Header row is actually fused data — don't trust it as column
            # names. Fall back to generic labels and keep the row itself as
            # the first data row instead of discarding its content.
            headers = [f"Column_{i+1}" for i in range(len(raw_headers))]
            data_lines = [table_lines[header_idx]] + table_lines[sep_idx + 1:end_idx]
        else:
            headers, counts = [], {}
            for h in raw_headers:
                h = _clean_text(h)
                if h in counts:
                    counts[h] += 1
                    headers.append(f"{h}_{counts[h]}")
                else:
                    counts[h] = 0
                    headers.append(h)
            data_lines = table_lines[sep_idx + 1:end_idx]

        for line in data_lines:
            if not line.strip(): continue
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            while len(cols) > len(headers):
                headers.append(f"Column_{len(headers)+1}")
            row = {headers[i]: _clean_text(cols[i]) if i < len(cols) else "" for i in range(len(headers))}
            if any(row.values()):
                all_jsonl.append(json.dumps(row))
                
    return "\n".join(all_jsonl)

def _detect_section_break(line: str) -> Optional[str]:
    s = line.strip()
    if 5 < len(s) < 200:
        if s.isupper():
            # Pure-alpha uppercase is always a section break.  Uppercase
            # lines that contain digits are section breaks only when the
            # line is *mostly* alphabetic (e.g. "VITAMIN B12; CYANOCOBALAMIN
            # (CLIA)") — reject lines that are mostly digits/numbers
            # (e.g. "2026 ACC-AHA...", "Page 123 of 456").
            alpha_count = sum(1 for c in s if c.isalpha())
            if not re.search(r"\d{2,}", s) or alpha_count / max(len(s), 1) > 0.6:
                return s
        if (s.startswith("**") and s.endswith("**")) or (s.startswith("__") and s.endswith("__")):
            return s.strip("*_").strip()
    return None


_TAB_DATA_RE = re.compile(
    r"(?:pg/mL|ng/mL|mg/dL|mmol/L|mIU/L|U/mL|μIU/mL|%|nmol/L|g/dL|"
    r"x10[³6]/μL|fL|pg|fl|g/L|mEq/L|mm/hr|mm/h)",
    re.IGNORECASE,
)


def _is_tab_data_row(line: str) -> bool:
    """Detect lab-report-style tab-separated data rows.

    A row qualifies when it has ≥2 tab-separated fields AND at least one
    field is numeric or contains medical-unit tokens.  This prevents false
    positives on plain prose that happens to contain a tab.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("|"):
        return False
    parts = stripped.split("\t")
    if len(parts) < 2:
        return False
    has_number = any(re.search(r"\d+\.?\d*", p) for p in parts)
    has_units = bool(_TAB_DATA_RE.search(stripped))
    return has_number or has_units


def _tab_rows_to_jsonl(header: str, data_lines: List[str]) -> str:
    """Convert tab-separated lab-report rows to JSONL.

    Infers column names from the data pattern:
      - First column is always "Test" (test name / analyte)
      - If any row has ≥4 fields → Test | Result | Unit | Reference_Range
      - If any row has 3 fields  → Test | Result | Unit
      - If any row has 2 fields  → Test | Value
      - Otherwise                → Column_1 .. Column_N
    """
    if not data_lines:
        return ""

    max_cols = max(len(ln.strip().split("\t")) for ln in data_lines)

    if max_cols >= 4:
        headers = ["Test", "Result", "Unit", "Reference_Range"]
    elif max_cols == 3:
        headers = ["Test", "Result", "Unit"]
    elif max_cols == 2:
        headers = ["Test", "Value"]
    else:
        headers = [f"Column_{i+1}" for i in range(max_cols)]

    rows = []
    for line in data_lines:
        fields = [f.strip() for f in line.strip().split("\t")]
        while len(fields) < len(headers):
            fields.append("")
        row = {headers[i]: _clean_text(fields[i]) for i in range(len(headers))}
        if any(row.values()):
            rows.append(json.dumps(row, ensure_ascii=False))

    return "\n".join(rows)


def _chunk_by_structure(md_pages: List[Dict]) -> List[Dict]:
    chunks, current_header = [], "Introduction / Front Matter"
    current_text, current_table = [], []
    in_table = False
    current_pages: set = set()

    # Tab-separated lab-data state
    in_tab_data = False
    tab_data_lines: List[str] = []
    tab_header: str = ""
    tab_pages: set = set()
    tab_header_is_section = False  # True when header came from _detect_section_break
    tab_pending_section: Optional[str] = None  # buffered section-break line for lookahead

    def flush_text():
        body = _clean_text("\n".join(current_text))
        if body and not re.match(r"^#+\s*$", body):
            chunks.append({"section_header": current_header, "text": body, "pages": sorted(current_pages)})
        current_text.clear(); current_pages.clear()

    def flush_table():
        if current_table:
            body = _md_table_to_jsonl(current_table)
            raw_md = "\n".join(current_table)
            if body.strip():
                chunks.append({
                    "section_header": current_header + " (Table)",
                    "text": body,
                    "_temp_raw_md": raw_md,
                    "pages": sorted(current_pages)
                })
        current_table.clear(); current_pages.clear()

    def flush_tab_data():
        nonlocal in_tab_data, tab_data_lines, tab_header, tab_pages, tab_header_is_section
        if tab_data_lines:
            body = _tab_rows_to_jsonl(tab_header, tab_data_lines)
            if body.strip():
                suffix = " (Table)" if not tab_header.endswith("(Table)") else ""
                chunks.append({
                    "section_header": tab_header + suffix,
                    "text": body,
                    "pages": sorted(tab_pages)
                })
        tab_data_lines = []
        tab_pages = set()
        in_tab_data = False
        tab_header_is_section = False

    for page_num, page in enumerate(md_pages, start=1):
        for line in page.get("text", "").splitlines():
            is_pipe_table = line.strip().startswith("|") and line.strip().endswith("|")
            is_tab_row = _is_tab_data_row(line)
            hm = re.match(r"^(#{1,6})\s+(.*)", line.strip())
            sb = _detect_section_break(line)

            # ── Pipe-delimited table ──────────────────────────────────────
            if is_pipe_table:
                if in_tab_data:
                    flush_tab_data()
                if not in_table:
                    flush_text(); in_table = True
                current_table.append(line); current_pages.add(page_num)
                continue

            if in_table:
                flush_table(); in_table = False

            # ── Markdown header (# ... ) ──────────────────────────────────
            if hm:
                if in_tab_data:
                    flush_tab_data()
                flush_text()
                current_header = hm.group(2).strip()
                current_pages.add(page_num)
                tab_pending_section = None
                continue

            # ── Tab-separated data row ────────────────────────────────────
            if is_tab_row:
                if in_tab_data and tab_pending_section:
                    # A section-break header was buffered while we were in
                    # tab data.  Flush the current group and start a new one
                    # under the pending header.
                    flush_tab_data()
                    in_tab_data = True
                    tab_header = tab_pending_section
                    tab_header_is_section = True
                    tab_data_lines = [line]
                    tab_pages = {page_num}
                    tab_pending_section = None
                elif in_tab_data:
                    tab_data_lines.append(line)
                    tab_pages.add(page_num)
                else:
                    # Use the buffered section-break as header if available;
                    # otherwise promote the most recent section_header.
                    hdr = tab_pending_section if tab_pending_section else current_header
                    in_tab_data = True
                    tab_header = hdr
                    tab_header_is_section = bool(tab_pending_section)
                    tab_data_lines = [line]
                    tab_pages = {page_num}
                    tab_pending_section = None
                continue

            # ── Section break (upper-case line) ───────────────────────────
            if sb:
                if in_tab_data:
                    # This section break follows tab data → it belongs to
                    # the table.  Buffer it for lookahead: the next line
                    # decides whether it becomes a *new* table header or we
                    # close the current table and start a text section.
                    tab_pending_section = sb
                    tab_pages.add(page_num)
                else:
                    flush_text()
                    current_header = sb
                    current_pages.add(page_num)
                # Always update current_header so that if the section break
                # is NOT followed by tab data, the header is still correct.
                current_header = sb
                continue

            # ── Plain text ────────────────────────────────────────────────
            if in_tab_data:
                # Non-tab, non-header line after tab data → table is over.
                flush_tab_data()
            current_text.append(line); current_pages.add(page_num)
            tab_pending_section = None

    if in_tab_data:
        flush_tab_data()
    flush_text(); flush_table()
    return chunks

def _extract_keywords_batch(texts: List[str], batch_size: int = 32) -> List[List[str]]:
    kw = _get_keybert()
    all_kw: List[List[str]] = []
    
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        try:
            results = kw.extract_keywords(
                batch,
                keyphrase_ngram_range=(config.KEYBERT_NGRAM_MIN, config.KEYBERT_NGRAM_MAX),
                stop_words="english", use_mmr=True, diversity=config.KEYBERT_DIVERSITY, top_n=config.KEYBERT_TOP_N,
            )
            if batch and not isinstance(results[0], list):
                results = [results]
            all_kw.extend([[k for k, _ in r] for r in results])
        except Exception:
            all_kw.extend([[] for _ in batch])
    return all_kw

# ── Cohesion Rectifier (RESTORED EXACTLY AS PROVIDED) ──────────────────────────

_RECTIFY_PROMPT = (
    "Does this text abruptly switch to a completely unrelated topic halfway through?\n"
    "If NO — reply exactly: COHESIVE\n"
    "If YES — reply with the first 5-8 words of the sentence where the new topic starts.\n\n"
    "Text: {text}\n\nAnswer:"
)

def _is_jsonl_table(text: str) -> bool:
    lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
    return bool(lines) and all(ln.startswith("{") and ln.endswith("}") for ln in lines)

def _rectify_batch(batch: List[Dict]) -> List[Dict]:
    text_idxs = [i for i, c in enumerate(batch) if not _is_jsonl_table(c["text"])]
    result = list(batch)

    if not text_idxs:
        return result

    prompts = [_RECTIFY_PROMPT.format(text=batch[i]["text"][:1500]) for i in text_idxs]
    outputs = llm_client.generate_local_batch(prompts, max_tokens=20, batch_size=16, desc="Cohesion Check", stop=llm_client.STOP_FLAG)

    splits = []
    for idx, response in zip(text_idxs, outputs):
        chunk = batch[idx]

        if "COHESIVE" not in response.upper() and len(response) > 10:
            target = response.replace('"', "").replace("'", "").strip()
            split_pos = chunk["text"].find(target)
            if split_pos > 50:
                p1 = chunk["text"][:split_pos].strip()
                p2 = chunk["text"][split_pos:].strip()
                if p1: result[idx] = {**chunk, "text": p1, "_rectified": True}
                if p2: splits.append({**chunk, "text": p2, "section_header": chunk["section_header"] + " (Cont.)", "_rectified": True})
                continue
        result[idx] = {**chunk, "_rectified": True}

    return result + splits


# ── Main API Function ──────────────────────────────────────────────────────────

def _load_preconverted_md(pdf_path: Path) -> Optional[List[Dict]]:
    """
    Check if a pre-converted markdown file exists for this PDF.
    Naming convention: <stem>_Converted.md  in the same directory.
    Returns md_pages list (same format as Docling output) or None.
    """
    md_path = pdf_path.with_name(pdf_path.stem + "_Converted.md")
    if not md_path.exists():
        return None

    log.info(f"Found pre-converted markdown: {md_path.name} — skipping Docling extraction")
    text = md_path.read_text(encoding="utf-8")

    # Split on markdown H2+ headers to approximate page-level chunks,
    # matching the structure Docling produces.
    sections = re.split(r'(?=^## )', text, flags=re.MULTILINE)
    md_pages = []
    for section in sections:
        section = section.strip()
        if section:
            md_pages.append({"text": section})

    log.info(f"Loaded {len(md_pages)} sections from {md_path.name}")
    return md_pages


def _save_converted_markdown(pdf_path: Path, md_pages: List[Dict]) -> None:
    """
    Persists freshly-extracted markdown next to the source PDF as
    <stem>_Converted.md — the same file _load_preconverted_md() already
    knows to read on a future run (skips re-extraction entirely next time),
    and what surfaces this PDF under the Knowledge Explorer's "Source
    Documents" section. Only called after a real extraction (never when
    md_pages was itself loaded from a pre-converted file, to avoid a
    pointless rewrite). Best-effort — extraction succeeding matters more
    than this cache write, so failures here only log a warning.
    """
    md_path = pdf_path.with_name(pdf_path.stem + "_Converted.md")
    try:
        text = "\n\n".join(
            page.get("text", "").strip() for page in md_pages if page.get("text", "").strip()
        )
        if text:
            md_path.write_text(text, encoding="utf-8")
            log.info(f"Saved converted markdown → {md_path.name}")
    except Exception as e:
        log.warning(f"Could not save converted markdown for {pdf_path.name}: {e}")


def process_single_pdf(pdf_path: Path, global_id: dict) -> List[Dict]:
    """
    Extracts a single PDF, runs keyword extraction, and applies text cohesion.
    Returns a list of chunks in memory.
    """
    log.info(f"Extracting: {pdf_path.name}...")

    temp_md_dir = config.OUTPUT_DIR / "temp_md"
    temp_md_dir.mkdir(parents=True, exist_ok=True)

    md_pages = _load_preconverted_md(pdf_path)
    if md_pages is None:
        md_pages = _extract_pdf_primary(pdf_path)
        _save_converted_markdown(pdf_path, md_pages)

    clean_pages = []
    toc_texts = []
    for page in md_pages:
        if _is_toc_page(page["text"]):
            toc_texts.append(page["text"])
            continue
        if _is_alphabetical_index(page["text"]): break
        clean_pages.append(page)

    if toc_texts:
        import context_profiler
        context_profiler.store_toc(pdf_path.name, "\n\n".join(toc_texts))

    raw_chunks = _chunk_by_structure(clean_pages)
    
    # Ban TOC headers
    valid = []
    for c in raw_chunks:
        hdr = c["section_header"].lower()
        if "contents" in hdr or "table of contents" in hdr or "index" in hdr:
            continue
        if len(c["text"].split()) > 10:
            valid.append(c)

    texts = [c["text"] for c in valid]
    kw_lists = _extract_keywords_batch(texts, batch_size=config.KEYBERT_BATCH_SIZE)

    pdf_chunks = []
    for chunk, kws in zip(valid, kw_lists):
        cid = global_id["id"]
        pages = chunk.get("pages", [])
        
        # In Docling batching, 'pages' represents the batch index. This keeps it stable.
        page_str = (f"{pages[0]}-{pages[-1]}" if len(pages) > 1 else str(pages[0]) if pages else "?")

        md_file_path = None
        # PRESERVES YOUR TEMP MD SAVING FOR RE-RECTIFIER
        if "_temp_raw_md" in chunk:
            md_path = temp_md_dir / f"table_{cid}.md"
            md_path.write_text(chunk["_temp_raw_md"], encoding="utf-8")
            md_file_path = str(md_path)

        pdf_chunks.append({
            "chunk_id": cid,
            "section_header": chunk["section_header"],
            "text": chunk["text"],
            "md_file_path": md_file_path,
            "keywords": kws,
            "source_doc": pdf_path.name,
            "page_range": page_str,
            "master_label": None,
            "description": None,
            "_rectified": False,
        })
        global_id["id"] += 1

    # --- Cohesion Rectify (In-Memory) ---
    if config.USE_LLM_NOISE_FILTER:
        batch_sz = config.RECTIFY_BATCH_SIZE * 4
        
        final_pdf_chunks = []
        for i in range(0, len(pdf_chunks), batch_sz):
            batch = pdf_chunks[i : i + batch_sz]
            final_pdf_chunks.extend(_rectify_batch(batch))
        return final_pdf_chunks
        
    return pdf_chunks