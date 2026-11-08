"""Stage 4: build candidate promo windows in code. No LLM here.

Pool A: event-driven windows (hook option x end option for each tagged event).
Pool B: the last 20-60s of every episode (episodes are written to end on cliffhangers).

Rules enforced here, not by the model:
- start/end snap to element boundaries; never start or end in the middle of a line of dialogue
- duration 20-60s (if out of range we keep the ENDING and move the start, because the
  cliffhanger is the harder thing to get right)
- each candidate carries: hook window (first N s), ending window, hidden continuation
  (next N s, shown to the judge but never part of the promo), and a gap flag
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .parse import Episode, Element, compact


@dataclass
class Candidate:
    cid: str
    episode: int
    pool: str                    # "A" event-driven | "B" episode ending
    event_uid: str | None
    start: float
    end: float
    start_id: str
    end_id: str
    element_ids: list[str] = field(default_factory=list)
    script: str = ""
    hook_text: str = ""
    end_text: str = ""
    hidden_next: str = ""
    max_gap: float = 0.0          # longest stretch with no dialogue/action/sound (camera only)
    flags: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)


def _dialogue_spanning(ep: Episode, t: float) -> Element | None:
    for el in ep.elements:
        if el.kind == "DLG" and el.start < t - 0.05 and el.end > t + 0.05:
            return el
    return None


def _snap(ep: Episode, t0: float, t1: float) -> tuple[float, float]:
    """Move the start back / end forward so no dialogue line is cut in half."""
    if (d := _dialogue_spanning(ep, t0)):
        t0 = d.start
    if (d := _dialogue_spanning(ep, t1)):
        t1 = d.end
    return round(t0, 2), round(t1, 2)


def _starts(ep: Episode) -> list[float]:
    """Legal start points: element starts that are not inside a dialogue line."""
    pts = sorted({el.start for el in ep.elements})
    return [p for p in pts if not _dialogue_spanning(ep, p)]


def _fit(ep: Episode, t0: float, t1: float, lo: float, hi: float) -> tuple[float, float] | None:
    """Keep the end fixed; move the start to the nearest legal point that makes lo<=dur<=hi."""
    if lo <= t1 - t0 <= hi:
        return t0, t1
    legal = [p for p in _starts(ep) if lo <= t1 - p <= hi]
    if not legal:
        return None
    return min(legal, key=lambda p: abs(p - t0)), t1


def _within(ep: Episode, a: float, b: float) -> list[Element]:
    return [el for el in ep.elements if el.end > a + 0.01 and el.start < b - 0.01]


def _max_gap(els: list[Element], t0: float, t1: float) -> float:
    spans = sorted((max(e.start, t0), min(e.end, t1)) for e in els if e.kind in ("DLG", "ACT", "SFX"))
    gap, cur = 0.0, t0
    for a, b in spans:
        gap = max(gap, a - cur)
        cur = max(cur, b)
    return round(max(gap, t1 - cur), 2)


def _first_id_at(ep: Episode, t: float) -> str:
    return min((el for el in ep.elements if el.start >= t - 0.01), key=lambda e: e.start).id


def _last_id_at(ep: Episode, t: float) -> str:
    return max((el for el in ep.elements if el.end <= t + 0.01), key=lambda e: e.end).id


def _iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union else 0.0


HOOK_TONES = ("shout", "scream", "furious", "enrag", "menac", "threat", "shock", "desperat", "terrif",
              "panic", "cruel", "sinister", "defiant", "accus", "gasp", "horrif", "alarm", "sob", "demand",
              "urgent", "outrag", "cold", "venom", "deadly", "chilling", "painful", "betray")


def _hookiness(el: Element) -> int:
    """Cheap, transparent guess at whether a dialogue line could open a promo (0 = no)."""
    tone = (el.tone or "").lower()
    score = 2 * any(t in tone for t in HOOK_TONES)
    score += el.text.rstrip().endswith("!") + el.text.rstrip().endswith("?")
    return score if score >= 2 else 0


def build(eps: list[Episode], ep_out: dict, cfg: dict) -> list[Candidate]:
    """pools come from cfg['promo']['pools'], e.g. {"A": true, "B": "hooks", "C": true}."""
    p = cfg["promo"]
    lo, hi = p["min_sec"], p["max_sec"]
    pools = {k: v for k, v in p.get("pools", {"A": True, "B": "duration"}).items() if v}
    caps = {"A": p["max_candidates_per_episode"],
            "B": 4 if pools.get("B") == "hooks" else 2,
            "C": 3}
    by_no = {e.episode: e for e in eps}
    out: list[Candidate] = []

    for ep in eps:
        idx = {el.id: el for el in ep.elements}
        raw: list[tuple[str, str | None, float, float, list[str]]] = []  # pool, event, t0, t1, flags

        # ---- Pool A: tagged hooks x tagged endings ----
        # A hook may pair with its own event's endings or with LATER events' endings in the
        # same episode (an editor stretches a short beat to the next unresolved moment).
        # The start is only moved (losing the hook) if no pairing fits 20-60s.
        events = ep_out.get(ep.episode, {}).get("events", [])
        pot = {ev["event_uid"]: ev.get("promo_potential") or 0 for ev in events}
        hooks = [(h, ev["event_uid"]) for ev in events for h in ev["hook_options"]]
        ends = [(e_, ev["event_uid"]) for ev in events for e_ in ev["end_options"]]
        pairs = []
        for h, hev in hooks:
            for e_, eev in ends:
                t0, t1 = _snap(ep, idx[h].start, idx[e_].end)
                if lo <= t1 - t0 <= hi:
                    # prefer strong events, own-event pairs, and 25-45s clips
                    pref = pot[hev] + pot[eev] + (1 if hev == eev else 0) - abs((t1 - t0) - 35) / 15
                    pairs.append((pref, hev, t0, t1, []))
        for ev in events:  # fallback for events with no fitting pair
            if any(p[1] == ev["event_uid"] for p in pairs):
                continue
            for h in ev["hook_options"][:1]:
                for e_ in ev["end_options"][-1:]:
                    t0, t1 = _snap(ep, idx[h].start, idx[e_].end)
                    if t1 > t0 and (fit := _fit(ep, t0, t1, lo, hi)):
                        pairs.append((pot[ev["event_uid"]] - 2, ev["event_uid"], fit[0], fit[1],
                                      [f"start moved {fit[0]-t0:+.1f}s to fit {lo}-{hi}s (hook may be lost)"]))
        for pref, ev_uid, t0, t1, flags in sorted(pairs, key=lambda x: -x[0]):
            raw.append(("A", ev_uid, t0, t1, flags))

        # ---- Pool B: episode endings ----
        t_end = max(el.end for el in ep.elements)
        legal = _starts(ep)
        if "B" in pools:
            if pools.get("B") == "hooks":
                # v2: backward hook search. Keep the designed ending, try openings that could
                # grip in 3s: tagged hook options first, then dialogue lines that read as hooks.
                tagged = sorted(((idx[h].start, pot[uid]) for h, uid in hooks), key=lambda x: -x[1])
                lines = sorted(((el.start, _hookiness(el)) for el in ep.elements
                                if el.kind == "DLG" and _hookiness(el) > 0), key=lambda x: -x[1])
                for s, _ in tagged + lines:
                    s, _e = _snap(ep, s, t_end)
                    if lo <= t_end - s <= hi:
                        raw.append(("B", None, s, t_end, []))
            for target in (30, 50):  # v1 behaviour (duration-based start), kept as a fallback
                pts = [s for s in legal if lo <= t_end - s <= hi]
                if pts:
                    s = min(pts, key=lambda x: abs((t_end - x) - target))
                    raw.append(("B", None, s, t_end, []))

        # ---- Pool C: episode openings (episodes are written to open hot) ----
        if "C" in pools:
            t_first = min(el.start for el in ep.elements)
            starts = [t_first] + [el.start for el in ep.elements
                                  if el.kind in ("DLG", "ACT", "SFX") and el.start <= t_first + 8][:1]
            tagged_ends = [idx[e_].end for e_, _ in ends]
            for s0 in dict.fromkeys(starts):
                ends_ok = sorted({_snap(ep, s0, e)[1] for e in tagged_ends
                                  if lo <= _snap(ep, s0, e)[1] - s0 <= hi})
                if not ends_ok:  # no tagged ending in range: nearest line end to ~35s
                    dl = [el.end for el in ep.elements if el.kind == "DLG" and lo <= el.end - s0 <= hi]
                    ends_ok = [min(dl, key=lambda e: abs(e - s0 - 35))] if dl else []
                for e in ends_ok:
                    raw.append(("C", None, s0, e, []))

        # ---- dedupe within episode (near-identical windows), apply caps ----
        kept: list[tuple] = []
        for r in raw:
            if r[0] not in pools:
                continue
            # same pool only (keeps the pool comparison fair); different openings survive
            if any(k[0] == r[0] and (abs(k[2] - r[2]) < 0.05 or _iou((r[2], r[3]), (k[2], k[3])) > 0.85)
                   for k in kept):
                continue
            if sum(1 for k in kept if k[0] == r[0]) >= caps[r[0]]:
                continue
            kept.append(r)

        for pool, ev_uid, t0, t1, flags in kept:
            els = _within(ep, t0, t1)
            hook = _within(ep, t0, t0 + p["hook_sec"])
            ending = _within(ep, t1 - p["end_window_sec"], t1)
            nxt = [el for el in ep.elements if t1 - 0.01 <= el.start < t1 + p["hidden_next_sec"]]
            nxt_txt = "\n".join(compact(el) for el in nxt)
            if not nxt and (nep := by_no.get(ep.episode + 1)):
                nxt_txt = f"(next episode {nep.episode} opens with)\n" + "\n".join(
                    compact(el) for el in nep.elements if el.start < p["hidden_next_sec"])
            c = Candidate(
                # stable ID from episode, pool and times: unchanged windows keep their cached scores
                cid=f"E{ep.episode:02d}{pool}_{round(t0 * 10):04d}_{round(t1 * 10):04d}",
                episode=ep.episode, pool=pool, event_uid=ev_uid,
                start=t0, end=t1, start_id=_first_id_at(ep, t0), end_id=_last_id_at(ep, t1),
                element_ids=[el.id for el in els], script="\n".join(compact(el) for el in els),
                hook_text="\n".join(compact(el) for el in hook),
                end_text="\n".join(compact(el) for el in ending),
                hidden_next=nxt_txt or "(series ends here)", max_gap=_max_gap(els, t0, t1), flags=flags)
            if c.max_gap > 6:
                c.flags.append(f"{c.max_gap:.1f}s with no dialogue/action")
            assert lo - 0.01 <= c.duration <= hi + 0.01, (c.cid, c.duration)
            out.append(c)
    return out


def to_dicts(cands: list[Candidate]) -> list[dict]:
    return [asdict(c) | {"duration": c.duration} for c in cands]
