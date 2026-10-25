"""Download the public datasets. The repo ships this script, never the data.

Usage:
  python scripts/download_data.py --list                 # show what's in the Zenodo record
  python scripts/download_data.py --match "RE2-OB"       # download files whose name matches
  python scripts/download_data.py --runbooks             # clone Prometheus Operator runbooks

RCAEval (735 recorded failures, labeled root causes): Zenodo record 14504480.
Start with Online Boutique (RE2 has logs+metrics+traces); Train Ticket is the
held-out test system — download it, but never open it while building.
"""
import argparse
import subprocess
import sys
from pathlib import Path

import requests

ZENODO_RECORD = "14504480"
ZENODO_API = f"https://zenodo.org/api/records/{ZENODO_RECORD}"
DATA = Path(__file__).resolve().parents[1] / "data"
RUNBOOKS_REPO = "https://github.com/prometheus-operator/runbooks.git"


def zenodo_files() -> list[dict]:
    r = requests.get(ZENODO_API, timeout=30)
    r.raise_for_status()
    return r.json().get("files", [])


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    print(f"  -> {dest}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true", help="List files in the Zenodo record")
    p.add_argument("--match", help="Download Zenodo files whose name contains this substring")
    p.add_argument("--runbooks", action="store_true", help="Clone the Prometheus Operator runbooks")
    args = p.parse_args()

    if args.runbooks:
        dest = DATA / "runbooks" / "repo"
        if dest.exists():
            print(f"{dest} already exists; git -C {dest} pull to update")
        else:
            subprocess.run(["git", "clone", "--depth", "1", RUNBOOKS_REPO, str(dest)], check=True)
        return

    files = zenodo_files()
    if args.list or not args.match:
        print(f"Zenodo record {ZENODO_RECORD}:")
        for f in files:
            print(f"  {f['key']:50s} {human(f['size'])}")
        if not args.match:
            print("\nRe-run with --match <substring> to download.")
        return

    picked = [f for f in files if args.match.lower() in f["key"].lower()]
    if not picked:
        sys.exit(f"No files match {args.match!r}; run with --list to see names.")
    for f in picked:
        print(f"Downloading {f['key']} ({human(f['size'])})")
        download(f["links"]["self"], DATA / "rcaeval" / f["key"])


if __name__ == "__main__":
    main()
