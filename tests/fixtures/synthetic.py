"""Synthetic labels and predictions. Not real posts; never a result."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from abiso.schema import Area, DisruptionType, Event, Label, Status

BASE = datetime.fromisoformat("2026-10-04T08:00:00+08:00")
LABELLED_AT = datetime.fromisoformat("2026-10-05T12:00:00+08:00")


def labels(n: int = 150, seed: int = 1) -> list[Label]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        if i % 3 == 0:
            out.append(Label(post_id=f"post{i:03d}", type=DisruptionType.not_disruption, labelled_at=LABELLED_AT))
            continue
        start = BASE + timedelta(days=rng.randint(0, 20), hours=rng.randint(0, 6))
        out.append(Label(post_id=f"post{i:03d}", type=rng.choice([DisruptionType.power, DisruptionType.water]),
                         status=Status.scheduled, barangays=rng.sample(["Basak", "Pusok", "Pajo", "Agus"], 2),
                         start=start, end=start + timedelta(hours=8), labelled_at=LABELLED_AT))
    return out


def perfect_events(lab: Label) -> list[Event]:
    return [Event(event_id=f"e-{lab.post_id}", type=lab.type, status=lab.status or Status.scheduled,
                  areas=[Area(barangay=b) for b in lab.barangays], start=lab.start, end=lab.end,
                  summary="s", source_post_ids=[lab.post_id], confidence=1, model="perfect")]


def write_jsonl(path: Path, rows) -> Path:
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
    return path


def write_labels(path: Path, labs: list[Label]) -> Path:
    return write_jsonl(path, [l.model_dump_json() for l in labs])


def write_predictions(path: Path, preds: dict[str, list[Event] | None]) -> Path:
    rows = []
    for pid, evs in preds.items():
        if evs is None:
            rows.append({"post_id": pid, "error": "compile failed"})
        else:
            rows.append({"post_id": pid, "events": [json.loads(e.model_dump_json()) for e in evs]})
    return write_jsonl(path, rows)
