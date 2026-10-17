"""Measure GPU energy for compiling the 20-post workload on the PC's shared vLLM server.

Starts no GPU process. It only (1) samples `nvidia-smi` power over SSH, (2) reads
vLLM's Prometheus counters, and (3) sends the 20 requests to the existing server.

Per repetition:
  baseline 60 s  ->  batch (20 posts, concurrency 4)  ->  tail 60 s
Before each repetition it refuses to run if lifeos' gpu.json shows a game on the
PC or is stale. Raw samples and counters go to cost/runs/pc_rep{n}.json;
cost/compute.py turns them into Wh per post.

    uv run --no-project --with httpx python cost/measure_pc.py [reps]
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

import common

GPU_JSON = Path.home() / ".local/state/lifeos/gpu.json"
QUERY = "timestamp,power.draw,utilization.gpu,clocks.sm"
SAMPLER = f"nvidia-smi --query-gpu={QUERY} --format=csv,noheader,nounits -lms 500"
BASELINE_S = 60
TAIL_S = 60
CONCURRENCY = 4
COUNTERS = ("vllm:prompt_tokens_total", "vllm:generation_tokens_total",
            "vllm:prefix_cache_hits_total", "vllm:prefix_cache_queries_total",
            "vllm:num_requests_running", "vllm:num_requests_waiting")


def game_check() -> None:
    d = json.loads(GPU_JSON.read_text())
    age = time.time() - datetime.fromisoformat(d["generated"]).timestamp()
    if age > 600:
        raise SystemExit(f"STOP: {GPU_JSON} is {age:.0f}s old; cannot confirm no game is running")
    games = [w for g in d.get("gpus", []) for w in g.get("work", []) if w.get("kind") == "game"]
    if games:
        raise SystemExit(f"STOP: a game is running on the PC: {games}")


def metrics(c: httpx.Client) -> dict:
    text = c.get(f"{common.VLLM_BASE}/metrics").text
    out: dict[str, float] = {"t": time.time()}
    for line in text.splitlines():
        for name in COUNTERS:
            if line.startswith(name + "{") and f'model_name="{common.VLLM_MODEL}"' in line:
                out[name] = out.get(name, 0.0) + float(line.rsplit(" ", 1)[1])
    return out


class Sampler:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.proc = subprocess.Popen(["ssh", "PC", SAMPLER], stdout=subprocess.PIPE, text=True, bufsize=1)
        self.th = threading.Thread(target=self._read, daemon=True)
        self.th.start()

    def _read(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            parts = [x.strip() for x in line.split(",")]
            try:
                self.rows.append({"t": time.time(), "pc_ts": parts[0], "w": float(parts[1]),
                                  "util": float(parts[2]), "sm_mhz": float(parts[3])})
            except (ValueError, IndexError):
                continue

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=10)
        # The remote nvidia-smi can outlive the SSH session on Windows; kill only ours (matched by full query).
        ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'nvidia-smi.exe' -and "
              f"$_.CommandLine -like '*{QUERY}*-lms 500*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }}")
        # Windows OpenSSH hands the command to cmd.exe, which would eat the pipes; send it encoded.
        enc = base64.b64encode(ps.encode("utf-16-le")).decode()
        r = subprocess.run(["ssh", "PC", "powershell", "-NoProfile", "-EncodedCommand", enc],
                           capture_output=True, text=True, timeout=60)
        print("  remote sampler cleanup:", r.stdout.strip() or "none left", r.stderr.strip()[:200])


def one_request(c: httpx.Client, p: dict) -> dict:
    t0 = time.time()
    r = c.post(f"{common.VLLM_BASE}/v1/chat/completions", json={
        "model": common.VLLM_MODEL, "messages": common.openai_messages(p),
        "max_tokens": common.MAX_OUTPUT_TOKENS, "temperature": 0})
    t1 = time.time()
    if r.status_code != 200:
        raise RuntimeError(f"{p['sample_id']}: HTTP {r.status_code} {r.text[:300]}")
    j = r.json()
    return {"sample_id": p["sample_id"], "has_image": bool(p["image"]), "t0": t0, "t1": t1,
            "usage": j["usage"], "finish_reason": j["choices"][0]["finish_reason"],
            "output": j["choices"][0]["message"]["content"]}


def rep(n: int) -> Path:
    game_check()
    posts = common.posts()
    with httpx.Client(timeout=900) as c:
        s = Sampler()
        try:  # the remote sampler must die even if a request fails
            time.sleep(3)  # let the first samples arrive
            marks = {"m0": metrics(c)}
            print(f"rep {n}: baseline {BASELINE_S}s")
            time.sleep(BASELINE_S)
            marks["m1"] = metrics(c)
            print(f"rep {n}: batch of {len(posts)}")
            with ThreadPoolExecutor(CONCURRENCY) as ex:
                results = list(ex.map(lambda p: one_request(c, p), posts))
            marks["m2"] = metrics(c)
            print(f"rep {n}: batch took {marks['m2']['t'] - marks['m1']['t']:.1f}s; tail {TAIL_S}s")
            time.sleep(TAIL_S)
            marks["m3"] = metrics(c)
        finally:
            s.stop()
    out = common.RUNS / f"pc_rep{n}.json"
    common.RUNS.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "rep": n, "run_at": datetime.now(timezone.utc).isoformat(), "server": common.VLLM_BASE,
        "model": common.VLLM_MODEL, "concurrency": CONCURRENCY, "sampler": SAMPLER,
        "marks": marks, "samples": s.rows, "requests": results}, indent=1))
    if len(s.rows) < (BASELINE_S + TAIL_S):
        raise SystemExit(f"FAIL: only {len(s.rows)} power samples in rep {n}; sampler broken")
    print(f"rep {n}: {len(s.rows)} samples -> {out}")
    return out


if __name__ == "__main__":
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    for i in range(1, reps + 1):
        rep(i)
