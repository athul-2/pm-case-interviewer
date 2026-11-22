"""Replay every candidate turn of a saved session against a live API model, printing old vs new replies.
Costs real money (about $0.002 a session on DeepSeek via OpenRouter, counted in the monthly cap). Not saved
to sessions/; --out writes the replayed session elsewhere, e.g. to score it with interviewer.audit.

  python3 -m interviewer.replay sessions/<file>.json [model_id] [--out replayed.json]
"""
import json
import sys
import time

from .agent import AgentInterview
from .casepack import load_all
args = sys.argv[1:]
out = args[args.index("--out") + 1] if "--out" in args else None
args = [a for i, a in enumerate(args) if a != "--out" and (i == 0 or args[i - 1] != "--out")]
path, mid = args[0], (args[1] if len(args) > 1 else "or:deepseek/deepseek-v4.1-flash")
s = json.load(open(path)); turns = s["turns"]
iv = AgentInterview(load_all()[s["case_id"]], s.get("level", "pm"), mid); iv.open()
for i, t in enumerate(turns):
    if t["speaker"] != "Candidate": continue
    old = next((x["text"] for x in turns[i+1:] if x["speaker"] == "Interviewer"), "")
    t0 = time.time(); ev = list(iv.respond(t["text"]))
    new = " ".join(e.text for e in ev if e.kind == "say"); ex = [e.exhibit_id for e in ev if e.kind == "exhibit"]
    meta = iv.turns[-2].meta or {}
    print(f"C:   {t['text'][:150]}\nOLD: {old[:200]}\nNEW: {new}" + (f"  [table {ex}]" if ex else "")
          + (f"  [review trimmed]" if meta.get("draft") else "") + f"  ({time.time()-t0:.1f}s)\n")
    if iv.done: break
print("help logged:", [(i['kind'], i['target']) for i in iv.interventions if i['kind'] != 'clarification'], "| twist", iv.twist_id, "| done", iv.done)
print(f"cost ${iv.cost:.5f}")
if out:
    d = iv.to_dict()
    d.update(source="simulated", replay_of=path)
    open(out, "w").write(json.dumps(d, indent=1))
    print("saved", out)
