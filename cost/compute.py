"""Turn the measurements in cost/runs/ into cost/results.json.

Every number carries a unit, a date and a source. Rates and prices are typed in
below from primary sources where one exists (secondary ones are marked).
Assumptions are marked "assumption" and are not measurements.

    uv run --no-project python cost/compute.py
"""
from __future__ import annotations

import json
import statistics as st
from datetime import datetime, timezone

import common

POSTS_PER_BATCH = 20
PER = 1000  # report everything per 1,000 posts

# ---------------------------------------------------------------- sources
SRC = {
    "gemini_pricing": "https://ai.google.dev/gemini-api/docs/pricing",
    "gemini_counttokens": "https://ai.google.dev/api/tokens#method:-models.counttokens",
    "meralco_sep2026": "https://company.meralco.com.ph/news-and-advisories/lower-rates-september-2026",
    "meco_jul2026": "https://gosolarph.com/guides/electricity-rates-by-utility-philippines/",
    "pge_etouc": "https://www.pge.com/tariffs/assets/pdf/tariffbook/ELEC_SCHEDS_E-TOU-C.pdf",
    "pge_e1": "https://www.pge.com/tariffs/assets/pdf/tariffbook/ELEC_SCHEDS_E-1.pdf",
    "fx": "https://tribune.net.ph/2026/10/02/stocks-slide-for-five-days-straight-peso-strengthens",
    "m5max_ollama": "https://github.com/ollama/ollama/issues/15368",
    "m5max_x": "https://x.com/bridgemindai/status/2059950076789751992",
    "m5max_nbc": "https://www.notebookcheck.net/Apple-s-fastest-laptop-starts-to-show-its-age-Apple-MacBook-Pro-16-2026-M5-Max-Review.1250821.0.html",
    "m5max_cs": "https://creativestrategies.com/research/m5-max-chiplets-thermals-and-performance-per-watt/",
    "80plus": "https://en.wikipedia.org/wiki/80_Plus",
}


def v(value, unit, as_of, source, note="", quality="primary"):
    return {"value": value, "unit": unit, "as_of": as_of, "source": source, "source_quality": quality, "note": note}


RATES = {
    "gemini_3_8_flash_input": v(0.75, "USD per 1M input tokens (text/image)", "2026-10-01", SRC["gemini_pricing"],
                                "Introductory price through 2026-12-31; $1.50 from 2027-01-01."),
    "gemini_3_8_flash_output": v(3.75, "USD per 1M output tokens incl. thinking", "2026-10-01", SRC["gemini_pricing"],
                                 "Introductory price through 2026-12-31; $7.50 from 2027-01-01."),
    "gemini_3_8_flash_input_2027": v(1.50, "USD per 1M input tokens", "2027-01-01", SRC["gemini_pricing"]),
    "gemini_3_8_flash_output_2027": v(7.50, "USD per 1M output tokens incl. thinking", "2027-01-01", SRC["gemini_pricing"]),
    "gemini_batch_discount": v(0.5, "fraction of standard price", "2026-10-01", SRC["gemini_pricing"],
                               "Batch tier: $0.375 in / $1.875 out through 2026-12-31."),
    "meralco_residential": v(14.7424, "PHP per kWh (typical 200 kWh household, all-in)", "2026-09", SRC["meralco_sep2026"],
                             "September 2026 announcement (published 2026-09-11). October 2026 not yet announced on 2026-10-03."),
    "meco_residential": v(13.4933, "PHP per kWh (residential average, all-in)", "2026-07", SRC["meco_jul2026"],
                          "Secondary aggregator quoting MECO's July 2026 rate card; no newer figure found.", "secondary"),
    "pge_etouc_winter_offpeak": v(0.36757, "USD per kWh (E-TOU-C winter off-peak, bundled)", "2026-06-01 tariff; winter season from 2026-10-01",
                                  SRC["pge_etouc"], "Headline PG&E rate: batch compile runs overnight. Bundled PG&E; CleanPowerSF customers differ."),
    "pge_etouc_winter_peak": v(0.39757, "USD per kWh (E-TOU-C winter peak 4-9 pm)", "2026-06-01 tariff", SRC["pge_etouc"]),
    "pge_e1_tier2": v(0.40702, "USD per kWh (E-1 above baseline)", "2026-06-01 tariff", SRC["pge_e1"]),
    "usd_php": v(62.535, "PHP per USD (close)", "2026-10-02", SRC["fx"],
                 "BSP statistics site was down on 2026-10-03; news report of the day's close.", "secondary"),
}

ASSUME = {
    "platform_w": v(100, "W, non-GPU system draw (CPU, board, RAM, fans, drives) while serving", "assumption", "",
                    "Not measured; no wall meter on the PC. Attributed in the same share as the GPU.", "assumption"),
    "psu_efficiency": v(0.90, "fraction (DC out / AC in)", "assumption", SRC["80plus"],
                        "80 PLUS Gold: 87/90/87% at 20/50/100% load, 115 V (secondary: Wikipedia certification table). PSU model not checked.", "assumption (spec from a secondary source)"),
    "mac_package_w_low": v(70.6, "W, M5 Max package average under combined CPU+GPU stress", "2026", SRC["m5max_nbc"],
                           "Notebookcheck MBP 16 M5 Max review (seen via search snippet; page 403). Not an LLM workload.", "secondary"),
    "mac_package_w_high": v(114.4, "W, M5 Max package peak in the same test", "2026", SRC["m5max_nbc"], "", "secondary"),
    "mac_prefill_tps": v(168, "tokens/s prompt processing, Gemma 4 31B Q4_K_M, M5 Max 128 GB (Ollama/llama.cpp)", "2026-04-06",
                         SRC["m5max_ollama"], "User report.", "secondary"),
    "mac_decode_tps": v(15, "tokens/s generation, same report", "2026-04-06", SRC["m5max_ollama"],
                        "A second report says 21 tok/s (X post); 15 used as the slower, better-documented figure.", "secondary"),
    "mac_adapter_efficiency": v(0.90, "fraction", "assumption", "", "Charger and board losses; not measured.", "assumption"),
}


def stats(xs: list[float]) -> dict:
    return {"mean": st.mean(xs), "sd": st.stdev(xs) if len(xs) > 1 else 0.0, "min": min(xs), "max": max(xs), "n": len(xs)}


def window(samples, t0, t1):
    return [s for s in samples if t0 <= s["t"] <= t1]


def pc_rep(path) -> dict:
    d = json.loads(path.read_text())
    m, S = d["marks"], d["samples"]
    base = window(S, m["m0"]["t"], m["m1"]["t"])
    batch = window(S, m["m1"]["t"], m["m2"]["t"])
    tail = window(S, m["m2"]["t"], m["m3"]["t"])
    T = m["m2"]["t"] - m["m1"]["t"]
    p_ref = st.mean([s["w"] for s in base + tail])
    p_batch = st.mean([s["w"] for s in batch])
    reqs = d["requests"]
    ours_p = sum(r["usage"]["prompt_tokens"] for r in reqs)
    ours_g = sum(r["usage"]["completion_tokens"] for r in reqs)
    all_p = m["m2"]["vllm:prompt_tokens_total"] - m["m1"]["vllm:prompt_tokens_total"]
    all_g = m["m2"]["vllm:generation_tokens_total"] - m["m1"]["vllm:generation_tokens_total"]
    share_g = ours_g / all_g
    share_all = (ours_p + ours_g) / (all_p + all_g)
    share = max(share_g, share_all)  # conservative: charge the larger share
    n = len(reqs)
    gpu_batch_wh = p_batch * T / 3600
    return {
        "rep": d["rep"], "run_at": d["run_at"], "batch_seconds": T, "n_posts": n,
        "n_samples": {"baseline": len(base), "batch": len(batch), "tail": len(tail)},
        "gpu_w": {"baseline": st.mean([s["w"] for s in base]), "batch": p_batch, "tail": st.mean([s["w"] for s in tail]),
                  "reference_baseline_and_tail": p_ref},
        "gpu_util_pct": {"baseline": st.mean([s["util"] for s in base]), "batch": st.mean([s["util"] for s in batch])},
        "tokens": {"ours_prompt": ours_p, "ours_completion": ours_g, "server_prompt": all_p, "server_generation": all_g,
                   "share_generation": share_g, "share_all_tokens": share_all, "share_used": share},
        "wh_per_post": {
            "gpu_marginal": (p_batch - p_ref) * T / 3600 / n,
            "gpu_token_share": gpu_batch_wh * share / n,
            "gpu_dedicated_upper": gpu_batch_wh / n,
        },
        "mean_completion_tokens": ours_g / n, "mean_prompt_tokens": ours_p / n,
        "finish_reasons": sorted({r["finish_reason"] for r in reqs}),
    }


def main() -> None:
    fx = RATES["usd_php"]["value"]
    psu = ASSUME["psu_efficiency"]["value"]
    plat = ASSUME["platform_w"]["value"]

    # --------------------------------------------------- Gemini
    ct = json.loads((common.RUNS / "gemini_counttokens.json").read_text())
    gr = json.loads((common.RUNS / "gemini_run.json").read_text())
    pin, pout = RATES["gemini_3_8_flash_input"]["value"], RATES["gemini_3_8_flash_output"]["value"]
    calls = gr["rows"]

    def out_of(r):
        return r["usage"].get("candidatesTokenCount", 0) + r["usage"].get("thoughtsTokenCount", 0)

    def usd_per_1000(rows, a=pin, b=pout):
        return PER * st.mean([(r["usage"]["promptTokenCount"] * a + out_of(r) * b) / 1e6 for r in rows])

    in_tok = [r["total_tokens"] for r in ct["rows"]]
    gem = usd_per_1000(calls)
    by_rep = [usd_per_1000([r for r in calls if r["rep"] == k]) for k in sorted({r["rep"] for r in calls})]
    issuers = sorted({r["issuer"] for r in calls})
    gemini = {
        "model": gr["model"],
        "input_tokens_per_post": {**stats(in_tok), "source": "countTokens on all 20 samples (free endpoint); matches usage.promptTokenCount",
                                  "with_image": st.mean([r["total_tokens"] for r in ct["rows"] if r["has_image"]]),
                                  "text_only": st.mean([r["total_tokens"] for r in ct["rows"] if not r["has_image"]]),
                                  "image_tokens_each": sorted({d["tokenCount"] for r in ct["rows"] for d in r["prompt_tokens_details"] if d["modality"] == "IMAGE"})},
        "output_tokens_per_post": {**stats([out_of(r) for r in calls]),
                                   "visible": stats([r["usage"].get("candidatesTokenCount", 0) for r in calls]),
                                   "thinking": stats([r["usage"].get("thoughtsTokenCount", 0) for r in calls]),
                                   "source": f"{gr['calls']} paid generateContent calls: 20 posts x {gr['reps']} reps, default thinking"},
        "usd_per_1000": gem,
        "usd_per_1000_by_rep": by_rep,
        "usd_per_1000_range_over_reps": [min(by_rep), max(by_rep)],
        "usd_per_1000_by_issuer": {i: usd_per_1000([r for r in calls if r["issuer"] == i]) for i in issuers},
        "php_per_1000": gem * fx,
        "usd_per_1000_batch_tier": gem * RATES["gemini_batch_discount"]["value"],
        "usd_per_1000_from_2027": usd_per_1000(calls, RATES["gemini_3_8_flash_input_2027"]["value"],
                                               RATES["gemini_3_8_flash_output_2027"]["value"]),
    }
    low_path = common.RUNS / "gemini_run_thinking-low.json"
    low_spent = 0.0
    if low_path.exists():
        lo = json.loads(low_path.read_text())
        low_spent = lo["spent_usd_at_list_price"]
        gemini["thinking_low"] = {
            "usd_per_1000": usd_per_1000(lo["rows"]), "php_per_1000": usd_per_1000(lo["rows"]) * fx,
            "output_tokens_per_post": stats([out_of(r) for r in lo["rows"]]),
            "source": f"{lo['calls']} paid calls, thinkingLevel=low, 1 rep",
            "note": "Accuracy at low thinking NOT measured; cost only. Scored on the locked 50 in eval/ before use."}
    gemini["output_tokens_per_post"]["median"] = st.median([out_of(r) for r in calls])
    gemini["paid_spend_usd_at_list_price"] = {
        "default_thinking_run": gr["spent_usd_at_list_price"], "thinking_low_run": low_spent, "earlier_smoke_5_calls": 0.01475,
        "total": gr["spent_usd_at_list_price"] + low_spent + 0.01475,
        "note": "Smoke calls were on the earlier workload, commit 425641f. Carl's cap: $5."}

    # --------------------------------------------------- PC
    reps = [pc_rep(p) for p in sorted(common.RUNS.glob("pc_rep*.json"))]
    if len(reps) < 3:
        raise SystemExit(f"FAIL: need 3 PC reps, found {len(reps)}")
    gpu_share = [r["wh_per_post"]["gpu_token_share"] for r in reps]
    gpu_marg = [r["wh_per_post"]["gpu_marginal"] for r in reps]
    gpu_ded = [r["wh_per_post"]["gpu_dedicated_upper"] for r in reps]
    # wall energy: GPU + platform, attributed in the same share, through the PSU
    wall_share = [(r["wh_per_post"]["gpu_token_share"] + plat * r["batch_seconds"] / 3600 * r["tokens"]["share_used"] / r["n_posts"]) / psu for r in reps]
    wall_ded = [(r["wh_per_post"]["gpu_dedicated_upper"] + plat * r["batch_seconds"] / 3600 / r["n_posts"]) / psu for r in reps]
    kwh_1000 = st.mean(wall_share) * PER / 1000
    kwh_1000_ded = st.mean(wall_ded) * PER / 1000

    def elec(kwh):
        m_php = kwh * RATES["meralco_residential"]["value"]
        meco_php = kwh * RATES["meco_residential"]["value"]
        pge_usd = kwh * RATES["pge_etouc_winter_offpeak"]["value"]
        pge_e1_usd = kwh * RATES["pge_e1_tier2"]["value"]
        return {"meralco": {"php": m_php, "usd": m_php / fx},
                "meco": {"php": meco_php, "usd": meco_php / fx},
                "pge_etouc_offpeak": {"usd": pge_usd, "php": pge_usd * fx},
                "pge_e1_tier2": {"usd": pge_e1_usd, "php": pge_e1_usd * fx}}

    pc = {
        "server": common.VLLM_BASE, "model": common.VLLM_MODEL + " (AWQ 4-bit, vLLM, RTX 5090 GPU0, shared)",
        "reps": reps,
        "gpu_w_batch": stats([r["gpu_w"]["batch"] for r in reps]),
        "gpu_w_baseline": stats([r["gpu_w"]["reference_baseline_and_tail"] for r in reps]),
        "wh_per_post_gpu_marginal": stats(gpu_marg),
        "wh_per_post_gpu_token_share": stats(gpu_share),
        "wh_per_post_gpu_dedicated_upper": stats(gpu_ded),
        "wh_per_post_wall_token_share": stats(wall_share),
        "wh_per_post_wall_dedicated_upper": stats(wall_ded),
        "kwh_per_1000_headline": kwh_1000,
        "kwh_per_1000_dedicated_upper": kwh_1000_ded,
        "cost_per_1000_headline": elec(kwh_1000),
        "cost_per_1000_range_over_reps": {"low": elec(min(wall_share) * PER / 1000), "high": elec(max(wall_share) * PER / 1000)},
        "cost_per_1000_dedicated_upper": elec(kwh_1000_ded),
        "method": ("nvidia-smi power.draw every 500 ms over SSH; 60 s baseline, batch of 20 posts at concurrency 4, 60 s tail; "
                   "vLLM /metrics token counters read at each boundary. Headline = GPU energy in the batch window x our share "
                   "of tokens the server processed in that window (larger of generation-only and all-token share), plus "
                   "platform power in the same share, divided by PSU efficiency. Marginal = (batch W - mean of baseline and "
                   "tail W) x duration. Dedicated upper = all GPU + platform energy in the window charged to our 20 posts."),
    }

    # --------------------------------------------------- Mac (estimate only; nothing was run on a Mac)
    mp = st.mean([r["mean_prompt_tokens"] for r in reps])
    mc = st.mean([r["mean_completion_tokens"] for r in reps])
    sec = mp / ASSUME["mac_prefill_tps"]["value"] + mc / ASSUME["mac_decode_tps"]["value"]
    eff = ASSUME["mac_adapter_efficiency"]["value"]
    wh_lo = ASSUME["mac_package_w_low"]["value"] * sec / 3600 / eff
    wh_hi = ASSUME["mac_package_w_high"]["value"] * sec / 3600 / eff
    wh_mid = (wh_lo + wh_hi) / 2
    mac = {
        "label": "ESTIMATE, not measured (Carl's rule: no bulk compute on the Macs)",
        "seconds_per_post": sec, "inputs": {"prompt_tokens": mp, "completion_tokens": mc},
        "wh_per_post": {"low": wh_lo, "mid": wh_mid, "high": wh_hi},
        "hours_per_1000": sec * PER / 3600,
        "cost_per_1000_mid": elec(wh_mid * PER / 1000),
        "cost_per_1000_low": elec(wh_lo * PER / 1000),
        "cost_per_1000_high": elec(wh_hi * PER / 1000),
        "excludes": "vision-encoder time for image posts (6/20); display; idle power between posts",
    }

    out = {
        "question": "Cost to compile 1,000 city posts into events",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "workload": {"posts": POSTS_PER_BATCH, "synthetic": True, "with_image": 6, "issuers": {"city": 4, "power (MECO)": 4, "water (MCWD)": 4, "deped": 4, "pagasa": 4}, "mix_assumption": "equal weights; real posting volume per issuer not yet counted",
                     "languages": "Cebuano, Taglish, English", "path": "cost/samples/",
                     "note": "SYNTHETIC posts mirroring the compile task; token counts must be re-measured on real posts"},
        "rates": RATES, "assumptions": ASSUME,
        "gemini": gemini, "pc": pc, "mac": mac,
    }
    (common.HERE / "results.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({"gemini_usd_1000": gem, "gemini_php_1000": gem * fx,
                      "pc_kwh_1000": kwh_1000, "pc": pc["cost_per_1000_headline"],
                      "pc_wh_post_gpu_share": pc["wh_per_post_gpu_token_share"],
                      "pc_wh_post_marginal": pc["wh_per_post_gpu_marginal"],
                      "pc_w_batch": pc["gpu_w_batch"], "pc_w_base": pc["gpu_w_baseline"],
                      "mac": mac["cost_per_1000_mid"], "mac_s": sec}, indent=1))


if __name__ == "__main__":
    main()
