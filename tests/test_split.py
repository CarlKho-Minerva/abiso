import json

import pytest

from eval import split as S
from tests.fixtures.synthetic import labels, write_labels


@pytest.fixture
def paths(tmp_path):
    lab = write_labels(tmp_path / "labels.jsonl", labels(150))
    return lab, tmp_path / "split.json", tmp_path / "SPLIT_LOCK"


def test_make_split_is_seeded_disjoint_and_complete():
    ids = [f"p{i}" for i in range(150)]
    a, b = S.make_split(ids, seed=7), S.make_split(list(reversed(ids)), seed=7)
    assert a == b                                  # input order does not matter
    assert len(a["test"]) == 50 and len(a["train"]) == 100
    assert not set(a["test"]) & set(a["train"]) and set(a["test"]) | set(a["train"]) == set(ids)
    assert S.make_split(ids, seed=8)["test"] != a["test"]


def test_too_few_labels():
    with pytest.raises(ValueError):
        S.make_split([f"p{i}" for i in range(50)])


def test_write_then_lock_matches(paths):
    lab, out, lock = paths
    split = S.write_split(lab, out, lock, seed=1, n_test=50)
    assert S.read_lock(lock) == S.sha256_file(out)
    assert S.verify_lock(out, lock)["test"] == split["test"]
    assert json.loads(out.read_text())["labels_sha256"] == S.sha256_file(lab)


def test_refuses_to_resplit(paths, capsys):
    lab, out, lock = paths
    assert S.main(["--labels", str(lab), "--out", str(out), "--lock", str(lock)]) == 0
    before = out.read_bytes()
    rc = S.main(["--labels", str(lab), "--out", str(out), "--lock", str(lock), "--seed", "99"])
    assert rc == 3 and "REFUSING TO RE-SPLIT" in capsys.readouterr().err
    assert out.read_bytes() == before


def test_refuses_split_file_without_lock(paths):
    lab, out, lock = paths
    out.write_text("{}")
    with pytest.raises(S.SplitLockError):
        S.write_split(lab, out, lock, 1, 50)


def test_edit_after_lock_is_detected(paths):
    lab, out, lock = paths
    S.write_split(lab, out, lock, 1, 50)
    d = json.loads(out.read_text())
    d["test"][0], d["train"][0] = d["train"][0], d["test"][0]
    out.write_text(json.dumps(d))
    with pytest.raises(S.SplitLockError, match="does not match"):
        S.verify_lock(out, lock)


def test_missing_lock_or_split(tmp_path):
    with pytest.raises(S.SplitLockError):
        S.verify_lock(tmp_path / "split.json", tmp_path / "LOCK")
    (tmp_path / "LOCK").write_text("abc  x\n")
    with pytest.raises(S.SplitLockError):
        S.verify_lock(tmp_path / "split.json", tmp_path / "LOCK")


def test_bad_label_line_is_loud(tmp_path):
    f = tmp_path / "labels.jsonl"
    f.write_text('{"post_id": "a"}\nnot json\n')
    with pytest.raises(ValueError, match=":2:"):
        S.labelled_ids(f)
