"""
label_normalizer.py
───────────────────
Post-labeling normalization and deduplication pass.

Runs after labeler.label_all_chunks() and before registry.build_registry().
Clusters near-duplicate labels via embedding similarity and assigns a
canonical label to each cluster.

Problems solved:
  - "Billing & Financials" vs "Billing and Financials" (symbol variant)
  - "Runtime Property" vs "Runtime Properties" (plural)
  - "COB Savings Adjustment" vs "COB Saving Adjustment" (typo)
  - DB table names used as labels (HCFA1500_SERVICE_LINE_FACT)
  - Single-word garbage labels ("Bug", "Modify", "Amend")
  - Keyword-concatenation fallback labels
"""

import re
import logging
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

import config

log = logging.getLogger(__name__)

_embedder: Optional[SentenceTransformer] = None


def unload():
    global _embedder
    if _embedder is not None:
        del _embedder
        _embedder = None
        import gc, torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(config.EMBEDDING_MODEL, device="cpu")
    return _embedder


# ── Text normalization (for clustering, not final output) ─────────────────────

def _normalize_for_clustering(label: str) -> str:
    """Deterministic normalization to help embedding similarity."""
    s = label.lower().strip()
    s = s.replace('&', 'and')
    s = re.sub(r'[^\w\s]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    words = s.split()
    words = [w for w in words if w not in ('the', 'a', 'an')]
    if words and words[-1].endswith('s') and len(words[-1]) > 5:
        words[-1] = words[-1][:-1]
    return ' '.join(words)


# ── Garbage detection ─────────────────────────────────────────────────────────

_DB_TABLE_RE = re.compile(r'^[A-Z][A-Z0-9_]+$')
_KEYWORD_FALLBACK_RE = re.compile(r'^([A-Z][a-z]+ ){2,}[A-Z]')
_TABLE_WORDS = {'fact', 'table', 'column', 'nullable', 'varchar', 'number', 'datatype'}


def _is_garbage_label(label: str) -> bool:
    stripped = label.strip()
    if not stripped:
        return True
    if _DB_TABLE_RE.match(stripped) and '_' in stripped:
        return True
    words = stripped.split()
    if len(words) < config.LABEL_MIN_WORDS:
        return True
    if len(words) >= 6 and _KEYWORD_FALLBACK_RE.match(stripped):
        return True
    if re.match(r'^\d+$', stripped):
        return True
    lower_words = {w.lower() for w in words}
    if len(lower_words & _TABLE_WORDS) >= 2:
        return True
    return False


# ── Complete-linkage clustering ───────────────────────────────────────────────
# Single-linkage (union-find on any pairwise edge >= threshold) lets clusters
# "chain": A merges with B, B merges with C, so A and C end up in one cluster
# even though A and C were never directly similar. Confirmed in practice —
# unrelated labels ("Sequela and Manifestation Coding Guidelines", "Multiple
# Code Sequencing Rules") got merged with genuinely sepsis-specific ones
# purely by transitive chaining through intermediate labels. Complete-linkage
# instead requires EVERY cross-pair between two clusters to clear the
# threshold before merging them, which is the standard fix for this failure
# mode — a merge can no longer happen through a single weak bridging link.

def _complete_linkage_clusters(sim_matrix: np.ndarray, threshold: float) -> List[List[int]]:
    n = sim_matrix.shape[0]
    clusters: List[List[int]] = [[i] for i in range(n)]

    def min_sim(c1: List[int], c2: List[int]) -> float:
        return min(sim_matrix[i][j] for i in c1 for j in c2)

    while True:
        best_pair: Optional[Tuple[int, int]] = None
        best_sim = threshold
        for a in range(len(clusters)):
            for b in range(a + 1, len(clusters)):
                s = min_sim(clusters[a], clusters[b])
                if s >= threshold and (best_pair is None or s > best_sim):
                    best_sim = s
                    best_pair = (a, b)
        if best_pair is None:
            break
        a, b = best_pair
        clusters[a] = clusters[a] + clusters[b]
        del clusters[b]

    return clusters


# ── Canonical label selection ─────────────────────────────────────────────────

def _pick_canonical(
    indices: List[int], unique_labels: List[str], sim_matrix: np.ndarray, garbage_set: Set[str]
) -> str:
    """
    Pick the medoid of the cluster — the label with the highest average
    similarity to every OTHER member — preferring non-garbage candidates.
    This reflects what the cluster is actually about. The previous version
    scored by raw word count, which let a longer, narrower label (e.g.
    "Congenital Sepsis Coding Rules") win over the whole cluster even when
    most members were general (e.g. plain "Sepsis Coding Guidelines").
    """
    candidates = [i for i in indices if unique_labels[i] not in garbage_set] or indices
    if len(candidates) == 1:
        return unique_labels[candidates[0]]

    def avg_sim(i: int) -> float:
        others = [j for j in indices if j != i]
        return sum(sim_matrix[i][j] for j in others) / len(others)

    best = max(candidates, key=avg_sim)
    return unique_labels[best]


# ── Main entry point ─────────────────────────────────────────────────────────

def normalize_labels(chunks: List[Dict]) -> List[Dict]:
    """
    Cluster near-duplicate master_labels and assign canonical labels.
    Modifies chunks in place and returns them.
    """
    unique_labels = list({c.get("master_label", "") for c in chunks if c.get("master_label")})
    if len(unique_labels) < 2:
        log.info("[Label Normalizer] Fewer than 2 unique labels — nothing to normalize.")
        return chunks

    log.info(f"[Label Normalizer] Analyzing {len(unique_labels)} unique labels...")

    # Identify garbage labels
    garbage_set = {l for l in unique_labels if _is_garbage_label(l)}
    if garbage_set:
        log.info(f"[Label Normalizer] Flagged {len(garbage_set)} garbage labels")

    # Compute embeddings on normalized forms
    normalized_forms = [_normalize_for_clustering(l) for l in unique_labels]
    embedder = _get_embedder()
    embeddings = embedder.encode(normalized_forms, batch_size=64, normalize_embeddings=True, show_progress_bar=False)

    # Build similarity matrix and cluster (complete-linkage — see docstring
    # above _complete_linkage_clusters for why not union-find/single-linkage)
    sim_matrix = cosine_similarity(embeddings)
    threshold = config.LABEL_MERGE_THRESHOLD

    cluster_list = _complete_linkage_clusters(sim_matrix, threshold)

    # Build label mapping: old label -> canonical label
    label_map: Dict[str, str] = {}
    n_clusters_merged = 0

    for indices in cluster_list:
        cluster_labels = [unique_labels[i] for i in indices]
        canonical = _pick_canonical(indices, unique_labels, sim_matrix, garbage_set)

        if len(cluster_labels) > 1:
            n_clusters_merged += 1
            log.info(
                f"  Merged cluster ({len(cluster_labels)} labels) → '{canonical}': "
                f"{cluster_labels}"
            )

        for l in cluster_labels:
            label_map[l] = canonical

    # Apply mapping to chunks
    updated = 0
    for c in chunks:
        old = c.get("master_label", "")
        if old in label_map and label_map[old] != old:
            c["master_label"] = label_map[old]
            updated += 1

    new_unique = len({c.get("master_label", "") for c in chunks if c.get("master_label")})
    log.info(
        f"[Label Normalizer] Done. "
        f"Merged {n_clusters_merged} clusters, updated {updated} chunks. "
        f"Unique labels: {len(unique_labels)} → {new_unique}"
    )

    return chunks
