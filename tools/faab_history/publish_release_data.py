"""Publishes an updated FAAB training-data file to the "faab-data" GitHub
release (see fetch_release_data.py) and updates that script's pinned sha256
in the SAME step - the checksum refresh a bare `gh release upload --clobber`
would otherwise leave as a manual, easy-to-forget follow-up (fetch_release_data.py
deliberately refuses a checksum mismatch, so a forgotten refresh here means
every future fetch of this file starts failing instead of just being stale).

Requires the `gh` CLI installed and authenticated locally - this is a
publish step you run yourself when a file changes, never from CI.

Usage:
    python -m tools.faab_history.publish_release_data combined-training-table.json
    python -m tools.faab_history.publish_release_data other-leagues-bids.json other-leagues-rostered-by-week.json
"""
from __future__ import annotations

import re
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
    print(f"{filename}: hashing...")
    digest = sha256_of_file(path)
    print(f"{filename}: uploading to release '{TAG}' ({path.stat().st_size / 1e6:.0f} MB)...")
    result = subprocess.run(
        ["gh", "release", "upload", TAG, str(path), "--clobber", "--repo", REPO],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh release upload failed for {filename}:\n{result.stderr}")
    _update_pinned_hash(filename, digest)
    print(f"{filename}: done - pinned sha256 in fetch_release_data.py updated to {digest}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for arg in sys.argv[1:]:
        publish(Path(arg).name)  # accepts either a bare filename or a full path
    print("\nDon't forget to commit the updated fetch_release_data.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
