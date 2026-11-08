# Kuku promo finder

Reads a folder of time-aligned microdrama screenplays and returns a ranked shortlist of
20–60s promo clips: the episode, start/end of each segment, the hook, the cliffhanger, and one line on why it works.
It is a shortlist for an editor, not an auto-publisher.

## Website
`streamlit run app.py` starts a local web app. It shows the bundled show's results, lets you paste your own key or use
any OpenAI-compatible model, and lets you upload another show as a `.zip`. To deploy it free, see [DEPLOY.md](DEPLOY.md).

## Run

```bash
pip install -r requirements.txt
cp .env.example .env            # add GEMINI_API_KEY and GROQ_API_KEY (free tiers are enough)
python run.py input             # full run  -> output/promos.md, output/promos.json
python run.py input --fast      # skip the stronger re-score (still ~15 min on free tiers: 15 calls/min limit)
python run.py input --episodes 1-10   # a slice of the show (~3-4 min on free tiers), good for a live demo
python -m eval.evaluate output  # metrics -> eval/report.md (add --judge-check for the Groq judge)
python -m eval.ablate           # v1 vs v1+PoolC vs v1+hook search vs v2 -> eval/ablation.md
python run.py input --pools A,B:duration   # reproduce v1's candidate pools
```

Outputs: `output/promos.json` (acquisition), `output/promos_engagement.json`, `output/promos.md` (both, for editors),
`output/all_candidates_scored.json` (every candidate with scores and the reason it was or wasn't picked).

`input/` is any folder of screenplay `.txt` files in the Kuku format (searched recursively).
Every LLM response is cached in `cache/`, so re-running costs nothing. To swap a model, edit one line in `config.yaml`.

## How it works

| Stage | Who | What |
|---|---|---|
| 1. Parse | code | Every camera/action/sound/dialogue block becomes an element with an ID (`E01_008`) and exact times |
| 2. Episode pass | Gemini Flash Lite ×50 | Summary, who's who, unresolved question, 2–4 events with hook and ending options **as element IDs** |
| 3. Series pass | Gemini Flash ×1 | Character bible across aliases (e.g. *Mysterious Bearded Man* = Alexander) |
| 4. Candidates | code | **Pool A**: tagged hooks × tagged endings. **Pool B**: windows ending at the episode's end; v2 searches backwards for an opening that hooks (tagged hooks, then intense dialogue lines). **Pool C** (v2): windows starting at the episode's opening. Code snaps cuts to element boundaries, never cuts mid-line, and enforces 20–60s. IDs are stable (`E26B_0800_1209` = episode 26, pool B, 80.0–120.9s) |
| 5. Score | Gemini Flash Lite | Hook, cliffhanger, standalone, coherence and emotion (1–5), evidence cited by element ID **before** the score. The judge also sees the next ~8s after the cut (not in the promo) to tell real suspense from an arbitrary cut |
| 6. Re-score | Gemini Flash | The same rubric on the top 36 of both shortlists (skipped with `--fast`) |
| 7. Spoiler check | Gemini Flash Lite | The top 60 acquisition candidates are checked against the series bible's reveals. The show's premise (its title twist) is allowed; giving away a later twist (severity ≥ 4, with cited evidence) costs 0.5 in the acquisition list |
| 8. Verify, gate, rank | code | Cited evidence must be in the first 3s (hook) and inside and near the end of the clip (cliffhanger), or the score is capped. **Two shortlists** from the same scores. **Acquisition** (new viewers): gates hook, cliffhanger, standalone, coherence ≥ 3; score = 0.30·H + 0.30·C + 0.25·standalone + 0.15·emotion. **Engagement** (lapsed viewers): no standalone gate; 0.30·H + 0.30·C + 0.10·standalone + 0.30·emotion. Dedupe at >50% overlap, max 3 per episode (never padded), top 20 each |
| 9. Report | Gemini Flash ×1 | A "why post first" line for the acquisition top 10 |

**The LLM never writes a timestamp.** It returns element IDs, and code turns them into times, so a promo can't point at a moment that doesn't exist.

## Layout
`src/` pipeline · `prompts/` versioned prompts · `config.yaml` models, limits, weights · `eval/` labels and metrics · `output/` results

`input/`, `cache/` and `parsed/` are gitignored because the screenplays are confidential.
