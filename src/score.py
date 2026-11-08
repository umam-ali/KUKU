"""Stages 6-8: pointwise scoring (cheap model), re-score of the top (stronger model),
code checks on the evidence, gates, weighted rank, dedupe.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from .llm import LLM, LLMError, ROOT
from .parse import Episode

CRITERIA = ["hook", "cliffhanger", "standalone", "coherence", "emotion"]


def whos_who(ep_out: dict, series: dict, episode: int) -> str:
    """Episode-level label->name (trusted for that episode) plus the series descriptor."""
    canon = {c.get("canonical_name", "").split()[0].lower(): c for c in series.get("characters", [])
             if c.get("canonical_name")}
    lines = []
    for ch in ep_out.get(episode, {}).get("characters", []):
        name = ch.get("name") or ch.get("label")
        desc = ch.get("descriptor", "")
        c = canon.get(str(name).split()[0].lower()) if name else None
        if c and c.get("first_seen_as"):
            desc = f"{desc}; first introduced to viewers as {c['first_seen_as']}"
        lines.append(f"- {ch.get('label')} = {name}: {desc}")
    return "\n".join(lines) or "- (none extracted)"


def render(c: dict, who: str) -> str:
    return (f"### CANDIDATE {c['cid']} (episode {c['episode']}, {c['duration']:.1f}s)\n"
            f"WHO'S WHO:\n{who}\n\nHOOK WINDOW (first 3s):\n{c['hook_text']}\n\n"
            f"CLIP:\n{c['script']}\n\nENDING:\n{c['end_text']}\n\n"
            f"HIDDEN NEXT (not in promo):\n{c['hidden_next']}\n")


def verify(c: dict, r: dict, hook_sec: float) -> list[str]:
    """Check the judge's cited evidence against the clip. Penalise unsupported scores."""
    issues = []
    clip_ids = set(c["element_ids"])
    hook_ids = {line.split(" ", 1)[0] for line in c["hook_text"].splitlines() if line}
    end_ids = {line.split(" ", 1)[0] for line in c["end_text"].splitlines() if line}
    h = r.get("hook", {})
    hid = set(h.get("evidence_ids") or [])
    if not hid or not hid <= hook_ids:
        issues.append(f"hook evidence {sorted(hid - hook_ids) or 'missing'} not in first {hook_sec:g}s")
        h["score"] = min(h.get("score", 1), 2)
    ch = r.get("cliffhanger", {})
    cid_ = set(ch.get("evidence_ids") or [])
    if not cid_ or not cid_ <= clip_ids:
        issues.append(f"cliffhanger evidence {sorted(cid_ - clip_ids) or 'missing'} not in clip")
        ch["score"] = min(ch.get("score", 1), 2)
    elif not cid_ & (end_ids | set(c["element_ids"][-6:])):
        issues.append("cliffhanger evidence is not near the end of the clip")
        ch["score"] = min(ch.get("score", 1), 3)
    if ch.get("type") in ("artificial", "none"):
        ch["score"] = min(ch.get("score", 1), 2)
    return issues


def _normalize(r: dict) -> dict:
    """Coerce each criterion to {"score": int, ...}: models sometimes return 4, "4" or [..] instead."""
    for k in CRITERIA:
        v = r.get(k)
        if isinstance(v, list):
            v = next((x for x in v if isinstance(x, dict)), {"score": v[0]} if v else {})
        if isinstance(v, (int, float, str)):
            v = {"score": v}
        if not isinstance(v, dict):
            v = {}
        try:
            v["score"] = max(1, min(5, int(float(v.get("score", 0)))))
        except (TypeError, ValueError):
            v["score"] = 1
        r[k] = v
    return r


def _score_batch(llm: LLM, role: str, system: str, batch: list[dict], whos: dict, tag: str) -> dict:
    user = "\n\n".join(render(c, whos[c["episode"]]) for c in batch)
    try:
        out = llm.chat_json(role, system, user, max_tokens=900 * len(batch) + 1000, tag=tag)
    except (LLMError, json.JSONDecodeError) as e:
        return {c["cid"]: {"error": str(e)[:200]} for c in batch}
    if isinstance(out, list):  # a bare list instead of {"results": [...]}
        out = {"results": out}
    results = [_normalize(r) for r in out.get("results", []) if isinstance(r, dict)]
    res = {str(r.get("cid", "")).strip(): r for r in results}
    if len(results) == len(batch) and not all(c["cid"] in res for c in batch):
        # the model mangled the IDs but returned one result per candidate, in order (the prompt asks for that)
        res = {c["cid"]: r for c, r in zip(batch, results)}
    return {c["cid"]: res.get(c["cid"], {"error": "missing from model output"}) for c in batch}


def score_all(cands: list[dict], ep_out: dict, series: dict, llm: LLM, cfg: dict,
              role: str, batch_size: int, workers: int, group: bool = True) -> dict:
    system = (ROOT / "prompts" / "score.md").read_text(encoding="utf-8")
    whos = {e: whos_who(ep_out, series, e) for e in {c["episode"] for c in cands}}
    ordered = sorted(cands, key=lambda c: c["cid"])
    if group:
        # batch by (episode, pool), sorted by stable ID: changing one pool leaves the others' cache intact
        groups: dict = {}
        for c in ordered:
            groups.setdefault((c["episode"], c["pool"]), []).append(c)
        batches = [g[i:i + batch_size] for g in groups.values() for i in range(0, len(g), batch_size)]
    else:
        # full batches: for scarce models (Flash: 20 calls/day), fewer calls matters more than cache reuse
        batches = [ordered[i:i + batch_size] for i in range(0, len(ordered), batch_size)]
    out: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(lambda b: _score_batch(llm, role, system, b, whos,
                                                    f"{role}:{b[0]['cid']}-{b[-1]['cid']}"), batches):
            out.update(part)
        # anything the batch lost (bad JSON, dropped from the answer) is re-scored on its own
        failed = [c for c in cands if "error" in out.get(c["cid"], {"error": 1})]
        if failed and group:  # not for the scarce re-score model: those keep their cheap-model score
            for part in pool.map(lambda c: _score_batch(llm, role, system, [c], whos, f"{role}:{c['cid']}:single"),
                                 failed):
                out.update(part)
    hook_sec = cfg["promo"]["hook_sec"]
    by_id = {c["cid"]: c for c in cands}
    for cid, r in out.items():
        if "error" not in r:
            r["verify_issues"] = verify(by_id[cid], r, hook_sec)
    return out


def final_score(r: dict, cfg: dict, mode: str = "acquisition") -> tuple[float, list[str]]:
    m = cfg["promo"]["modes"][mode]
    s = {k: (r.get(k) or {}).get("score", 0) or 0 for k in CRITERIA}
    failed = [k for k, mn in m["gates"].items() if s.get(k, 0) < mn]
    total = sum(w * s[k] for k, w in m["weights"].items())
    return round(total, 3), failed


def spoiler_check(cands: list[dict], series: dict, title: str, llm: LLM, batch_size: int = 8) -> dict:
    """Separate pass (does not touch the scoring prompts, so ablations stay comparable).
    The premise (title + first episodes) is allowed; later twists are flagged with evidence."""
    system = (ROOT / "prompts" / "spoiler.md").read_text(encoding="utf-8")
    reveals = [f"- {c['canonical_name']}: {c['reveal']}" for c in series.get("characters", []) if c.get("reveal")]
    head = (f"SHOW TITLE: {title}\nPREMISE: {series.get('premise', '')}\n"
            f"REVEALS:\n" + ("\n".join(reveals) or "- (none recorded)") + "\n\n")
    out: dict = {}
    ordered = sorted(cands, key=lambda c: c["cid"])
    for i in range(0, len(ordered), batch_size):
        batch = ordered[i:i + batch_size]
        clips = "\n\n".join(
            f"### CLIP {c['cid']} (episode {c['episode']})\n" +
            "\n".join(l for l in c["script"].splitlines() if " DLG " in l or " ACT " in l) for c in batch)
        try:
            res = llm.chat_json("score", system, head + clips, max_tokens=300 * len(batch) + 500,
                                tag=f"spoiler:{batch[0]['cid']}")
            res = {"results": res} if isinstance(res, list) else res
            items = [r for r in res.get("results", []) if isinstance(r, dict)]
            got = {str(r.get("cid", "")).strip(): r for r in items}
            if len(items) == len(batch) and not all(c["cid"] in got for c in batch):
                got = {c["cid"]: r for c, r in zip(batch, items)}  # mangled IDs: match by order
        except (LLMError, json.JSONDecodeError) as e:
            got = {c["cid"]: {"error": str(e)[:150]} for c in batch}
        for c in batch:
            r = got.get(c["cid"], {"error": "missing"})
            if r.get("spoils") and not set(r.get("evidence_ids") or []) <= set(c["element_ids"]):
                r["spoils"], r["unsupported"] = False, True  # cited evidence not in the clip: ignore
            out[c["cid"]] = r
    return out


def is_spoiler(sp: dict | None) -> bool:
    return bool(sp and sp.get("spoils") and not sp.get("in_premise") and (sp.get("severity") or 0) >= 4)


def overlap(a: dict, b: dict) -> float:
    """Shared time as a fraction of the shorter clip (same episode only)."""
    if a["episode"] != b["episode"]:
        return 0.0
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return inter / min(a["duration"], b["duration"])


def rank(cands: list[dict], scores: dict, rescores: dict, cfg: dict,
         spoilers: dict | None = None) -> tuple[dict, list[dict]]:
    """Rank every candidate twice, once per mode (acquisition: strangers; engagement: lapsed viewers).
    Returns ({mode: selected top N}, all rows). Row fields 'status'/'total'/'rank' = acquisition."""
    p = cfg["promo"]
    spoilers = spoilers or {}
    rows = []
    for c in cands:
        r = rescores.get(c["cid"]) or scores.get(c["cid"]) or {"error": "not scored"}
        rows.append(dict(c, judge="rescore" if c["cid"] in rescores else "score", result=r,
                         spoiler=spoilers.get(c["cid"])))

    selected_by_mode = {}
    for mode, m in p["modes"].items():
        for row in rows:
            if "error" in row["result"]:
                total, failed = 0.0, ["error"]
            else:
                total, failed = final_score(row["result"], cfg, mode)
                if is_spoiler(row["spoiler"]) and m.get("spoiler_penalty"):
                    total = round(total - m["spoiler_penalty"], 3)
            row[mode] = {"total": total, "failed_gates": failed}
        order = sorted(rows, key=lambda x: (bool(x[mode]["failed_gates"]), -x[mode]["total"]))
        selected, per_ep = [], {}
        for row in order:
            st = row[mode]
            if st["failed_gates"]:
                st["status"] = "rejected: " + ",".join(st["failed_gates"])
            elif (dup := next((s for s in selected if overlap(row, s) > p["dedupe_overlap"]), None)):
                st["status"] = f"duplicate of {dup['cid']}"
            elif per_ep.get(row["episode"], 0) >= p["max_promos_per_episode"]:
                st["status"] = "episode cap"
            elif len(selected) >= p["top_n"]:
                st["status"] = "passed, below top N"
            else:
                st["status"] = "selected"
                st["rank"] = len(selected) + 1
                per_ep[row["episode"]] = per_ep.get(row["episode"], 0) + 1
                selected.append(row)
        selected_by_mode[mode] = selected

    for row in rows:  # back-compatible top-level fields = acquisition mode
        acq = row["acquisition"]
        row.update(total=acq["total"], failed_gates=acq["failed_gates"], status=acq["status"])
        if "rank" in acq:
            row["rank"] = acq["rank"]
    return selected_by_mode, rows
