"""Crash-safe JSON writes for the tools/faab_history scripts.

Several of these (discover_public_leagues.py chief among them) rewrite
their own output file repeatedly over a long run so progress survives an
interruption - but a plain `path.write_text(json.dumps(...))` opens the
file in "w" mode, which TRUNCATES it to 0 bytes before the new content is
written. Any OTHER process reading that same file in that narrow window - a
downstream step like vet_candidates.py, run concurrently in another
terminal, which is exactly what the README's own "let it run in your own
terminal" instructions invite while a long discover/pull script is still
going - sees an empty file and crashes with a JSONDecodeError at char 0
("Expecting value: line 1 column 1"). Confirmed live 2026-09-18: exactly
this crash, reading discovered_leagues.json while discover_public_leagues.py
was still mid-run rewriting it.

write_json eliminates that window: it writes to a temp file in the SAME
directory, then os.replace()'s it into place - atomic on both POSIX and
Windows, so a concurrent reader always sees either the complete old file or
the complete new one, never a partial write.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_json(path: Path, data, **dumps_kwargs) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **dumps_kwargs)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise
