"""Print stage outputs for eyeballing: python -m tests.show <out_dir> episodes|series|candidates"""
import json
import sys
from pathlib import Path

out, what = Path(sys.argv[1]), sys.argv[2]

if what == "episodes":
    d = json.loads((out / "episodes.json").read_text(encoding="utf-8"))
    for k, r in d.items():
        print(f"=== EP {k}  ending={r.get('ending_type')}  Q: {r.get('unresolved_question')}")
        print("  summary:", r.get("summary"))
        print("  chars:", [(c.get("label"), c.get("name")) for c in r.get("characters", [])])
        for e in r["events"]:
            print(f"  {e['event_id']} {e['type']:<13} int={e['dramatic_intensity']} promo={e['promo_potential']} "
                  f"{e['start_id']}..{e['end_id']} hooks={e['hook_options']} ends={e['end_options']}")
            print(f"      {e['description']}  | backstory: {e.get('needs_backstory')}")
elif what == "series":
    d = json.loads((out / "series.json").read_text(encoding="utf-8"))
    print("PREMISE:", d.get("premise"))
    for a in d.get("arcs", []):
        print("  arc:", a)
    for c in d.get("characters", []):
        print(f"  {c['character_id']} {c['canonical_name']:<22} {c.get('importance','')[:5]:<6} "
              f"labels={c.get('labels')}  reveal={c.get('reveal','')[:60]}")
elif what == "candidates":
    d = json.loads((out / "candidates.json").read_text(encoding="utf-8"))
    for c in d[: int(sys.argv[3]) if len(sys.argv) > 3 else 999]:
        print(f"{c['cid']} ep{c['episode']} pool {c['pool']} {c['start']:6.2f}-{c['end']:6.2f} "
              f"({c['duration']:.1f}s) {c['start_id']}..{c['end_id']} gap={c['max_gap']} {c['flags']}")
