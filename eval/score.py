"""Score compiled Events against Carl's Labels, per field, with bootstrap 95% CIs.

    uv run python -m eval.score --split test \
        --system gemini=data/predictions/gemini.jsonl \
        --system gemma=data/predictions/gemma.jsonl \
        --system gemma-lora=data/predictions/gemma-lora.jsonl

Prediction files: one JSON object per line, {"post_id": ..., "events": [Event, ...]}
or {"post_id": ..., "error": "..."} when compile failed. A post with no line or
an error counts as wrong on every field (never skipped), and is reported.

Scoring the test split requires eval/SPLIT_LOCK to match data/labels/split.json.

Per-post reduction (a post can compile to several Events; a Label is one row):
  type      = most common non-not_disruption type (ties: first seen);
              not_disruption if there is none
  barangays = union of canonical barangays over events of that type
  start/end = earliest start / latest end over events of that type
  status    = status of the first event of that type
Fields and the posts each is scored on (fixed by the Label, so every system
is scored on the same posts and the bootstrap can be paired):
  type                       all posts
  barangays_exact, _jaccard  posts labelled as a disruption
  start/end exact, +-1 h     posts labelled as a disruption (None == None is correct)
  status                     posts whose label has a status

CIs: percentile bootstrap, 10,000 resamples of posts, seeded; the same resampled
post indices are used for every system, so paired differences between systems
get their own CI. Read differences from `paired_differences`, never from
whether two systems' intervals overlap.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np

from abiso.schema import DisruptionType, Event, Label, Status
from eval.split import DEFAULT_LOCK, DEFAULT_SPLIT, SplitLockError, verify_lock

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = REPO / "data" / "labels" / "labels.jsonl"
DEFAULT_OUT = REPO / "eval" / "results.json"
N_BOOT = 10_000
SEED = 20261003
MANILA = ZoneInfo("Asia/Manila")
TOLERANCE = timedelta(hours=1)

FIELDS = ["type", "barangays_exact", "barangays_jaccard", "start_exact", "start_1h",
          "end_exact", "end_1h", "status"]
PROPORTION_FIELDS = [f for f in FIELDS if f != "barangays_jaccard"]


class Prediction:
    """One post's events reduced to the shape of a Label."""

    def __init__(self, type: Optional[DisruptionType], barangays: set[str],
                 start: Optional[datetime], end: Optional[datetime],
                 status: Optional[Status], missing: bool = False):
        self.type, self.barangays, self.start, self.end = type, barangays, start, end
        self.status, self.missing = status, missing

    @classmethod
    def missing_(cls) -> "Prediction":
        return cls(None, set(), None, None, None, missing=True)

    @classmethod
    def from_events(cls, events: list[Event]) -> "Prediction":
        real = [e for e in events if e.type != DisruptionType.not_disruption]
        if not real:
            return cls(DisruptionType.not_disruption, set(), None, None, None)
        counts = Counter(e.type for e in real)
        top = max(counts.values())
        t = next(e.type for e in real if counts[e.type] == top)
        mine = [e for e in real if e.type == t]
        starts = [_aware(e.start) for e in mine if e.start]
        ends = [_aware(e.end) for e in mine if e.end]
        return cls(t, {a.barangay for e in mine for a in e.areas if a.barangay},
                   min(starts) if starts else None, max(ends) if ends else None, mine[0].status)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=MANILA) if dt.tzinfo is None else dt


def time_score(pred: Optional[datetime], gold: Optional[datetime], tol: timedelta, missing: bool) -> float:
    if missing:
        return 0.0
    pred, gold = _aware(pred), _aware(gold)
    if pred is None or gold is None:
        return float(pred is None and gold is None)
    return float(abs(pred - gold) <= tol)


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def field_scores(pred: Prediction, gold: Label) -> dict[str, Optional[float]]:
    """Score one post. None = this field does not apply to this post."""
    m = pred.missing
    disruption = gold.type != DisruptionType.not_disruption
    gold_b = set(gold.barangays)
    s: dict[str, Optional[float]] = {"type": float(not m and pred.type == gold.type)}
    if disruption:
        s["barangays_exact"] = float(not m and pred.barangays == gold_b)
        s["barangays_jaccard"] = 0.0 if m else jaccard(pred.barangays, gold_b)
        s["start_exact"] = time_score(pred.start, gold.start, timedelta(0), m)
        s["start_1h"] = time_score(pred.start, gold.start, TOLERANCE, m)
        s["end_exact"] = time_score(pred.end, gold.end, timedelta(0), m)
        s["end_1h"] = time_score(pred.end, gold.end, TOLERANCE, m)
    else:
        for f in FIELDS[1:7]:
            s[f] = None
    s["status"] = None if gold.status is None else float(not m and pred.status == gold.status)
    return s


def bootstrap_means(matrix: np.ndarray, n_boot: int, rng: np.random.Generator) -> np.ndarray:
    """matrix: (n_systems, n_posts). Returns (n_systems, n_boot) resampled means,
    using the SAME resampled posts for every system (paired)."""
    n = matrix.shape[1]
    idx = rng.integers(0, n, size=(n_boot, n))
    return matrix[:, idx].mean(axis=2)


def _ci(samples: np.ndarray) -> list[float]:
    lo, hi = np.percentile(samples, [2.5, 97.5])
    return [float(lo), float(hi)]


def load_labels(path: Path) -> dict[str, Label]:
    out: dict[str, Label] = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            lab = Label.model_validate_json(line)
            prev = out.get(lab.post_id)
            if prev is None or lab.labelled_at >= prev.labelled_at:   # latest label wins
                out[lab.post_id] = lab
    return out


def load_predictions(path: Path) -> dict[str, Optional[list[Event]]]:
    out: dict[str, Optional[list[Event]]] = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("error") is not None or "events" not in row:
            out[row["post_id"]] = None
        else:
            out[row["post_id"]] = [Event.model_validate(e) for e in row["events"]]
    return out


def score(labels: dict[str, Label], predictions: dict[str, dict[str, Optional[list[Event]]]],
          post_ids: list[str], n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    systems = list(predictions)
    per_system_scores: dict[str, list[dict]] = {}
    missing: dict[str, list[str]] = {}
    for sysname, preds in predictions.items():
        rows, miss = [], []
        for pid in post_ids:
            evs = preds.get(pid)
            if evs is None:
                miss.append(pid)
                p = Prediction.missing_()
            else:
                p = Prediction.from_events(evs)
            rows.append(field_scores(p, labels[pid]))
        per_system_scores[sysname] = rows
        missing[sysname] = miss

    rng = np.random.default_rng(seed)
    result_fields: dict[str, dict[str, dict]] = {s: {} for s in systems}
    diffs: dict[str, dict[str, dict]] = {}
    for f in FIELDS:
        applicable = [i for i, pid in enumerate(post_ids)
                      if per_system_scores[systems[0]][i][f] is not None] if systems else []
        n = len(applicable)
        if n == 0:
            for s in systems:
                result_fields[s][f] = {"n": 0, "point": None, "ci95": None}
            continue
        mat = np.array([[per_system_scores[s][i][f] for i in applicable] for s in systems], dtype=float)
        boots = bootstrap_means(mat, n_boot, rng)
        for k, s in enumerate(systems):
            result_fields[s][f] = {"n": n, "point": float(mat[k].mean()), "ci95": _ci(boots[k])}
        for a, b in combinations(range(len(systems)), 2):
            d = boots[a] - boots[b]
            ci = _ci(d)
            diffs.setdefault(f"{systems[a]} - {systems[b]}", {})[f] = {
                "n": n, "point": float(mat[a].mean() - mat[b].mean()), "ci95": ci,
                "excludes_zero": bool(ci[0] > 0 or ci[1] < 0)}

    return {
        "n_posts": len(post_ids),
        "n_boot": n_boot,
        "seed": seed,
        "ci_method": "percentile bootstrap over posts, paired across systems",
        "systems": {s: {"n_missing": len(missing[s]), "missing_post_ids": missing[s],
                        "fields": result_fields[s]} for s in systems},
        "paired_differences": diffs,
        "note": ("n=50 gives 95% CIs of roughly +-13 points on a proportion near 0.7. "
                 "Do not read a difference from overlapping or non-overlapping per-system CIs; "
                 "use paired_differences. A difference whose CI excludes zero can still be "
                 "too small to matter for a resident; judge that separately."),
    }


def select_posts(split_name: str, labels: dict[str, Label], split_path: Path, lock: Path) -> list[str]:
    if split_name == "test":
        split = verify_lock(split_path, lock)          # raises on any mismatch
        ids = split["test"]
    elif split_name == "train":
        ids = json.loads(Path(split_path).read_text())["train"]
    else:
        raise ValueError(f"unknown split {split_name!r}")
    missing = [i for i in ids if i not in labels]
    if missing:
        raise ValueError(f"{len(missing)} {split_name} post ids have no label, e.g. {missing[:3]}")
    return list(ids)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Score predictions against labels")
    ap.add_argument("--split", choices=["test", "train"], required=True)
    ap.add_argument("--system", action="append", required=True, metavar="NAME=PATH")
    ap.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    ap.add_argument("--split-file", type=Path, default=DEFAULT_SPLIT)
    ap.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    ap.add_argument("--seed", type=int, default=SEED)
    a = ap.parse_args(argv)

    labels = load_labels(a.labels)
    try:
        ids = select_posts(a.split, labels, a.split_file, a.lock)
    except SplitLockError as e:
        print(f"REFUSING TO SCORE: {e}", file=sys.stderr)
        return 3
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    preds = {}
    for spec in a.system:
        name, _, path = spec.partition("=")
        if not path:
            print(f"ERROR: --system needs NAME=PATH, got {spec!r}", file=sys.stderr)
            return 2
        preds[name] = load_predictions(Path(path))
    res = score(labels, preds, ids, a.n_boot, a.seed)
    res["split"] = a.split
    if a.split == "test":
        from eval.split import read_lock
        res["split_sha256"] = read_lock(a.lock)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=1) + "\n")
    for s, v in res["systems"].items():
        if v["n_missing"]:
            print(f"WARNING: {s}: {v['n_missing']} posts missing or failed, scored as wrong", file=sys.stderr)
    print(f"wrote {a.out} (n={res['n_posts']}, {len(preds)} systems)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
