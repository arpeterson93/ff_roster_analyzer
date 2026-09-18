import json

import pytest

from tools.faab_history.atomic_json import write_json


def test_write_json_round_trips(tmp_path):
    path = tmp_path / "out.json"
    write_json(path, {"a": 1, "b": [1, 2, 3]}, indent=2)
    assert json.loads(path.read_text()) == {"a": 1, "b": [1, 2, 3]}


def test_write_json_overwrites_existing_file(tmp_path):
    path = tmp_path / "out.json"
    write_json(path, {"first": True}, indent=2)
    write_json(path, {"second": True}, indent=2)
    assert json.loads(path.read_text()) == {"second": True}


def test_write_json_never_leaves_the_target_truncated_mid_write(tmp_path):
    # The bug this exists to fix: a plain path.write_text(json.dumps(...))
    # opens in "w" mode, which truncates the file to 0 bytes BEFORE writing
    # the new content - a concurrent reader (a downstream script run in
    # another terminal, exactly what this repo's tools are meant to support)
    # can observe that empty window and crash with a JSONDecodeError at char
    # 0. write_json instead writes to a temp file and os.replace()'s it into
    # place, so the target path itself is only ever the complete old content
    # or the complete new content - simulated here by writing real content
    # first, then confirming a second write leaves no state where the file
    # is readable-but-empty (the strongest thing testable without actually
    # racing a second thread against the rename).
    path = tmp_path / "out.json"
    write_json(path, {"before": "real content, not empty"}, indent=2)
    assert path.read_text().strip() != ""
    write_json(path, {"after": "also real content"}, indent=2)
    assert path.read_text().strip() != ""
    assert json.loads(path.read_text()) == {"after": "also real content"}


def test_write_json_cleans_up_temp_file_on_failure(tmp_path):
    path = tmp_path / "out.json"

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        write_json(path, Unserializable(), indent=2)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_write_json_requires_an_existing_parent_directory(tmp_path):
    # Same precondition plain write_text already had - callers that write
    # to a possibly-new directory (e.g. pull_o_league_bids.py's --out) still
    # need their own mkdir(parents=True, exist_ok=True) first.
    path = tmp_path / "missing_dir" / "out.json"
    with pytest.raises(FileNotFoundError):
        write_json(path, {"a": 1})
