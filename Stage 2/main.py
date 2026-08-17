"""
Stage 2/main.py
────────────────
Thin orchestrator for a single patient report run. Reuses Stage 1's own
ingest -> rectify -> label -> enrich -> filter -> grouper -> labeling ->
registry -> nested-taxonomy pipeline verbatim (stage1_main.run(skip_profiling
=True)) instead of duplicating any of that code — only the env vars pointing
it at a different PDF/output directory, and the skipped profiling step, are
new.

Context profiling is skipped here on purpose: Stage 2 must never build its
own domain profile from a patient's PDFs (a single patient report is not a
representative sample of the domain, and per the design, EVERY patient run
should reuse the SAME guideline-KB profile). STAGE2_FORCED_PROFILE_PATH
(set by stage2_config.setup_patient_env() below) points
context_profiler.get_profile() (Stage 1/context_profiler.py) at that shared
profile for the whole run.

Usage:
    cd "Stage 2"
    STAGE2_PATIENT_ID=patient_123 python main.py
    python run_tail.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import stage2_config as s2cfg  # noqa: E402

s2cfg.setup_patient_env(os.environ.get("STAGE2_PATIENT_ID", "default"))
s2cfg.put_stage1_first_on_path()

import main as stage1_main  # noqa: E402  (resolves to Stage 1/main.py — see put_stage1_first_on_path)


def run() -> None:
    stage1_main.run(skip_profiling=True)


if __name__ == "__main__":
    run()
