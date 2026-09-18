import gzip
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from tools.faab_history import fetch_release_data as frd


def _gz_response(raw_bytes: bytes) -> MagicMock:
    compressed = gzip.compress(raw_bytes)
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.iter_content = lambda chunk_size=None: (compressed[i : i + 1024] for i in range(0, len(compressed), 1024))
    return resp


def test_fetch_downloads_gz_verifies_hash_and_decompresses(tmp_path, monkeypatch):
    raw = b'{"hello": "world", "rows": [1, 2, 3]}'
    compressed = gzip.compress(raw)
    digest = hashlib.sha256(compressed).hexdigest()

    monkeypatch.setattr(frd, "DEST_DIR", tmp_path)
    monkeypatch.setitem(frd.FILES, "sample.json", digest)

    with patch.object(frd.requests, "get", return_value=_gz_response(raw)):
        frd.fetch("sample.json")

    dest = tmp_path / "sample.json"
    assert dest.exists()
    assert dest.read_bytes() == raw
    # no leftover temp files
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sample.json"]


def test_fetch_rejects_a_hash_mismatch_and_cleans_up(tmp_path, monkeypatch):
    raw = b'{"hello": "world"}'

    monkeypatch.setattr(frd, "DEST_DIR", tmp_path)
    monkeypatch.setitem(frd.FILES, "sample.json", "0" * 64)  # deliberately wrong

    with patch.object(frd.requests, "get", return_value=_gz_response(raw)):
        with pytest.raises(RuntimeError, match="sha256 mismatch"):
            frd.fetch("sample.json")

    assert not (tmp_path / "sample.json").exists()
    assert list(tmp_path.iterdir()) == []


def test_fetch_skips_download_when_already_present(tmp_path, monkeypatch):
    monkeypatch.setattr(frd, "DEST_DIR", tmp_path)
    (tmp_path / "sample.json").write_text("already here")

    with patch.object(frd.requests, "get") as mock_get:
        frd.fetch("sample.json")
    mock_get.assert_not_called()
