"""Stage 2 + 3: per-episode understanding, then one series pass for the character bible.

The LLM only ever returns element IDs. Code checks every ID; bad IDs are dropped and
counted (they feed the "raw LLM error rate" in the evaluation).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .llm import LLM, LLMError, ROOT
from .parse import Episode, compact

PROMPTS = ROOT / "prompts"


def episode_text(ep: Episode) -> str:
    head = (f"EPISODE {ep.episode}  (duration {ep.duration:.1f}s)\n"
            f"LOGLINE: {ep.logline}\nCONTINUITY: {ep.continuity}\n\n")
    return head + "\n".join(compact(el) for el in ep.elements)


def _check_events(ep: Episode, out: dict) -> tuple[list[dict], list[str]]:
    """Keep only events whose IDs exist and are ordered; return (events, problems)."""
    order = {el.id: i for i, el in enumerate(ep.elements)}
    problems, kept = [], []
    for ev in out.get("events", []):
        eid = ev.get("event_id", "?")
        bad = [x for x in [ev.get("start_id"), ev.get("end_id"), *ev.get("hook_options", []),
                           *ev.get("end_options", [])] if x not in order]
        if bad:
            problems.append(f"ep{ep.episode} {eid}: unknown ids {bad}")
        ev["hook_options"] = [x for x in ev.get("hook_options", []) if x in order]
        ev["end_options"] = [x for x in ev.get("end_options", []) if x in order]
        if ev.get("start_id") not in order or ev.get("end_id") not in order:
            problems.append(f"ep{ep.episode} {eid}: dropped (start/end id invalid)")
            continue
        if order[ev["start_id"]] > order[ev["end_id"]]:
            ev["start_id"], ev["end_id"] = ev["end_id"], ev["start_id"]
            problems.append(f"ep{ep.episode} {eid}: start/end swapped")
        if not ev["hook_options"]:
            ev["hook_options"] = [ev["start_id"]]
        if not ev["end_options"]:
            ev["end_options"] = [ev["end_id"]]
        ev["episode"] = ep.episode
        ev["event_uid"] = f"ep{ep.episode:02d}_{eid}"
        kept.append(ev)
    return kept, problems


def episode_pass(eps: list[Episode], llm: LLM, out_dir: Path, workers: int = 3) -> dict:
    system = (PROMPTS / "episode.md").read_text(encoding="utf-8")

    def one(ep: Episode) -> dict:
        try:
            out = llm.chat_json("episode", system, episode_text(ep), max_tokens=3000, tag=f"episode:{ep.episode}")
        except (LLMError, json.JSONDecodeError) as e:
            return {"episode": ep.episode, "error": str(e), "events": [], "problems": [f"ep{ep.episode}: {e}"]}
        events, problems = _check_events(ep, out)
        out.update(episode=ep.episode, events=events, problems=problems)
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, eps))
    by_ep = {r["episode"]: r for r in results}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "episodes.json").write_text(json.dumps(by_ep, ensure_ascii=False, indent=1), encoding="utf-8")
    return by_ep


def series_pass(eps: list[Episode], ep_out: dict, llm: LLM, out_dir: Path) -> dict:
    system = (PROMPTS / "series.md").read_text(encoding="utf-8")
    parts = []
    for ep in eps:
        r = ep_out.get(ep.episode, {})
        chars = "; ".join(f"{c.get('label')} -> {c.get('name')} ({c.get('descriptor')})"
                          for c in r.get("characters", []))
        parts.append(f"EP {ep.episode}\nLOGLINE: {ep.logline}\nCONTINUITY: {ep.continuity}\n"
                     f"SUMMARY: {r.get('summary', '')}\nCHARACTERS: {chars}\n")
    out = llm.chat_json("series", system, "\n".join(parts), max_tokens=8000, tag="series")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "series.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def label_map(series: dict) -> dict[str, dict]:
    """On-screen speaker label (upper-case) -> canonical character entry."""
    m = {}
    for ch in series.get("characters", []):
        for lab in ch.get("labels", []) + [ch.get("canonical_name", "")]:
            if lab:
                m[lab.strip().upper()] = ch
    return m


def unmapped_speakers(eps: list[Episode], series: dict) -> dict[str, int]:
    """Speaker labels the bible does not cover, with line counts (for human review)."""
    lm, miss = label_map(series), {}
    for ep in eps:
        for el in ep.elements:
            if el.kind == "DLG" and el.speaker and el.speaker.upper() not in lm:
                miss[el.speaker] = miss.get(el.speaker, 0) + 1
    return dict(sorted(miss.items(), key=lambda kv: -kv[1]))
