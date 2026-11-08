"""Per-label outcome for each ablation variant: python -m tests.per_label"""
import json
from pathlib import Path

from eval.evaluate import metrics

ROOT = Path(__file__).resolve().parent.parent
labels = json.loads((ROOT / "eval" / "labels.json").read_text(encoding="utf-8"))["labels"]
for v in ["v1", "v1_PoolC", "v1_hooksearch", "v2"]:
    rows = json.loads((ROOT / "output" / "ablation" / v / "all_candidates_scored.json").read_text(encoding="utf-8"))
    m = metrics(rows, labels)
    print(f"== {v}")
    for lab, i, r, where in m["per_label"]:
        if i is None:
            continue
        print(f"  ep{lab['episode']:>2} {lab.get('set','dev'):5} IoU {i:.2f} {r['cid'] if r else '-':18} "
              f"tot {r['total'] if r else 0:.2f} {where}")
print("\n== fresh-episode candidates in v2")
rows = json.loads((ROOT / "output" / "ablation" / "v2" / "all_candidates_scored.json").read_text(encoding="utf-8"))
for ep in (22, 33, 48):
    for r in sorted((x for x in rows if x["episode"] == ep), key=lambda x: x["start"]):
        s = "/".join(str((r["result"].get(k) or {}).get("score", "-")) for k in ["hook", "cliffhanger", "standalone", "coherence", "emotion"])
        print(f"  {r['cid']:18} {r['start']:6.1f}-{r['end']:6.1f} {s} tot {r['total']:.2f} {r['status']}")
