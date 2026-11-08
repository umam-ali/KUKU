"""Explain what happened to specific candidates / episodes: python -m tests.why output C056 C067 ... | ep21 ep29"""
import json
import sys
from pathlib import Path

rows = json.loads((Path(sys.argv[1]) / "all_candidates_scored.json").read_text(encoding="utf-8"))
by = {r["cid"]: r for r in rows}
sel = sorted((r for r in rows if r.get("status") == "selected"), key=lambda r: r["rank"])
print(f"shortlist cutoff total: {sel[-1]['total']:.2f}")
for arg in sys.argv[2:]:
    items = [by[arg]] if arg in by else [r for r in rows if f"ep{r['episode']}" == arg]
    for r in items:
        res = r.get("result") or {}
        s = "/".join(str((res.get(k) or {}).get("score", "-")) for k in ["hook", "cliffhanger", "standalone", "coherence", "emotion"])
        print(f"{r['cid']} ep{r['episode']} pool {r['pool']} {r['start']:.1f}-{r['end']:.1f} total {r['total']:.2f} "
              f"H/C/S/Coh/E {s} judge={r['judge']} -> {r.get('status')}")
        if (res.get("standalone") or {}).get("missing_context"):
            print("    missing:", res["standalone"]["missing_context"][:150])
        print("    cliff:", (res.get("cliffhanger") or {}).get("evidence", "")[:150])
