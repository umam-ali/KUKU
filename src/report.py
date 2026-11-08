"""Stage 9: write promos.json (machine) and promos.md (editor) plus run stats."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .llm import CACHE
from .parse import fmt


def _r(row: dict, k: str) -> dict:
    return (row.get("result") or {}).get(k) or {}


def promo_record(row: dict, mode: str = "acquisition") -> dict:
    sp = row.get("spoiler") or {}
    flags = row.get("flags", []) + row["result"].get("verify_issues", [])
    if sp.get("spoils") and not sp.get("in_premise") and (sp.get("severity") or 0) >= 4:
        flags.append(f"spoiler: {sp.get('reveal')} (severity {sp.get('severity')})")
    return {
        "mode": mode, "rank": row[mode].get("rank"), "total": row[mode]["total"],
        "cid": row["cid"], "episode": row["episode"], "pool": row["pool"],
        "segments": [{"episode": row["episode"], "start": fmt(row["start"]), "end": fmt(row["end"]),
                      "start_sec": row["start"], "end_sec": row["end"],
                      "start_element": row["start_id"], "end_element": row["end_id"]}],
        "duration_sec": row["duration"],
        "hook": row["result"].get("hook_line"), "cliffhanger": row["result"].get("cliffhanger_line"),
        "cliffhanger_type": _r(row, "cliffhanger").get("type"),
        "why": row["result"].get("why"), "post_first": row.get("post_first"),
        "scores": {k: _r(row, k).get("score") for k in ["hook", "cliffhanger", "standalone", "coherence", "emotion"]},
        "judge": row["judge"],
        "missing_context": _r(row, "standalone").get("missing_context"),
        "flags": flags,
    }


def call_stats() -> dict:
    rows = [json.loads(l) for l in (CACHE / "calls.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("tag") != "ping" and r.get("model")]
    by_model: dict = {}
    for r in rows:
        m = by_model.setdefault(r.get("model"), {"calls": 0, "cached": 0, "errors": 0, "in": 0, "out": 0})
        if r.get("error"):
            m["errors"] += 1
            continue
        m["calls"] += 1
        m["cached"] += 1 if r.get("cache") else 0
        m["in"] += r.get("in") or 0
        m["out"] += r.get("out") or 0
    return by_model


POOL_NAMES = {"A": "event-driven", "B": "episode endings", "C": "episode openings"}
MODE_TITLES = {"acquisition": "Acquisition shortlist (ads for people who have never seen the show)",
               "engagement": "Engagement shortlist (bring back viewers who dropped off mid-series)"}


def _table(promos: list[dict]) -> list[str]:
    L = ["| # | Ep | Start-End | Sec | Pool | Hook (first 3s) | Cliffhanger | H/C/S/Coh/E | Total |",
         "|---|---|---|---|---|---|---|---|---|"]
    for p in promos:
        s, seg = p["scores"], p["segments"][0]
        L.append(f"| {p['rank']} | {p['episode']} | {seg['start']}-{seg['end']} | {p['duration_sec']:.0f} | {p['pool']} | "
                 f"{(p['hook'] or '').replace('|', '/')} | {(p['cliffhanger'] or '').replace('|', '/')} | "
                 f"{s['hook']}/{s['cliffhanger']}/{s['standalone']}/{s['coherence']}/{s['emotion']} | {p['total']:.2f} |")
    return L


def write(out: Path, selected_by_mode: dict, rows: list[dict], llm_errors: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    lists = {mode: [promo_record(s, mode) for s in sel] for mode, sel in selected_by_mode.items()}
    promos = lists["acquisition"]
    (out / "promos.json").write_text(json.dumps(promos, ensure_ascii=False, indent=1), encoding="utf-8")
    for mode, lst in lists.items():
        if mode != "acquisition":
            (out / f"promos_{mode}.json").write_text(json.dumps(lst, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "all_candidates_scored.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    gates = Counter(g for r in rows for g in r.get("failed_gates", []))
    pool_all = Counter(r["pool"] for r in rows)
    pool_pass = Counter(r["pool"] for r in rows if not r.get("failed_gates"))
    spoil = [r for r in rows if r.get("spoiler") and r["spoiler"].get("spoils") and not r["spoiler"].get("in_premise")
             and (r["spoiler"].get("severity") or 0) >= 4]
    L = ["# Promo shortlists", "",
         f"{len(rows)} candidates scored. Acquisition gate failures: {dict(gates)}", "",
         "| Pool | Candidates | Pass acquisition gates | In acquisition top 20 | In engagement top 20 |",
         "|---|---|---|---|---|"]
    for pool in sorted(pool_all):
        L.append(f"| {pool} ({POOL_NAMES.get(pool, pool)}) | {pool_all[pool]} | {pool_pass[pool]} | "
                 f"{sum(1 for p in promos if p['pool'] == pool)} | "
                 f"{sum(1 for p in lists.get('engagement', []) if p['pool'] == pool)} |")
    L += ["", f"Spoiler check: {sum(1 for r in rows if r.get('spoiler'))} acquisition candidates checked, "
          f"{len(spoil)} give away a later twist (penalised in acquisition, allowed in engagement).",
          "", f"LLM output problems: {llm_errors}", ""]
    for mode, lst in lists.items():
        L += [f"## {MODE_TITLES.get(mode, mode)}", "", *_table(lst), ""]
    L += ["## Acquisition: why each works / why post first", ""]
    for p in promos:
        L.append(f"**{p['rank']}. Ep {p['episode']} {p['segments'][0]['start']}-{p['segments'][0]['end']}** "
                 f"({p['cid']}, pool {p['pool']}, {p['cliffhanger_type']}): {p['why']}")
        if p.get("post_first"):
            L.append(f"  - Post-first: {p['post_first']}")
        if p.get("missing_context"):
            L.append(f"  - A stranger might miss: {p['missing_context']}")
        if p["flags"]:
            L.append(f"  - Flags: {'; '.join(p['flags'])}")
    L += ["", "## Model usage (cumulative call log, including development runs)", "",
          "| Model | Calls | Cached | Errors | Tokens in | Tokens out |",
          "|---|---|---|---|---|---|"]
    for m, v in call_stats().items():
        L.append(f"| {m} | {v['calls']} | {v['cached']} | {v['errors']} | {v['in']} | {v['out']} |")
    (out / "promos.md").write_text("\n".join(L) + "\n", encoding="utf-8")
