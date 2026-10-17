# Label page

Start it from the repo root:

```
uv run python -m label.server
```

Then open http://127.0.0.1:8516. It binds 0.0.0.0, so anyone on the same network can reach it while it runs. Stop it with Ctrl-C.

- Reads `data/raw/posts.jsonl`. Writes `data/labels/labels.jsonl`, append-only: saving a post again adds a newer line, and the newest line wins.
- One label per post. If a post lists several outages, label the most common type, every barangay it names (the union), and the overall window (earliest start, latest end). This is the rule the scorer uses.
- One post at a time. Keys: `p` power, `w` water, `c` class suspension, `k` work suspension, `r` road, `t` weather, `o` other advisory, `n` not a disruption. `s` or Cmd-Enter saves and moves on, `←`/`→` move between posts, `u` jumps to the next unlabelled post.
- Times are Philippine time (UTC+8) and are stored with `+08:00`. No barangay selected means city-wide or not applicable.
- Tags are free text for the labeller's own use. They are never scored.
- **How the reference labels were actually made (protocol amendment, 2026-10-08):** `draft_haiku.py` drafted a label for every post with Claude Haiku, `review_sonnet.py` checked each draft with Claude Sonnet (84 of 167 corrected), and a person spot-checked 20 of them on this page (`data/labels/spotcheck.json`): 18 of 20 were identical, both differences in `status`. Labels carry their origin in `source`. Claude is not a model being scored, so the eval compares Gemini and Gemma against an independent labeller, not against themselves.
- Leaving a post without saving throws away whatever you entered for it.
