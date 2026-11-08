# Evaluation findings (full 50-episode run)

## Caveat on the labels
The 10 labels in `eval/labels.json` were written by Claude, the AI assistant that also wrote the scoring
prompts, not by a human editor. To limit bias they were written from the raw screenplays of 10 episodes
whose candidates and scores had not been opened (6, 10, 12, 14, 17, 19, 21, 26, 29, 38), before looking at
any output for them. They are AI editorial judgement, not ground truth.

## Headline numbers
| Metric | Result | What it says |
|---|---|---|
| Candidate recall (IoU ≥ 0.5) | **6/8 (75%)** | Candidate generation usually produces the labelled promo |
| Episode top-3 hit | **5/8 (62%)** | Within its episode, the labelled promo is usually in the tool's top 3 |
| Global top-20 hit | **0/8** | None of the 8 labelled promos made the series-wide top 20 (see below) |
| Boundary error on matches | **4.9s** mean | When found, start and end are within about 5s of the label |
| "No promo here" episodes (6, 17) | **2/2 agree** | Nothing from either quiet episode was shortlisted |
| Weak labels (continuity notes) | **4.08 vs 2.86** | Ending clips of episodes the writers call cliffhangers score higher on cliffhanger |
| LLM output errors after retries | **0/288** | Plus 5 unsupported evidence citations caught and capped by code |

## Why the global top-20 hit is 0/8
- With 20 slots across 50 episodes, most episodes get no promo, so a random labelled episode is unlikely to be in the top 20.
- All the matched candidates scored 3.15 to 3.75 against a cutoff of 4.00. All were judged only by the cheap model, because they missed the top 36 sent for the stronger re-score.
- The per-episode metric (top-3 hit, 62%) is the better measure of "would an editor find my pick".

## Failure cases (concrete, from this run)
1. **Openings are under-used as hooks (2 misses).** In Ep 21 (barn doors slam, "Toby is dying!") and
   Ep 29 (guards dragging her into the dungeon), the best moment is in the first 5 seconds of the episode,
   but no candidate starts there. The episode pass never offered the opening as a hook option, and
   Pool B only covers episode endings.
   *Fix (v2, not applied to keep this evaluation clean):* add Pool C, the first 20–60s of each episode.
2. **The right ending with the wrong start (Ep 26, Ep 38).** The tool found the ring cliffhanger (Ep 26) and
   the "I suppose I'm next" bells ending (Ep 38), but its Pool B windows start 8–6 seconds too early, on
   backstory and eating bread, so the hook scored 2 and they were rejected. Pool B picks its start by
   duration, not by hook strength.
   *Fix:* for each Pool B ending, also try the tagged hook options before it.
3. **Mid-series promos are penalised on standalone.** The judge marks most episode 10–40 clips 3/5 on
   standalone ("misses the backstory of..."). The shortlist leans to the series start and its big
   set-pieces. That may be right for acquisition promos, but it is a bias to know about.
4. **Late-episode promos spoil reveals.** For example, the Ep 42 clip gives away that the "dead" husband is
   the rightful Duke. The judge does not penalise spoilers.
   *Fix:* add a "spoils a reveal" check using the series bible's `reveal` field.
5. **Judge disagreement on what counts as a cliffhanger (Ep 10).** The labeller saw "who is this wounded man,
   and what happened to him?" as open. The judge said the scar reveal "pays off" inside the clip
   (cliffhanger 2/5). This is a legitimate editorial disagreement, not a bug.
6. **Generic labels collide in the character bible.** `BLOND NOBLEMAN` maps to two different people.
   This is mitigated by using episode-level who's-who in scoring.

## Second judge (Groq gpt-oss-120b, same rubric, the 20 shortlisted promos)
- The judges **agree on hook and cliffhanger**: mean absolute difference 0.30 on each (1–5 scale).
- They **disagree on standalone** (mean difference 1.20). Groq gives almost every promo 2–3 on standalone,
  where Gemini gives 4–5. Groq would fail 5/20 on the standalone gate.
- Reading: "can a stranger follow this?" is the least reliable criterion. The 25% weight and the gate on it
  rest on the judgement the two models disagree about most. Real viewer data (3-second hold rate,
  completion rate) should calibrate it.
- This is consistency between two models, not correctness.

## v2: fixes for failure cases 1–4, and an ablation
**What changed:**
- **Pool C:** windows starting at the episode's opening.
- **Pool B hook search:** ending windows start on a tagged hook or an intense dialogue line.
- **Spoiler check:** a separate step, so scoring stays identical across variants.
- **Two shortlists:** acquisition and engagement.
- **Stable candidate IDs,** so each variant only pays for its new candidates.

**Label sets:**
- **Dev** = the 10 episodes that exposed v1's failures. v2 was designed after seeing them, so gains there are optimistic.
- **Fresh** = 4 episodes (8, 22, 33, 48) labelled blind *before* v2 was written.

All 4 variants ran in `--fast` mode, with the same prompts and ranking (`eval/ablation.md`).

| Variant | Candidates | Dev recall | Dev ep top-3 | Dev acq top-20 | Dev boundary err | Fresh recall | Fresh acq top-20 |
|---|---|---|---|---|---|---|---|
| v1 | 288 | 6/8 | 5/8 | 2/8 | 4.9s | 1/3 | 0/3 |
| v1 + Pool C | 341 | **8/8** | **7/8** | 2/8 | 4.2s | 1/3 | 0/3 |
| v1 + hook search | 351 | 5/8 | 5/8 | 1/8 | **2.1s** | 1/3 | 1/3 |
| v2 (both) | 404 | 7/8 | 7/8 | **3/8** | 2.1s | 1/3 | 0/3 |

**Reading:**
- **Pool C works.** It recovers both missed openings (Ep 21 and Ep 29: IoU 0.48 → 0.90 and 0.22 → 0.90) and adds 3 of the 20 acquisition promos.
- **Hook search improves precision rather than recall.**
  - Boundary error halves (4.9s → 2.1s), and Ep 26 and Ep 22 become exact (IoU 1.00) and score higher (3.45 → 4.30).
  - The recall "drop" is Ep 12. The tool now prefers a tighter cut (42.2–87.2s, score 4.45) that falls just under the 0.5 IoU threshold against the label. It is a stricter-metric artefact, not a worse promo.
- **The fresh set does not move** (n = 3, too small to conclude anything). Its misses are new kinds:
  - **Ep 22:** found exactly (IoU 1.00, 4.05), but a tighter 22s cut of the *same* cliffhanger (109–131s, 4.45) outranked it. An editor would probably count this as a hit.
  - **Ep 33:** a genuine miss. The mid-episode climax ("That is witchcraft", 2:06) was never tagged as an ending option, and the episode-end windows close on a resolved, weeping fade (cliffhanger 1/5).
    *Next fix:* also seed endings at high-intensity lines mid-episode, not only tagged endings and episode ends.
  - **Ep 48:** a late-series villain monologue. The judge scores standalone at 2 almost everywhere. This is a legitimate disagreement, and the engagement list is the right home for it.
- **Spoilers:** 2–6 of the 60 checked acquisition candidates per variant give away a later twist. They are penalised, not removed.

**Final v2 run** (full mode: 35/36 re-scored by the stronger model; `output/`, `eval/report.md`):
- 404 candidates, 0 scoring errors.
- Acquisition top 20 from 19 episodes, pools A 11 / B 4 / C 5. Engagement top 20, pools A 12 / B 4 / C 4.
- 5 of 60 checked acquisition candidates give away a later twist.
- **Dev:** recall 7/8, episode top-3 7/8, acquisition top-20 2/8, boundary error 2.1s. Quiet episodes correctly empty: 2/2.
- **Fresh:** recall 1/3, top-3 1/3, top-20 0/3. Quiet episode correctly empty: 1/1.
- **Weak labels:** ending clips of episodes the writers call cliffhangers score 4.31 on cliffhanger, vs 2.80 for other endings (v1: 3.96 vs 2.91).

**Honest bottom line:** v2's candidate generation is measurably better on the episodes it was designed for (recall and boundaries). It is unproven on fresh episodes. The next lever is a new kind of candidate (mid-episode climax endings), not more tuning.

## Cost and quota (free tiers)
- A full run is about 75 Flash Lite calls, about 8 Flash calls and 0–20 Groq calls. Re-runs come from cache.
- The free Flash quota (20 calls a day per model) is the binding limit. Development runs used it up once.
  Fixes: a fallback model, failing fast on daily-quota errors, and `--fast` mode for development.
