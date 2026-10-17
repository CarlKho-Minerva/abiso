# Cost: compiling 1,000 official posts into events (subproblem 8)

Run 2026-10-03 (PDT). Every number, with its unit, date and source URL, is in `cost/results.json`. Figure: `cost/fig4_cost.png`. The deck reads the `run_date` and `cost` keys, which `cost/deck_view.py` copies from the study's numbers.

## Results (per 1,000 posts)

| Option | USD | PHP | Basis |
|---|---|---|---|
| Gemini 3.8 Flash, default thinking | $4.35 (runs: $3.46–4.81) | ₱272 | real usage, 60 calls × list price |
| Same, low thinking | $1.39 | ₱87 | 20 calls; accuracy not scored |
| Same, Batch tier / from 2027-01-01 | $2.18 / $8.71 | ₱136 / ₱545 | price page |
| Gemma 4 31B, Carl's PC, Meralco | $0.035 ($0.035–0.036) | ₱2.22 | measured electricity |
| Same, MECO (Lapu-Lapu) | $0.032 | ₱2.03 | rate from a secondary source, July 2026 |
| Same, PG&E E-TOU-C winter off-peak | $0.055 | ₱3.46 | measured electricity |
| Gemma 4 31B, M5 Max, Meralco | $0.105 ($0.080–0.129) | ₱6.54 | **estimate, not run** |

These are electricity costs only. They leave out the hardware, which is already owned.

## Method (#estimation)

**Workload.** 20 SYNTHETIC posts, 4 from each issuer: city, MECO, MCWD, DepEd, PAGASA. 6 of them carry a 1080×1350 JPEG. Equal weighting is an assumption: nobody has counted how often each issuer posts.

**Gemini.**
- Input averages 961 tokens per post: 663 for text-only posts, 1,655 for posts with an image, and 1,073 for each image. Source: the free countTokens endpoint.
- Output averages 969 tokens (median 556): 163 visible and 806 thinking.
- Cost differs by issuer: water $2.74, power $3.25, city $3.92, PAGASA $5.68 and DepEd $6.18 per 1,000.

**PC.** Gemma 4 31B (AWQ) runs on vLLM on GPU0, on a server shared with Carl's nightly jobs.
- Before measuring, the agent checked that no game was running on the PC.
- `nvidia-smi` recorded GPU power every 0.5 s over SSH: 60 s before the batch, the batch of 20 (4 at a time), then 60 s after.
- vLLM's token counters were read at each boundary. This was repeated 3 times, and no new GPU process was started.

**Measured (#descriptivestats, n = 3).**
- During the batch the GPU drew 524 W (519–527), against 519 W before and after (518–521).
- The GPU was saturated, so the extra power is 0.0016 Wh/post (−0.0005 to 0.0034). That is noise.
- The headline charges the GPU energy by our share of the tokens processed in that window: 0.65–0.72, the larger of the generation-only and all-token shares. That gives 0.114 Wh/post on the GPU (0.111–0.117).
- Adding 100 W for the rest of the PC and 90% power-supply efficiency (both assumptions) gives 0.151 Wh/post at the wall.
- Charging the whole window to our posts gives an upper bound of 0.217 Wh/post.

**Mac.** Nothing was run on the Mac.
- Time per post: 757 prompt tokens at 168 tok/s plus 165 output tokens at 15 tok/s, which is 15.5 s. The speeds come from a user report of Gemma 4 31B on an M5 Max (secondary source).
- Power: 70.6–114.4 W, from a Notebookcheck stress test rather than LLM inference, over an assumed 90% adapter efficiency.
- Result: 0.34–0.55 Wh/post, or 4.3 hours per 1,000 posts.

## Rates (#sourcequality)

- Gemini: $0.75 per 1M input tokens and $3.75 per 1M output tokens, thinking included. Primary source, page updated 2026-10-01.
- Meralco: ₱14.7424/kWh for September 2026 (primary). October has not been announced.
- MECO: ₱13.4933/kWh for July 2026 (secondary).
- PG&E: $0.36757/kWh (primary; E-1 Tier 2 is $0.40702). These are bundled rates; CleanPowerSF customers pay a different total.
- Exchange rate: ₱62.535 per dollar, close of 2026-10-02 (secondary).

## Caveats

1. The posts are synthetic, and the issuer mix is assumed. Re-count tokens on real posts once real posts are collected.
2. Gemini's thinking is heavy-tailed: 2 of the 60 calls used about 7,900 thinking tokens each. Capping thinking cuts the cost by about two thirds, but its effect on accuracy is untested.
3. The PC number is an allocation on a busy shared server.
4. The 100 W for the rest of the PC and the power-supply efficiency are assumed; there is no wall meter.
5. The Mac figure is an estimate built from secondary sources and excludes vision-encoder time.

Paid Gemini spend: about $0.30 in total (cap $5).
