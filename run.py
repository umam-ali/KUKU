"""Find promos in a folder of microdrama screenplays.

  python run.py input                      full run
  python run.py input --episodes 1-10      a slice of the show (quick demo)
  python run.py input --until candidates   stop after a stage
  python run.py input --fast               skip the stronger re-score

Stages: parse -> episode -> series -> candidates -> score -> rescore -> rank -> report
All LLM responses are cached in cache/, so re-running a finished stage costs nothing.
The same pipeline powers the web app (app.py) through run_pipeline().
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Callable

from src.llm import LLM, ROOT, load_config
from src.parse import load_folder
from src import understand, candidates, score, report

STAGES = ["parse", "episode", "series", "candidates", "score", "rescore", "rank", "report"]
RESCORE_TOP = 36  # 6 calls of 6 on the stronger model (free tier: 20 calls/day)


def ep_range(spec: str | None) -> set[int] | None:
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


def run_pipeline(folder: Path, out: Path, cfg: dict, llm: LLM, episodes: set[int] | None = None,
                 fast: bool = False, until: str = "report", log: Callable[[str], None] = print,
                 stage: Callable[[str, float], None] | None = None) -> dict:
    """Run the whole pipeline. log() gets human-readable lines; stage(name, fraction) reports progress.
    Returns a summary dict (empty if stopped early)."""
    stop = STAGES.index(until)
    stage = stage or (lambda name, frac: None)
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)

    stage("Reading screenplays", 0.02)
    eps = load_folder(folder)
    if episodes:
        eps = [e for e in eps if e.episode in episodes]
    if not eps:
        raise ValueError(f"No screenplay .txt files found in {folder}")
    warn = [w for e in eps for w in e.warnings]
    log(f"[parse] {len(eps)} episodes, {sum(len(e.elements) for e in eps)} elements, {len(warn)} warnings")
    if stop == 0:
        return {}

    stage(f"Understanding {len(eps)} episodes", 0.08)
    ep_out = understand.episode_pass(eps, llm, out)
    probs = [p for r in ep_out.values() for p in r.get("problems", [])]
    n_ev = sum(len(r.get("events", [])) for r in ep_out.values())
    log(f"[episode] {n_ev} events from {len(ep_out)} episodes; {len(probs)} id problems")
    for p in probs[:15]:
        log(f"    {p}")
    if stop == 1:
        return {}

    stage("Building the character bible", 0.25)
    series = understand.series_pass(eps, ep_out, llm, out)
    miss = understand.unmapped_speakers(eps, series)
    log(f"[series] {len(series.get('characters', []))} characters; unmapped speaker labels: {list(miss.items())[:10]}")
    if stop == 2:
        return {}

    stage("Cutting candidate clips", 0.3)
    cands = candidates.build(eps, ep_out, cfg)
    (out / "candidates.json").write_text(
        json.dumps(candidates.to_dicts(cands), ensure_ascii=False, indent=1), encoding="utf-8")
    pools = {p: sum(1 for c in cands if c.pool == p) for p in "ABC"}
    log(f"[candidates] {len(cands)} (pool A {pools['A']}, pool B {pools['B']}, pool C {pools['C']}); "
        f"flagged {sum(1 for c in cands if c.flags)}")
    if stop == 3:
        return {}

    stage(f"Scoring {len(cands)} clips", 0.35)
    cdicts = candidates.to_dicts(cands)
    scores = score.score_all(cdicts, ep_out, series, llm, cfg, role="score", batch_size=6, workers=3)
    errs = {k: v["error"] for k, v in scores.items() if "error" in v}
    issues = Counter(i.split(" ")[0] + " " + i.split(" ")[1] for v in scores.values()
                     for i in v.get("verify_issues", []))
    log(f"[score] {len(scores) - len(errs)} scored, {len(errs)} errors; evidence issues: {dict(issues)}")
    if stop == 4:
        return {}

    ok = [c for c in cdicts if "error" not in scores.get(c["cid"], {"error": 1})]

    def best(mode: str, n: int, res: dict) -> list[dict]:
        """Top n by mode total among candidates passing that mode's gates."""
        ranked = [(score.final_score(res.get(c["cid"]) or scores[c["cid"]], cfg, mode), c) for c in ok]
        return [c for (t, f), c in sorted(ranked, key=lambda x: (bool(x[0][1]), -x[0][0]))][:n]

    rescores: dict = {}
    if not fast:
        stage("Re-scoring the best clips with the stronger model", 0.75)
        # stronger model on the top of BOTH lists (2/3 acquisition, 1/3 engagement), no duplicates
        prelim = list({c["cid"]: c for c in best("acquisition", RESCORE_TOP * 2 // 3, {})
                       + best("engagement", RESCORE_TOP, {})}.values())[:RESCORE_TOP]
        # free-tier Flash is often overloaded; cap the wait and keep cheap-model scores for the rest
        llm.deadline = time.time() + cfg["promo"].get("rescore_budget_sec", 120)
        try:
            rescores = score.score_all(prelim, ep_out, series, llm, cfg, role="rescore", batch_size=6, workers=2,
                                       group=False)
        finally:
            llm.deadline = None
        rescores = {k: v for k, v in rescores.items() if "error" not in v}
        log(f"[rescore] {len(rescores)}/{len(prelim)} re-scored by the stronger model"
            + ("" if len(rescores) == len(prelim) else " (time budget hit; the rest keep their first-pass scores)"))
    if stop == 5:
        return {}

    stage("Checking for spoilers", 0.88)
    title = next(folder.rglob("*.txt")).parent.name
    to_check = best("acquisition", cfg["promo"]["spoiler_check_top"], rescores)
    spoilers = score.spoiler_check(to_check, series, title, llm)
    n_sp = sum(score.is_spoiler(v) for v in spoilers.values())
    log(f"[spoiler] checked {len(spoilers)} acquisition candidates; {n_sp} give away a later twist")

    stage("Ranking", 0.93)
    selected_by_mode, rows = score.rank(cdicts, scores, rescores, cfg, spoilers)
    selected = selected_by_mode["acquisition"]
    for mode, sel in selected_by_mode.items():
        log(f"[rank:{mode}] {len(sel)} selected from {len({s['episode'] for s in sel})} episodes; "
            f"pools {dict(Counter(s['pool'] for s in sel))}")

    top = selected[: cfg["promo"]["explain_top"]]
    if top and not fast:
        stage("Writing 'why post first' notes", 0.96)
        system = (ROOT / "prompts" / "post_first.md").read_text(encoding="utf-8")
        user = "\n".join(f"{s['rank']}. {s['cid']} ep{s['episode']} {s['duration']:.0f}s | hook: "
                         f"{s['result'].get('hook_line')} | cliffhanger: {s['result'].get('cliffhanger_line')} | "
                         f"standalone gap: {(s['result'].get('standalone') or {}).get('missing_context')}"
                         for s in top)
        llm.deadline = time.time() + 60
        try:
            lines = llm.chat_json("rescore", system, user, max_tokens=3000, tag="post_first").get("lines", [])
            by = {l.get("cid"): l.get("post_first") for l in lines}
            for s in top:
                s["post_first"] = by.get(s["cid"])
        except Exception as e:  # blurbs are optional
            log(f"[post_first] skipped: {str(e)[:120]}")
        finally:
            llm.deadline = None

    llm_errors = {"scoring errors": len(errs), "evidence issues": dict(issues), "episode id problems": len(probs)}
    report.write(out, selected_by_mode, rows, llm_errors)
    secs = time.time() - t0
    log(f"[report] wrote {out / 'promos.md'} and promos.json")
    log(f"done in {secs:.0f}s")
    stage("Done", 1.0)
    return {"episodes": len(eps), "candidates": len(cands), "seconds": round(secs), "spoilers": n_sp,
            "selected": {m: len(s) for m, s in selected_by_mode.items()}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, default=Path("output"))
    ap.add_argument("--episodes")
    ap.add_argument("--until", choices=STAGES, default="report")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--pools", help='override candidate pools, e.g. "A,B:duration" (v1) or "A,B:hooks,C" (v2)')
    a = ap.parse_args()
    cfg = load_config()
    if a.pools:
        cfg["promo"]["pools"] = {k: (v or True) for k, _, v in (x.partition(":") for x in a.pools.split(","))}
    run_pipeline(a.folder, a.out, cfg, LLM(cfg), ep_range(a.episodes), a.fast, a.until)
    return 0


if __name__ == "__main__":
    sys.exit(main())
