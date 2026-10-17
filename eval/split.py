"""Lock the held-out test set (spec section 7). Run ONCE, before any model sees a label.

    uv run python -m eval.split            # reads data/labels/labels.jsonl

A seeded draw puts 50 labelled post ids in `test` and the rest in `train`,
writes data/labels/split.json, and writes its SHA-256 to eval/SPLIT_LOCK.
Commit both. If eval/SPLIT_LOCK already exists this refuses and exits 3:
re-splitting after seeing results is how a held-out set stops being held out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = REPO / "data" / "labels" / "labels.jsonl"
DEFAULT_SPLIT = REPO / "data" / "labels" / "split.json"
DEFAULT_LOCK = REPO / "eval" / "SPLIT_LOCK"
DEFAULT_SEED = 20261003
N_TEST = 50


class SplitLockError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_lock(lock: Path) -> str:
    text = Path(lock).read_text().strip()
    if not text:
        raise SplitLockError(f"{lock} is empty")
    return text.split()[0]


def verify_lock(split_path: Path = DEFAULT_SPLIT, lock: Path = DEFAULT_LOCK) -> dict:
    """Return the split if its hash matches the lock; raise SplitLockError otherwise."""
    if not Path(lock).exists():
        raise SplitLockError(f"no lock at {lock}: run eval.split first")
    if not Path(split_path).exists():
        raise SplitLockError(f"lock exists but {split_path} is missing")
    want, got = read_lock(lock), sha256_file(split_path)
    if want != got:
        raise SplitLockError(f"{split_path} sha256 {got} does not match lock {want}: the split was edited")
    return json.loads(Path(split_path).read_text())


def labelled_ids(labels_path: Path) -> list[str]:
    ids = []
    for n, line in enumerate(Path(labels_path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            ids.append(json.loads(line)["post_id"])
        except (json.JSONDecodeError, KeyError) as e:
            raise ValueError(f"{labels_path}:{n}: not a label line ({e})") from e
    return sorted(set(ids))


def make_split(post_ids: list[str], seed: int = DEFAULT_SEED, n_test: int = N_TEST) -> dict:
    ids = sorted(set(post_ids))
    if len(ids) <= n_test:
        raise ValueError(f"need more than {n_test} labelled posts to split, have {len(ids)}")
    test = sorted(random.Random(seed).sample(ids, n_test))
    test_set = set(test)
    return {"seed": seed, "n_test": n_test, "n_train": len(ids) - n_test,
            "test": test, "train": [i for i in ids if i not in test_set]}


def write_split(labels: Path, out: Path, lock: Path, seed: int, n_test: int) -> dict:
    if Path(lock).exists():
        raise SplitLockError(
            f"REFUSING TO RE-SPLIT: {lock} exists (sha256 {read_lock(lock)}). The test set is locked. "
            "If the labels genuinely changed, that is a protocol amendment for Carl, not a re-run.")
    if Path(out).exists():
        raise SplitLockError(f"REFUSING: {out} exists without a lock; inspect it before doing anything")
    split = make_split(labelled_ids(labels), seed, n_test)
    split["labels_sha256"] = sha256_file(labels)
    split["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(split, indent=1, sort_keys=True) + "\n")
    rel = Path(out).resolve()
    try:
        rel = rel.relative_to(REPO)
    except ValueError:
        pass
    Path(lock).write_text(f"{sha256_file(out)}  {rel}\n")
    return split


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    ap.add_argument("--out", type=Path, default=DEFAULT_SPLIT)
    ap.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--n-test", type=int, default=N_TEST)
    a = ap.parse_args(argv)
    try:
        split = write_split(a.labels, a.out, a.lock, a.seed, a.n_test)
    except SplitLockError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 3
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"locked {split['n_test']} test / {split['n_train']} train -> {a.out}; hash in {a.lock}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
