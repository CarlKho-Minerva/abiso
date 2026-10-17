import csv
import json

import pytest

from abiso.compile import SYSTEM_PROMPT, ModelOutput
from abiso.schema import DisruptionType, Post
from eval import figures as F
from eval import score as SC
from eval.split import SplitLockError
from finetune.build_dataset import build_rows, label_to_target
from tests.fixtures.barangays import LAPULAPU_BARANGAYS
from tests.fixtures.synthetic import LABELLED_AT, labels, perfect_events


def synthetic_results():
    labs = {l.post_id: l for l in labels(150)}
    ids = sorted(labs)[:50]
    good = {p: perfect_events(labs[p]) for p in ids}
    half = {p: (perfect_events(labs[p]) if i % 2 else []) for i, p in enumerate(ids)}
    res = SC.score(labs, {"gemini": good, "gemma": half, "gemma-lora": good}, ids, n_boot=300)
    res["synthetic"] = True
    return res


def test_fig1_writes_svg_png_and_table(tmp_path):
    paths = F.fig1(synthetic_results(), tmp_path)
    names = {p.name for p in paths}
    assert names == {"fig1_accuracy.svg", "fig1_accuracy.png", "fig1_table.csv"}
    svg = (tmp_path / "fig1_accuracy.svg").read_text()
    assert "% correct on 50 held-out posts" in svg and "SYNTHETIC" in svg and "n = 50" in svg
    rows = list(csv.DictReader((tmp_path / "fig1_table.csv").open()))
    assert len(rows) == 3 * 7 and {r["system"] for r in rows} == {"gemini", "gemma", "gemma-lora"}


def test_colour_follows_system_not_order():
    assert F.assign_slots(["gemma-lora", "gemini"]) == {"gemma-lora": 2, "gemini": 0}
    assert F.assign_slots(["x", "gemma"]) == {"gemma": 1, "x": 0}
    with pytest.raises(ValueError):
        F.assign_slots(["a", "b", "c", "d"])


def test_label_to_target_validates_as_model_output():
    for lab in labels(30):
        out = ModelOutput.model_validate(label_to_target(lab))
        assert out.events[0].type == lab.type
        if lab.type != DisruptionType.not_disruption:
            assert [a.place for a in out.events[0].areas] == lab.barangays


def _posts(ids):
    return {p: Post(post_id=p, page="LLCPIO", url="u", posted_at=LABELLED_AT, captured_at=LABELLED_AT,
                    text="t", capture_method="dom") for p in ids}


def test_build_rows_uses_inference_prompt_and_blocks_test_leak():
    labs = {l.post_id: l for l in labels(60)}
    ids = sorted(labs)
    rows = build_rows(_posts(ids), labs, ids[:10], set(ids[10:]), LAPULAPU_BARANGAYS)
    assert len(rows) == 10 and rows[0]["messages"][0]["content"] == SYSTEM_PROMPT
    assert json.loads(rows[0]["messages"][2]["content"])["events"]
    with pytest.raises(SplitLockError):
        build_rows(_posts(ids), labs, ids[:11], set(ids[10:]), LAPULAPU_BARANGAYS)


def test_train_script_imports_without_gpu_libs():
    import finetune.train_lora as T
    a = T.parse_args(["--data", "d.jsonl", "--out", "o"])
    assert a.base == "google/gemma-4-31B-it" and a.rank == 16
