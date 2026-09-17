"""Pulls the FAAB training-data files from this repo's "faab-data" GitHub
release rather than expecting them in git - see the conversation this was
built from: combined-training-table.json alone is ~436MB, well over GitHub's
100MB per-file hard limit, and the three raw pulled-data files (real bid/
roster history scraped from ~30 *other* people's public ESPN leagues) can
never be regenerated if lost, so they need a durable home even though none
of them belong in the git history itself.

Release assets on a public repo are plain, unauthenticated HTTPS downloads -
no `gh` CLI, no token, no rate-limit concerns worth worrying about here -
so this only needs `requests`, already a project dependency.

Usage:
    python -m tools.faab_history.fetch_release_data                 # just
        combined-training-table.json - all engine/pipeline.py needs
    python -m tools.faab_history.fetch_release_data --all            # every
        file, including the raw pulled data (only needed to rebuild the
        training table from scratch via build_training_table.py)
    python -m tools.faab_history.fetch_release_data --force          # re-download
        even if the local file already exists
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import requests

REPO = "arpeterson93/ff_roster_analyzer"
TAG = "faab-data"
DEST_DIR = Path(__file__).parent

# sha256 pinned at upload time (2026-09-14) - a checksum mismatch means the
# release asset was replaced (see build_training_table.py's own regeneration
# flow) without this list being updated, not that the download is corrupt;
# update the hash here whenever you `gh release upload faab-data <file>
# --clobber` a new version.
FILES = {
    "combined-training-table.json": "1772e36ffbf5d4860ece50d8465e56a41e4a98c5d9ee8d4b0335f30150cd695b",
    "other-leagues-bids-raw.json": "09d85cd8d7f0cbbd0e86628c13eefc9e896bf42e5dc1ac5402340bf422b2de8e",
    "other-leagues-bids.json": "12d90577cd245465c37974ac8d9473c02a09eeb5dfee1ec92776835da3e59ad3",
    "other-leagues-rostered-by-week.json": "4f9e5b9cbc6894526e4a695640768dc7114bc739ce7a2fb2047f745488d34251",
}

# The one file engine/pipeline.py actually loads at run time (see
# engine.faab_estimate.POOLED_TRAINING_TABLE_PATH) - the other three are
# raw inputs to build_training_table.py, only needed to rebuild it from
# scratch, not for a normal pipeline run.
DEFAULT_FILES = ["combined-training-table.json"]


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(filename: str, force: bool = False) -> None:
    dest = DEST_DIR / filename
    if dest.exists() and not force:
        print(f"  {filename}: already present, skipping (--force to re-download)")
        return
    url = f"https://github.com/{REPO}/releases/download/{TAG}/{filename}"
    print(f"  {filename}: downloading from {url}")
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    digest = sha256_of_file(tmp)
    expected = FILES[filename]
    if digest != expected:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{filename}: sha256 mismatch (got {digest}, expected {expected}) - "
            "the release asset may have been updated without FILES being updated here, "
            "or the download was corrupted. Not installing it."
        )
    tmp.replace(dest)
    print(f"  {filename}: OK ({dest.stat().st_size / 1e6:.0f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="fetch every file, including the raw pulled-data inputs")
    parser.add_argument("--force", action="store_true", help="re-download even if already present locally")
    args = parser.parse_args()

    filenames = list(FILES) if args.all else DEFAULT_FILES
    print(f"Fetching {len(filenames)} file(s) from release '{TAG}'...")
    for filename in filenames:
        fetch(filename, force=args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
