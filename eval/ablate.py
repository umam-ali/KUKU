"""Ablation: which candidate-generation change actually helped?

  python -m eval.ablate            runs 4 variants in --fast mode (cheap model only), writes eval/ablation.md

Every variant uses the same cached episode/series passes, the same scoring prompt and the
same ranking, so differences come only from the candidate pools.
"""
from __future__ import annotations

import json
import statistics as st
import subprocess
import sys
from pathlib import Path

from eval.evaluate import metrics

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = [
    ("v1", "A,B:duration", "event windows + episode endings (start chosen by length)"),
    ("v1 + Pool C", "A,B:duration,C", "+ episode openings"),
    ("v1 + hook search", "A,B:hooks", "+ ending windows start on a hook line"),
    ("v2", "A,B:hooks,C", "both changes"),
]


def main() -> int:
    labels = json.loads((ROOT / "eval" / "labels.json").read_text(encoding="utf-8"))["labels"]
    sets = {"dev": [l for l in labels if l.get("set") != "fresh"],
            "fresh": [l for l in labels if l.get("set") == "fresh"]}
    L = ["# Ablation (fast mode: cheap model only, same prompts and ranking)", "",
         "Dev = the 10 episodes that exposed v1's failures (v2 was designed after seeing them, so gains there "
         "are optimistic). Fresh = 4 episodes labelled blind before v2 was built.", "",
         "| Variant | Pools | Candidates | Set | Recall | Ep top-3 | Acq top-20 | Eng top-20 | Boundary err | Weak eps OK | Acq top-20 pools |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, pools, desc in VARIANTS:
        out = ROOT / "output" / "ablation" / name.replace(" ", "").replace("+", "_")
        print(f"=== {name}: {desc}", flush=True)
        r = subprocess.run([sys.executable, "-u", "run.py", "input", "--fast", "--pools", pools, "--out", str(out)],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        print(r.stdout[-1500:], r.stderr[-1500:], flush=True)
        if r.returncode != 0:
            L.append(f"| {name} | {pools} | run failed | | | | | | | | |")
            continue
        rows = json.loads((out / "all_candidates_scored.json").read_text(encoding="utf-8"))
        acq_pools = {}
        for x in rows:
            if (x.get("acquisition") or {}).get("status") == "selected":
                acq_pools[x["pool"]] = acq_pools.get(x["pool"], 0) + 1
        for set_name, labs in sets.items():
            m = metrics(rows, labs)
            n = m["n"] or 1
            L.append(f"| {name} | {pools} | {len(rows)} | {set_name} | {m['recall']}/{m['n']} | {m['top3']}/{m['n']} | "
                     f"{m['acq_hit']}/{m['n']} | {m['eng_hit']}/{m['n']} | "
                     f"{(st.mean(m['berr']) if m['berr'] else float('nan')):.1f}s | {m['weak_agree']}/{m['weak_n']} | "
                     f"{acq_pools if set_name == 'dev' else ''} |")
    (ROOT / "eval" / "ablation.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
