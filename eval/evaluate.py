"""Evaluate a run against hand labels, plus checks that need no labels.

  python -m eval.evaluate output                 metrics -> eval/report.md
  python -m eval.evaluate output --judge-check   also re-score the top 20 with the Groq judge

Metrics
- candidate recall : was the human promo among the candidates at all (IoU >= 0.5)?  -> candidate generation
- top-20 hit rate  : is it in the final shortlist?                                     -> scoring + ranking
- episode top-3    : among that episode's candidates, is a match in the top 3 by score?
- boundary error   : mean(|start diff|, |end diff|) in seconds, on matches only
- weak labels      : episodes whose NEXT episode's continuity note says "cliffhanger" should
                     have higher cliffhanger scores on their ending clips (pool B)
- judge agreement  : Gemini vs Groq on the same top 20 with the same rubric (consistency, not truth)
- quality ratings  : your own 1-5 ratings of the top 20 in eval/ratings.csv, if filled in
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
from collections import Counter
from pathlib import Path

from src.parse import load_folder, to_sec

ROOT = Path(__file__).resolve().parent.parent
CRIT = ["hook", "cliffhanger", "standalone", "coherence", "emotion"]


def iou(a, b) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union else 0.0


def best_match(h, rows):
    scored = [(iou(h, (r["start"], r["end"])), r) for r in rows]
    return max(scored, key=lambda x: x[0], default=(0.0, None))


def _selected(r: dict, mode: str) -> bool:
    return (r.get(mode) or {}).get("status", r.get("status") if mode == "acquisition" else "") == "selected"


def metrics(rows: list[dict], labels: list[dict]) -> dict:
    """Numbers for one label set (used by the report and by eval/ablate.py)."""
    m = {"n": 0, "recall": 0, "acq_hit": 0, "eng_hit": 0, "top3": 0, "berr": [], "match_pool": Counter(),
         "weak_n": 0, "weak_agree": 0, "per_label": []}
    for lab in labels:
        if not lab.get("start") or not lab.get("end"):
            continue
        ep_rows = [r for r in rows if r["episode"] == lab["episode"]]
        if lab.get("weak"):  # labeller says: nothing here is worth posting (acquisition)
            sel = [r for r in ep_rows if _selected(r, "acquisition")]
            m["weak_n"] += 1
            m["weak_agree"] += not sel
            m["per_label"].append((lab, None, None, f"weak episode: {len(sel)} shortlisted"))
            continue
        h = (to_sec(lab["start"]), to_sec(lab["end"]))
        ranked = sorted(ep_rows, key=lambda r: -r.get("total", 0))[:3]
        i_all, r_all = best_match(h, ep_rows)
        i_acq, _ = best_match(h, [r for r in ep_rows if _selected(r, "acquisition")])
        i_eng, _ = best_match(h, [r for r in ep_rows if _selected(r, "engagement")])
        i_top, _ = best_match(h, ranked)
        m["n"] += 1
        m["recall"] += i_all >= 0.5
        m["acq_hit"] += i_acq >= 0.5
        m["eng_hit"] += i_eng >= 0.5
        m["top3"] += i_top >= 0.5
        if i_all >= 0.5:
            m["berr"].append((abs(r_all["start"] - h[0]) + abs(r_all["end"] - h[1])) / 2)
            m["match_pool"][r_all["pool"]] += 1
        where = ("in acquisition top 20" if i_acq >= 0.5 else "in engagement top 20" if i_eng >= 0.5
                 else "scored, not shortlisted" if i_all >= 0.5 else "never generated")
        m["per_label"].append((lab, i_all, r_all, where))
    return m


def label_metrics(rows: list[dict], labels: list[dict], title: str) -> list[str]:
    m = metrics(rows, labels)
    if not m["n"]:
        return [f"### {title}", "No labels."]
    n = m["n"]
    pct = lambda k: f"{m[k]}/{n} ({m[k]/n:.0%})"
    out = [f"### {title}", f"Labelled episodes with a promo: {n}",
           f"- Candidate recall: {pct('recall')} (did candidate generation find it?)",
           f"- Episode top-3 hit: {pct('top3')} (is it in that episode's top 3 by score?)",
           f"- Acquisition top-20 hit: {pct('acq_hit')}   Engagement top-20 hit: {pct('eng_hit')}",
           f"- Boundary error on matches: {st.mean(m['berr']):.1f}s mean" if m["berr"] else "- Boundary error: no matches",
           f"- Pool of the matching candidate: {dict(m['match_pool'])}",
           f"- 'No promo here' episodes with nothing shortlisted: {m['weak_agree']}/{m['weak_n']}",
           "", "| Ep | Labelled promo | Best IoU (candidate) | Outcome |", "|---|---|---|---|"]
    for lab, i, r, where in m["per_label"]:
        out.append(f"| {lab['episode']} | {lab['start']}-{lab['end']} | "
                   f"{'-' if i is None else f'{i:.2f}'} ({r['cid'] if r else '-'}) | {where} |")
    return out


def weak_label_check(rows: list[dict], folder: Path) -> list[str]:
    eps = {e.episode: e for e in load_folder(folder)}
    flagged = {n - 1 for n, e in eps.items() if "cliffhanger" in e.continuity.lower()}
    a, b = [], []
    for r in rows:
        if r["pool"] != "B" or "error" in (r.get("result") or {}):
            continue
        s = ((r.get("result") or {}).get("cliffhanger") or {}).get("score")
        if s is None:
            continue
        (a if r["episode"] in flagged else b).append(s)
    if not a or not b:
        return ["Weak-label check: not enough data."]
    return [f"Episodes whose next episode says it 'picks up from the cliffhanger': {len(flagged)}",
            f"- Mean cliffhanger score of their ending clips: {st.mean(a):.2f} (n={len(a)})",
            f"- Mean for all other episode-ending clips: {st.mean(b):.2f} (n={len(b)})",
            "- Expect the first to be higher if the cliffhanger score tracks the writers' own cliffhangers."]


def judge_check(out_dir: Path) -> list[str]:
    from src.llm import LLM, load_config
    from src.score import score_all
    cfg = load_config()
    rows = json.loads((out_dir / "all_candidates_scored.json").read_text(encoding="utf-8"))
    ep_out = {int(k): v for k, v in json.loads((out_dir / "episodes.json").read_text(encoding="utf-8")).items()}
    series = json.loads((out_dir / "series.json").read_text(encoding="utf-8"))
    sel = [r for r in rows if r.get("status") == "selected"]
    other = score_all(sel, ep_out, series, LLM(cfg), cfg, role="check", batch_size=1, workers=1)
    diffs = {k: [] for k in CRIT}
    gate_agree = 0
    lines = ["| Rank | CID | Gemini H/C/S/Coh/E | Groq H/C/S/Coh/E |", "|---|---|---|---|"]
    for r in sel:
        o = other.get(r["cid"], {})
        if "error" in o:
            lines.append(f"| {r['rank']} | {r['cid']} | - | error: {o['error'][:60]} |")
            continue
        g = [(r["result"].get(k) or {}).get("score", 0) for k in CRIT]
        q = [(o.get(k) or {}).get("score", 0) for k in CRIT]
        for k, x, y in zip(CRIT, g, q):
            diffs[k].append(abs(x - y))
        gate_agree += all(y >= cfg["promo"]["modes"]["acquisition"]["gates"].get(k, 0) for k, y in zip(CRIT, q))
        lines.append(f"| {r['rank']} | {r['cid']} | {'/'.join(map(str, g))} | {'/'.join(map(str, q))} |")
    n = len(diffs["hook"])
    head = [f"Second judge (Groq) re-scored the {len(sel)} shortlisted promos with the same rubric.",
            f"- Groq also passes all gates on {gate_agree}/{n}",
            "- Mean absolute score difference: " + ", ".join(
                f"{k} {st.mean(v):.2f}" for k, v in diffs.items() if v),
            "- This is a consistency check between two models, not proof that either is right.", ""]
    (out_dir / "judge_check.json").write_text(json.dumps(other, ensure_ascii=False, indent=1), encoding="utf-8")
    return head + lines


def ratings() -> list[str]:
    path = ROOT / "eval" / "ratings.csv"
    if not path.exists():
        return ["No eval/ratings.csv yet."]
    rows = [r for r in csv.DictReader(path.open(encoding="utf-8")) if r.get("hook")]
    if not rows:
        return ["eval/ratings.csv is empty."]
    cols = [c for c in ["hook", "cliffhanger", "clarity", "would_post"] if c in rows[0]]
    return [f"Your ratings of {len(rows)} shortlisted promos: " + ", ".join(
        f"{c} {st.mean(float(r[c]) for r in rows if r[c]):.2f}" for c in cols)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--input", type=Path, default=ROOT / "input")
    ap.add_argument("--judge-check", action="store_true")
    a = ap.parse_args()
    rows = json.loads((a.out / "all_candidates_scored.json").read_text(encoding="utf-8"))
    labels = json.loads((ROOT / "eval" / "labels.json").read_text(encoding="utf-8"))["labels"]
    dev = [l for l in labels if l.get("set") != "fresh"]
    fresh = [l for l in labels if l.get("set") == "fresh"]
    L = ["# Evaluation", "", "## Against labels (AI-written, blind; see eval/labels.json)",
         *label_metrics(rows, dev, "Dev set: 10 episodes that exposed v1's failures (v2 was designed after seeing them)"),
         "", *label_metrics(rows, fresh, "Fresh set: 4 episodes labelled before v2 was built (honest test)"),
         "", "## Weak labels from continuity notes", *weak_label_check(rows, a.input),
         "", "## Your quality ratings", *ratings()]
    if a.judge_check:
        L += ["", "## Second-judge consistency", *judge_check(a.out)]
    (ROOT / "eval" / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
