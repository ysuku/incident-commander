"""Paths and environment. Everything data-related resolves from the repo root."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RCAEVAL_DIR = DATA_DIR / "rcaeval"          # downloaded by scripts/download_data.py (gitignored)
RUNBOOKS_DIR = DATA_DIR / "runbooks"        # Prometheus Operator runbooks (markdown)
POSTMORTEMS_DIR = DATA_DIR / "postmortems"  # summaries + links only, never copies
OUTPUTS_DIR = REPO_ROOT / "outputs"

# Development set: Online Boutique (12 services). Train Ticket is TEST ONLY —
# never load it while building, so the unseen-system result stays honest.
DEV_SYSTEM = "online-boutique"
HELDOUT_SYSTEM = "train-ticket"
