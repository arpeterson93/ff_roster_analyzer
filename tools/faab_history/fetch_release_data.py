"""Pulls the FAAB training-data files from this repo's "faab-data" GitHub
release rather than expecting them in git - see the conversation this was
built from: combined-training-table.parquet alone is well past GitHub's
100MB per-file git push limit, and the three raw pulled-data files (real
bid/roster history scraped from ~30 *other* people's public ESPN leagues)
can never be regenerated if lost, so they need a durable home even though
none of them belong in the git history itself.

Release assets on a public repo are plain, unauthenticated HTTPS downloads -
no `gh` CLI, no token, no rate-limit concerns worth worrying about here -
so this only needs `requests`, already a project dependency.

Every asset on the release is gzip-compressed (`<filename>.gz`, not the raw
filename) - see publish_release_data.py. combined-training-table.parquet's
own columnar compression already keeps it well under GitHub's 2GB-per-asset
Release limit on its own (115MB raw for ~5.6M rows, vs. 2.7GB when this was
still JSON, before it OOM-killed a CI runner loading it - see
build_training_table.py's COMBINED_OUT_PATH comment and engine/
faab_estimate.py's load_pools) - gzip on top is just this script's uniform
handling for every file it fetches, not load-bearing for this one anymore.
Downloaded here as the `.gz`, verified against the pinned hash of THAT
compressed asset, then decompressed to the plain filename every other
script in this repo expects.

Usage:
    python -m tools.faab_history.fetch_release_data                 # just
        combined-training-table.parquet - all engine/pipeline.py needs
    python -m tools.faab_history.fetch_release_data --all            # every
        file, including the raw pulled data (only needed to rebuild the
        training table from scratch via build_training_table.py)
    python -m tools.faab_history.fetch_release_data --force          # re-download
        even if the local file already exists
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import shutil
import sys
from pathlib import Path

import requests

REPO = "arpeterson93/ff_roster_analyzer"
TAG = "faab-data"
DEST_DIR = Path(__file__).parent

# sha256 of the COMPRESSED (.gz) release asset, pinned at upload time - see
# publish_release_data.py, which computes and writes these here in the same
# step it uploads. A checksum mismatch means the release asset was replaced
# without this list being updated, not that the download is corrupt; update
# the hash here whenever you `gh release upload faab-data <file>.gz --clobber`
# a new version by hand (normally you don't - publish_release_data.py does
# both in one step).
FILES = {
    # .parquet, not .json - see build_training_table.py's COMBINED_OUT_PATH
    # comment (the pooled table's row count makes a plain json.loads OOM a
    # CI runner outright; polars loads/reduces a Parquet file columnar).
    "combined-training-table.parquet": "26ad257f621e8a9012526262f7aecaee94789b28a332fadb9bd735da1b2c08ed",
    "other-leagues-bids-raw.json": "2ec2b8ffa150b31754dd7d4d4c6fa395d055b3f0b6c8b3b944ad806d6e3e3bee",
    "other-leagues-bids.json": "c35bc7d550b83b28942d538dfccb96ab372b802e5e479b11a7557952ffc0d6de",
    "other-leagues-rostered-by-week.json": "241c2caad0785115dcaa1e6fa4d399f62da6d3a59e5755e631b93b4026c5ff81",
}

# The one file engine/pipeline.py actually loads at run time (see
# engine.faab_estimate.POOLED_TRAINING_TABLE_PATH) - the other three are
# raw inputs to build_training_table.py, only needed to rebuild it from
# scratch, not for a normal pipeline run.
DEFAULT_FILES = ["combined-training-table.parquet"]


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
    gz_name = f"{filename}.gz"
    url = f"https://github.com/{REPO}/releases/download/{TAG}/{gz_name}"
    print(f"  {filename}: downloading {gz_name} from {url}")
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    gz_tmp = dest.with_name(dest.name + ".gz.part")
    with open(gz_tmp, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    digest = sha256_of_file(gz_tmp)
    expected = FILES[filename]
    if digest != expected:
        gz_tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"{filename}: sha256 mismatch on the compressed asset (got {digest}, expected {expected}) - "
            "the release asset may have been updated without FILES being updated here, "
            "or the download was corrupted. Not installing it."
        )
    print(f"  {filename}: decompressing...")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with gzip.open(gz_tmp, "rb") as f_in, open(tmp, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    gz_tmp.unlink()
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
