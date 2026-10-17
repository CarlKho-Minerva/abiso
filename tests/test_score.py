import json
from datetime import timedelta

import numpy as np
import pytest

from abiso.schema import Area, DisruptionType, Event, Label, Status
from eval import score as SC
from eval import split as S
from tests.fixtures.synthetic import LABELLED_AT, labels, perfect_events, write_labels, write_predictions


def lab(**kw):
    base = dict(post_id="x", type=DisruptionType.power, status=Status.scheduled, barangays=["Basak", "Pusok"],
                start=LABELLED_AT, end=LABELLED_AT + timedelta(hours=8), labelled_at=LABELLED_AT)
    base.update(kw)
    return Label(**base)


def evt(type="power", barangays=("Basak", "Pusok"), start=LABELLED_AT, end=None, status="scheduled"):
    return Event(event_id="e", type=DisruptionType(type), status=Status(status),
                 areas=[Area(barangay=b) for b in barangays], start=start,
                 end=end if end is not None else (start + timedelta(hours=8) if start else None),
                 summary="s", source_post_ids=["x"], confidence=1, model="m")


# ---------------------------------------------------------------- per-post scoring
def test_perfect_post_scores_one_everywhere():
    s = SC.field_scores(SC.Prediction.from_events([evt()]), lab())
    assert all(v == 1.0 for v in s.values())


def test_time_tolerance():
    p = SC.Prediction.from_events([evt(start=LABELLED_AT + timedelta(minutes=30))])
    s = SC.field_scores(p, lab())
    assert s["start_exact"] == 0 and s["start_1h"] == 1
    p2 = SC.Prediction.from_events([evt(start=LABELLED_AT + timedelta(minutes=61))])
    assert SC.field_scores(p2, lab())["start_1h"] == 0


def test_none_equals_none_but_not_a_value():
    s = SC.field_scores(SC.Prediction.from_events([evt(start=None, end=None)]), lab(start=None, end=None))
    assert s["start_exact"] == 1 and s["end_1h"] == 1
    s2 = SC.field_scores(SC.Prediction.from_events([evt(start=None, end=None)]), lab())
    assert s2["start_exact"] == 0 and s2["start_1h"] == 0


def test_barangay_set_exact_and_jaccard():
    s = SC.field_scores(SC.Prediction.from_events([evt(barangays=("Basak", "Pajo"))]), lab())
    assert s["barangays_exact"] == 0 and s["barangays_jaccard"] == pytest.approx(1 / 3)
    city = SC.field_scores(SC.Prediction.from_events([evt(barangays=())]), lab(barangays=[]))
    assert city["barangays_exact"] == 1 and city["barangays_jaccard"] == 1


def test_not_disruption_label_only_scores_type():
    s = SC.field_scores(SC.Prediction.from_events([]), lab(type=DisruptionType.not_disruption, status=None))
    assert s["type"] == 1 and all(s[f] is None for f in SC.FIELDS[1:])


def test_missing_prediction_is_wrong_everywhere():
    s = SC.field_scores(SC.Prediction.missing_(), lab(start=None, end=None, barangays=[]))
    assert all(v == 0 for v in s.values())


def test_reduction_over_multiple_events():
    evs = [evt(barangays=("Pusok",), start=LABELLED_AT + timedelta(hours=5), end=LABELLED_AT + timedelta(hours=8)),
           evt(barangays=("Basak",), start=LABELLED_AT, end=LABELLED_AT + timedelta(hours=3)),
           evt(type="water", barangays=("Pajo",)),
           evt(type="not_disruption", barangays=(), start=None)]
    p = SC.Prediction.from_events(evs)
    assert p.type == DisruptionType.power and p.barangays == {"Basak", "Pusok"}
    assert p.start == LABELLED_AT and p.end == LABELLED_AT + timedelta(hours=8)
    assert SC.Prediction.from_events([evt(type="not_disruption", barangays=(), start=None)]).type \
        == DisruptionType.not_disruption


# ---------------------------------------------------------------- aggregate + bootstrap
def _setup(n=150):
    labs = {l.post_id: l for l in labels(n)}
    return labs, sorted(labs)[:50]


def test_perfect_system_ci_collapses_and_identical_systems_differ_by_zero():
    labs, ids = _setup()
    preds = {pid: perfect_events(labs[pid]) for pid in ids}
    r = SC.score(labs, {"a": preds, "b": preds}, ids, n_boot=500)
    for f, cell in r["systems"]["a"]["fields"].items():
        assert cell["point"] == 1.0 and cell["ci95"] == [1.0, 1.0], f
    d = r["paired_differences"]["a - b"]["type"]
    assert d["point"] == 0 and d["ci95"] == [0.0, 0.0] and not d["excludes_zero"]


def test_n_per_field_follows_labels():
    labs, ids = _setup()
    r = SC.score(labs, {"a": {pid: perfect_events(labs[pid]) for pid in ids}}, ids, n_boot=100)
    n_dis = sum(labs[p].type != DisruptionType.not_disruption for p in ids)
    f = r["systems"]["a"]["fields"]
    assert f["type"]["n"] == 50 and f["start_exact"]["n"] == n_dis and f["status"]["n"] == n_dis


def test_missing_and_failed_posts_are_counted_not_skipped():
    labs, ids = _setup()
    preds = {pid: perfect_events(labs[pid]) for pid in ids[5:]}
    preds[ids[5]] = None                      # an error row
    r = SC.score(labs, {"a": preds}, ids, n_boot=100)
    assert r["systems"]["a"]["n_missing"] == 6
    assert r["systems"]["a"]["fields"]["type"]["point"] == pytest.approx(44 / 50)


def test_bootstrap_ci_width_matches_the_13_point_claim():
    # p = 0.7, n = 50: normal approx half-width 1.96*sqrt(.21/50) = 0.127
    x = np.array([[1.0] * 35 + [0.0] * 15])
    b = SC.bootstrap_means(x, 10_000, np.random.default_rng(0))
    lo, hi = np.percentile(b[0], [2.5, 97.5])
    assert 0.10 < (hi - lo) / 2 < 0.15


def test_bootstrap_is_paired():
    x = np.array([[1.0, 0.0] * 25, [1.0, 0.0] * 25])
    b = SC.bootstrap_means(x, 200, np.random.default_rng(1))
    assert np.array_equal(b[0], b[1])


def test_seeded_and_deterministic():
    labs, ids = _setup()
    preds = {pid: (perfect_events(labs[pid]) if i % 4 else []) for i, pid in enumerate(ids)}
    assert SC.score(labs, {"a": preds}, ids, n_boot=300) == SC.score(labs, {"a": preds}, ids, n_boot=300)


# ---------------------------------------------------------------- CLI + lock
@pytest.fixture
def repo(tmp_path):
    labs = labels(150)
    lf = write_labels(tmp_path / "labels.jsonl", labs)
    sp, lock = tmp_path / "split.json", tmp_path / "SPLIT_LOCK"
    split = S.write_split(lf, sp, lock, 1, 50)
    by_id = {l.post_id: l for l in labs}
    pf = write_predictions(tmp_path / "gemma.jsonl", {p: perfect_events(by_id[p]) for p in split["test"]})
    return tmp_path, lf, sp, lock, pf


def _argv(lf, sp, lock, pf, out, split="test"):
    return ["--split", split, "--system", f"gemma={pf}", "--labels", str(lf), "--split-file", str(sp),
            "--lock", str(lock), "--out", str(out), "--n-boot", "200"]


def test_cli_scores_test_split_when_lock_matches(repo):
    d, lf, sp, lock, pf = repo
    out = d / "results.json"
    assert SC.main(_argv(lf, sp, lock, pf, out)) == 0
    res = json.loads(out.read_text())
    assert res["split"] == "test" and res["n_posts"] == 50 and res["split_sha256"] == S.read_lock(lock)
    assert res["systems"]["gemma"]["fields"]["type"]["point"] == 1.0


def test_cli_refuses_test_split_when_lock_mismatches(repo, capsys):
    d, lf, sp, lock, pf = repo
    lock.write_text("0" * 64 + "  split.json\n")
    out = d / "results.json"
    assert SC.main(_argv(lf, sp, lock, pf, out)) == 3
    assert "REFUSING TO SCORE" in capsys.readouterr().err and not out.exists()


def test_cli_refuses_without_lock(repo):
    d, lf, sp, lock, pf = repo
    lock.unlink()
    assert SC.main(_argv(lf, sp, lock, pf, d / "r.json")) == 3


def test_cli_train_split_does_not_need_lock_but_needs_labels(repo):
    d, lf, sp, lock, pf = repo
    lock.unlink()
    assert SC.main(_argv(lf, sp, lock, pf, d / "r.json", split="train")) == 0
