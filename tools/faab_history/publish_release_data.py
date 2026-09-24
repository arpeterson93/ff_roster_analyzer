"""Publishes an updated FAAB training-data file to the "faab-data" GitHub
release (see fetch_release_data.py) and updates that script's pinned sha256
in the SAME step - the checksum refresh a bare `gh release upload --clobber`
would otherwise leave as a manual, easy-to-forget follow-up (fetch_release_data.py
deliberately refuses a checksum mismatch, so a forgotten refresh here means
every future fetch of this file starts failing instead of just being stale).

Gzip-compresses the file before uploading (as `<filename>.gz`, not the raw
filename) rather than uploading it as-is - GitHub Releases hard-caps a
single asset at 2GB, and combined-training-table.json (this file's format
before it moved to Parquet - see build_training_table.py's COMBINED_OUT_PATH
comment) alone crossed that raw (2.7GB, confirmed live 2026-09-18 - a flat
422 rejection, not a slow failure) as the pooled multi-league dataset kept
growing. Repetitive JSON like that (thousands of rows sharing the same field
names) compresses to roughly 5-7% of its raw size via gzip alone - enormous
headroom, no need to split into parts. combined-training-table.parquet's own
columnar compression already keeps it small on its own (115MB raw for the
same ~5.6M rows that were 2.7GB as JSON), but this script stays generic
(gzip-wraps whatever file it's given) rather than special-casing one
filename, and it still shrinks every daily pipeline run's download further,
since fetch_release_data.py decompresses on its end regardless of format.

Requires the `gh` CLI installed and authenticated locally - this is a
publish step you run yourself when a file changes, never from CI.

Usage:
    python -m tools.faab_history.publish_release_data combined-training-table.parquet
    python -m tools.faab_history.publish_release_data other-leagues-bids.json other-leagues-rostered-by-week.json
"""
from __future__ import annotations

import gzip
import re
import shutil
import subprocess
import sys
from pathlib import Path

from tools.faab_history.fetch_release_data import DEST_DIR, REPO, TAG, sha256_of_file

_FETCH_SCRIPT = Path(__file__).parent / "fetch_release_data.py"


def _update_pinned_hash(filename: str, new_hash: str) -> None:
    text = _FETCH_SCRIPT.read_text(encoding="utf-8")
    pattern = re.compile(rf'("{re.escape(filename)}"):\s*"[0-9a-f]{{64}}"')
    new_text, n = pattern.subn(rf'\1: "{new_hash}"', text)
    if n == 0:
        # A brand-new file, not yet in FILES - add it rather than failing, so
        # a first-time publish doesn't also need a separate manual edit.
        marker = "FILES = {\n"
        idx = text.index(marker) + len(marker)
        new_text = text[:idx] + f'    "{filename}": "{new_hash}",\n' + text[idx:]
        print(f"  {filename}: not previously in FILES - added it")
    elif n > 1:
        raise RuntimeError(f"expected exactly one FILES entry for {filename!r}, found {n}")
    _FETCH_SCRIPT.write_text(new_text, encoding="utf-8")


def publish(filename: str) -> None:
    path = DEST_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist locally - nothing to publish")
    gz_path = DEST_DIR / f"{filename}.gz"
    try:
        print(f"{filename}: compressing...")
        with open(path, "rb") as f_in, gzip.open(gz_path, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        raw_mb = path.stat().st_size / 1e6
        gz_mb = gz_path.stat().st_size / 1e6
        print(f"{filename}: hashing compressed asset...")
        digest = sha256_of_file(gz_path)
        print(f"{filename}: uploading {gz_path.name} to release '{TAG}' ({gz_mb:.0f} MB, compressed from {raw_mb:.0f} MB)...")
        result = subprocess.run(
            ["gh", "release", "upload", TAG, str(gz_path), "--clobber", "--repo", REPO],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh release upload failed for {gz_path.name}:\n{result.stderr}")
    finally:
        gz_path.unlink(missing_ok=True)
    _update_pinned_hash(filename, digest)
    print(f"{filename}: done - pinned sha256 (of the compressed asset) in fetch_release_data.py updated to {digest}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for arg in sys.argv[1:]:
        publish(Path(arg).name)  # accepts either a bare filename or a full path
    print("\nDon't forget to commit the updated fetch_release_data.py.")
    print(
        "If this replaced a previously-uncompressed asset (published before this gzip step existed), "
        "the old un-suffixed asset is still sitting on the release, unused - delete it by hand "
        "(gh release view faab-data --repo " + REPO + ", or the GitHub web UI) once you've confirmed the new one works."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
