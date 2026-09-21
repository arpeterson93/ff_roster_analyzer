"""Pulls the FAAB training-data files from this repo's "faab-data" GitHub
release rather than expecting them in git - see the conversation this was
built from: combined-training-table.json alone is well past GitHub's 100MB
per-file git push limit (and, uncompressed, past even the 2GB per-asset
Release limit - see the gzip note below), and the three raw pulled-data
files (real bid/roster history scraped from ~30 *other* people's public
ESPN leagues) can never be regenerated if lost, so they need a durable home
even though none of them belong in the git history itself.

Release assets on a public repo are plain, unauthenticated HTTPS downloads -
no `gh` CLI, no token, no rate-limit concerns worth worrying about here -
so this only needs `requests`, already a project dependency.

Every asset on the release is gzip-compressed (`<filename>.gz`, not the raw
filename) - see publish_release_data.py. JSON this repetitive (thousands of
rows sharing the same field names) compresses to roughly 5-7% of its raw
size, which is what actually keeps combined-training-table.json under
GitHub's 2GB-per-asset Release limit as the pooled dataset keeps growing
(2.7GB raw, ~180MB compressed, confirmed live 2026-09-18 - the raw upload
itself was flatly rejected with a 422 before this existed). Downloaded here
as the `.gz`, verified against the pinned hash of THAT compressed asset, then
decompressed to the plain filename every other script in this repo expects.

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
    "combined-training-table.json": "b61128e30bc935c9807c3358cc2674505ee645d630082a50e6cf5ca4411c5e18",
    "other-leagues-bids-raw.json": "8cb16f66b625fa3ef684e608aeb8241ad90f4512e8e1f86f6e3cb3241e23a1e2",
    "other-leagues-bids.json": "a0d07aa6f0c5663bd7a5ca8c79150b841ee48c7b89156067b8b6cd20e90343d3",
    "other-leagues-rostered-by-week.json": "4827d9d39ca1403b6afc6a1fdacc9412a343ba8e479a47e5b4b6f6ba50b713e7",
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
