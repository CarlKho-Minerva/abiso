"""Build the LoRA training set from the TRAIN split only.

    uv run python -m finetune.build_dataset   # -> data/finetune/train.jsonl

Each row is one chat: the exact system prompt and user text compile.py sends
at inference, plus the image paths, and the assistant turn is the Label
rendered as the ModelOutput JSON compile.py expects back. Same prompt in
training and inference, so the fine-tune learns the task, not a new format.

Guard rails: refuses unless the split lock verifies, and asserts that no test
post id reaches the output (spec section 7: the locked 50 are never trained on).

Known gap: a Label is one row per post, so a post listing several areas at
different times trains to ONE event with all its barangays and the overall
window, while the prompt asks for one event per window. Fine for type,
barangay and status; it under-teaches splitting. Revisit if Carl's labels
grow per-event rows.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from abiso.compile import SYSTEM_PROMPT, build_user_text, to_manila
from abiso.schema import DisruptionType, Label, Post, Status
from eval.score import load_labels
from eval.split import DEFAULT_LOCK, DEFAULT_SPLIT, SplitLockError, verify_lock

REPO = Path(__file__).resolve().parent.parent
DEFAULT_POSTS = REPO / "data" / "raw" / "posts.jsonl"
DEFAULT_LABELS = REPO / "data" / "labels" / "labels.jsonl"
DEFAULT_OUT = REPO / "data" / "finetune" / "train.jsonl"


def _fmt(dt) -> str:
    return to_manila(dt).strftime("%Y-%m-%d %H:%M")


def label_summary(lab: Label) -> str:
    if lab.type == DisruptionType.not_disruption:
        return "Not a disruption notice."
    where = ", ".join(lab.barangays) if lab.barangays else "whole city"
    when = ""
    if lab.start and lab.all_day:
        when = f", all day {to_manila(lab.start).date().isoformat()}"
    elif lab.start:
        when = f", {_fmt(lab.start)}" + (f" to {_fmt(lab.end)}" if lab.end else "")
    return f"{lab.type.value.replace('_', ' ')}: {where}{when}"


def label_to_target(lab: Label) -> dict:
    """Label -> the ModelOutput JSON the compiler must produce."""
    disruption = lab.type != DisruptionType.not_disruption
    return {"events": [{
        "type": lab.type.value,
        "status": (lab.status or Status.scheduled).value,
        "areas": [{"place": b, "detail": None} for b in lab.barangays] if disruption else [],
        "start": to_manila(lab.start).isoformat() if (disruption and lab.start) else None,
        "end": to_manila(lab.end).isoformat() if (disruption and lab.end) else None,
        "all_day": lab.all_day if disruption else False,
        "summary": label_summary(lab),
        "confidence": 1.0,
    }]}


def build_rows(posts: dict[str, Post], labels: dict[str, Label], train_ids: list[str],
               test_ids: set[str], barangays: list[str]) -> list[dict]:
    leak = set(train_ids) & test_ids
    if leak:
        raise SplitLockError(f"{len(leak)} test ids in the training list, e.g. {sorted(leak)[:3]}")
    rows = []
    for pid in train_ids:
        if pid not in posts:
            raise KeyError(f"train post {pid} has a label but no captured post")
        post = posts[pid]
        images = [m.path for m in post.media if m.kind == "image" and m.path]
        rows.append({
            "post_id": pid,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_text(post, barangays)},
                {"role": "assistant", "content": json.dumps(label_to_target(labels[pid]), ensure_ascii=False)},
            ],
            "images": images,
        })
    return rows


def load_posts(path: Path) -> dict[str, Post]:
    out = {}
    for line in Path(path).read_text().splitlines():
        if line.strip():
            p = Post.model_validate_json(line)
            out[p.post_id] = p
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build data/finetune/train.jsonl from the train split")
    ap.add_argument("--posts", type=Path, default=DEFAULT_POSTS)
    ap.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    ap.add_argument("--split-file", type=Path, default=DEFAULT_SPLIT)
    ap.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    try:
        split = verify_lock(a.split_file, a.lock)
    except SplitLockError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 3
    from abiso.places_match import load_barangays
    rows = build_rows(load_posts(a.posts), load_labels(a.labels), split["train"],
                      set(split["test"]), load_barangays())
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    print(f"wrote {len(rows)} training chats -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
