"""Compile a split's posts with one backend into a predictions file for eval.score.

    uv run python -m eval.predict --backend gemma --split test
    uv run python -m eval.predict --backend gemini --split test --max-usd 0.50   # PAID

Resumable: post ids already in the output file are skipped. A compile failure
is written as {"post_id", "error"} (scored as wrong), never dropped.
Gemini refuses to start without --max-usd, and stops before the estimated
spend would pass it (estimate from the 10-03 smoke test: about $0.003 per image
post, $0.001 per text post, at the 2026 price in eval/smoke.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from abiso.compile import CompileError, Gemini, GemmaVLLM, MediaError, compile_post_detailed
from eval.smoke import GEMINI_PRICE
from eval.split import DEFAULT_LOCK, DEFAULT_SPLIT, SplitLockError, verify_lock
from finetune.build_dataset import DEFAULT_POSTS, load_posts

REPO = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["gemma", "gemma-lora", "gemini"], required=True)
    ap.add_argument("--split", choices=["test", "train"], required=True)
    ap.add_argument("--posts", type=Path, default=DEFAULT_POSTS)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--max-usd", type=float, help="required for gemini: hard spend cap")
    a = ap.parse_args(argv)
    try:
        split = verify_lock(DEFAULT_SPLIT, DEFAULT_LOCK)
    except SplitLockError as e:
        print(f"REFUSING: {e}", file=sys.stderr)
        return 3
    if a.backend == "gemini" and a.max_usd is None:
        print("REFUSING: gemini is paid; pass --max-usd after Carl approves the estimate", file=sys.stderr)
        return 2
    backend = {"gemma": lambda: GemmaVLLM(),
               "gemma-lora": lambda: GemmaVLLM(model="abiso-lora"),
               "gemini": lambda: Gemini()}[a.backend]()
    out = a.out or REPO / "data" / "predictions" / f"{a.backend}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {json.loads(l)["post_id"] for l in out.read_text().splitlines() if l.strip()} if out.exists() else set()
    posts = load_posts(a.posts)
    from abiso.places_match import load_barangays
    names = load_barangays()
    spent, failures = 0.0, 0
    with out.open("a") as fh:
        for pid in split[a.split]:
            if pid in done:
                continue
            if a.backend == "gemini" and spent + 0.01 > a.max_usd:   # 0.01 = worst case incl. a retry
                print(f"STOP: spend cap ${a.max_usd} reached (${spent:.4f} spent)", file=sys.stderr)
                return 4
            row: dict = {"post_id": pid}
            try:
                try:
                    r = compile_post_detailed(posts[pid], backend, names)
                except MediaError:
                    # Dead image link (expired CDN / site 404): score from text + alt text, and record it.
                    r = compile_post_detailed(posts[pid], backend, names, images=[])
                    row["images_unavailable"] = True
                row["events"] = [json.loads(e.model_dump_json()) for e in r.events]
                replies = r.replies
            except (CompileError, MediaError, KeyError) as e:
                failures += 1
                row["error"] = str(e)
                replies = getattr(e, "replies", [])
                print(f"FAIL {pid}: {e}", file=sys.stderr)
            row["usage"] = [{"in": rp.input_tokens, "out": rp.output_tokens,
                             "latency_s": round(rp.latency_s, 2)} for rp in replies]
            if a.backend == "gemini":
                spent += sum((rp.input_tokens or 0) / 1e6 * GEMINI_PRICE["input"]
                             + (rp.output_tokens or 0) / 1e6 * GEMINI_PRICE["output"] for rp in replies)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
    print(f"wrote {out}; {failures} failures; gemini spend ${spent:.4f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
