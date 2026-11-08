"""Parse Kuku screenplay .txt files into time-aligned elements.

Every CAMERA / ACTION / SOUND FX / dialogue block becomes one element with a
stable ID (E01_015 = episode 1, element 15). Later stages refer to elements by
ID only; timestamps are always looked up here, never produced by an LLM.

Usage:  python -m src.parse <input_folder> [--out parsed]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, asdict, field
from pathlib import Path

TIME_RE = r"(\d{1,2}:)?\d{1,2}:\d{2}(?:\.\d+)?"
BLOCK_LABEL_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")
START_END_RE = re.compile(rf"^Start:\s*\(({TIME_RE})\)\s*\|\s*End:\s*\(({TIME_RE})\)\s*$")
SCENE_RE = re.compile(r"^SCENE\s+(\d+)\s*$")
HEADER_RE = re.compile(r"^(EPISODE NUMBER|EPISODE LOGLINE|CONTINUITY NOTES):\s*(.*)$")
NON_DIALOGUE = {"CAMERA": "CAM", "ACTION": "ACT", "SOUND FX": "SFX"}
SCENE_TOL = 0.5  # seconds an element may spill past its scene bounds before we flag it


def to_sec(ts: str) -> float:
    parts = ts.split(":")
    sec = float(parts[-1]) + 60 * int(parts[-2])
    if len(parts) == 3:
        sec += 3600 * int(parts[0])
    return round(sec, 2)


def fmt(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:05.2f}"


@dataclass
class Element:
    id: str
    episode: int
    scene: int
    idx: int
    kind: str            # CAM | ACT | SFX | DLG
    start: float
    end: float
    text: str
    speaker: str | None = None   # raw on-screen label, e.g. "THE FORMER GROOM"
    tone: str | None = None      # e.g. "Cold, Commanding"
    offscreen: bool = False      # (O.S.) / (V.O.)


@dataclass
class Scene:
    number: int
    heading: str
    start: float
    end: float


@dataclass
class Episode:
    episode: int
    source_file: str
    logline: str
    continuity: str
    duration: float
    scenes: list[Scene] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_label(label: str) -> tuple[str, str | None, str | None, bool]:
    """'MATRIARCH (O.S.) (Firm)' -> ('DLG', 'MATRIARCH', 'Firm', True)."""
    label = label.strip()
    if label.upper() in NON_DIALOGUE:
        return NON_DIALOGUE[label.upper()], None, None, False
    parens = re.findall(r"\(([^)]*)\)", label)
    speaker = re.sub(r"\s*\([^)]*\)", "", label).strip()
    offscreen = any(p.strip().upper() in {"O.S.", "V.O.", "O.C."} for p in parens)
    tones = [p.strip() for p in parens if p.strip().upper() not in {"O.S.", "V.O.", "O.C."}]
    return "DLG", speaker, (", ".join(tones) or None), offscreen


def parse_file(path: Path) -> Episode:
    lines = path.read_text(encoding="utf-8").splitlines()
    header: dict[str, str] = {}
    scenes: list[Scene] = []
    elements: list[Element] = []
    warnings: list[str] = []

    cur_scene: dict | None = None
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if m := HEADER_RE.match(line):
            header[m.group(1)] = m.group(2).strip()
        elif m := SCENE_RE.match(line):
            cur_scene = {"number": int(m.group(1)), "heading": "", "start": None, "end": None}
            scenes.append(cur_scene)  # filled in as the next lines arrive
        elif cur_scene is not None and line.lower().startswith("scene heading:"):
            cur_scene["heading"] = line.split(":", 1)[1].strip()
        elif cur_scene is not None and line.lower().startswith("scene start:"):
            cur_scene["start"] = to_sec(line.split(":", 1)[1].strip())
        elif cur_scene is not None and line.lower().startswith("scene end:"):
            cur_scene["end"] = to_sec(line.split(":", 1)[1].strip())
        elif m := BLOCK_LABEL_RE.match(line):
            label = m.group(1)
            se = START_END_RE.match(lines[i + 1].strip()) if i + 1 < len(lines) else None
            if not se:
                warnings.append(f"line {i+1}: block '{label}' has no Start/End line")
                i += 1
                continue
            start, end = to_sec(se.group(1)), to_sec(se.group(3))
            j, body = i + 2, []
            while j < len(lines) and lines[j].strip() and not BLOCK_LABEL_RE.match(lines[j].strip()) \
                    and not lines[j].strip().startswith("--- SCRIPT"):
                body.append(lines[j].strip())
                j += 1
            text = " ".join(body).strip()
            kind, speaker, tone, offscreen = parse_label(label)
            if kind == "DLG":
                text = text.strip('"').strip("“”")
            if not text:
                warnings.append(f"line {i+1}: block '{label}' has empty text")
            elements.append(Element(
                id="", episode=0, scene=cur_scene["number"] if cur_scene else 0, idx=0,
                kind=kind, start=start, end=end, text=text,
                speaker=speaker, tone=tone, offscreen=offscreen))
            i = j
            continue
        i += 1

    ep_no = header.get("EPISODE NUMBER", "")
    m = re.search(r"(\d+)", ep_no) or re.search(r"episode-(\d+)", path.name)
    episode = int(m.group(1)) if m else 0
    if not m:
        warnings.append("could not read episode number")

    scene_objs = [Scene(**s) for s in scenes]
    for n, el in enumerate(elements, 1):
        el.episode, el.idx, el.id = episode, n, f"E{episode:02d}_{n:03d}"

    duration = max([s.end for s in scene_objs if s.end is not None] + [e.end for e in elements] + [0.0])
    return Episode(
        episode=episode, source_file=path.name,
        logline=header.get("EPISODE LOGLINE", ""), continuity=header.get("CONTINUITY NOTES", ""),
        duration=duration, scenes=scene_objs, elements=elements, warnings=warnings)


def validate(ep: Episode) -> list[str]:
    """Structural checks. Returns problems; also stored on the episode."""
    probs: list[str] = []
    if not ep.logline:
        probs.append("missing logline")
    if not ep.scenes:
        probs.append("no scenes")
    if not ep.elements:
        probs.append("no elements")
    for a, b in zip(ep.scenes, ep.scenes[1:]):
        if a.end is None or b.start is None or abs(a.end - b.start) > SCENE_TOL:
            probs.append(f"scene {a.number}->{b.number} not continuous ({a.end} vs {b.start})")
    scene_by_no = {s.number: s for s in ep.scenes}
    prev_start = -1.0
    for el in ep.elements:
        if el.end < el.start:
            probs.append(f"{el.id}: end before start")
        if el.start + 0.01 < prev_start - 3.0:  # small reorderings are normal (overlapping cues)
            probs.append(f"{el.id}: starts {prev_start - el.start:.1f}s before previous element")
        prev_start = max(prev_start, el.start)
        sc = scene_by_no.get(el.scene)
        if sc and sc.start is not None and sc.end is not None:
            if el.start < sc.start - SCENE_TOL or el.end > sc.end + SCENE_TOL:
                probs.append(f"{el.id}: outside scene {sc.number} [{fmt(sc.start)}-{fmt(sc.end)}]")
    ep.warnings.extend(probs)
    return probs


def compact(el: Element) -> str:
    """One-line rendering sent to LLMs: E01_015 [00:11.30-00:13.10] DLG MATRIARCH (Cold): "..." """
    who = ""
    if el.kind == "DLG":
        who = f" {el.speaker}" + (" (O.S.)" if el.offscreen else "") + (f" ({el.tone})" if el.tone else "") + ":"
        body = f'"{el.text}"'
    else:
        body = el.text
    return f"{el.id} [{fmt(el.start)}-{fmt(el.end)}] {el.kind}{who} {body}"


def load_folder(folder: Path) -> list[Episode]:
    files = sorted(p for p in folder.rglob("*.txt"))
    # skip files that are not screenplays (no timed blocks), e.g. a README inside an uploaded zip
    eps = [e for e in (parse_file(p) for p in files) if e.elements]
    eps.sort(key=lambda e: e.episode)
    for ep in eps:
        validate(ep)
    return eps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--out", type=Path, default=Path("parsed"))
    args = ap.parse_args()

    eps = load_folder(args.folder)
    if not eps:
        print(f"No .txt files under {args.folder}")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    for ep in eps:
        (args.out / f"ep_{ep.episode:03d}.json").write_text(
            json.dumps(asdict(ep), ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- report ----
    nums = [e.episode for e in eps]
    missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
    dupes = [n for n, c in Counter(nums).items() if c > 1]
    kinds = Counter(el.kind for e in eps for el in e.elements)
    speakers = Counter(el.speaker for e in eps for el in e.elements if el.kind == "DLG")
    durs = sorted(e.duration for e in eps)
    print(f"episodes: {len(eps)}  (range {min(nums)}-{max(nums)}; missing {missing or 'none'}; dupes {dupes or 'none'})")
    print(f"elements: {sum(kinds.values())}  {dict(kinds)}")
    print(f"duration: min {fmt(durs[0])}  median {fmt(durs[len(durs)//2])}  max {fmt(durs[-1])}  "
          f"total {sum(durs)/60:.1f} min")
    print(f"distinct speaker labels: {len(speakers)}  top: {speakers.most_common(8)}")
    bad = [(e.episode, w) for e in eps for w in e.warnings]
    print(f"warnings: {len(bad)}")
    for ep_no, w in bad[:40]:
        print(f"  ep {ep_no}: {w}")
    print(f"\nwrote {len(eps)} files to {args.out}/")
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
