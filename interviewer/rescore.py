"""Recompute verdicts for saved sessions without calling any model.

  python3 -m interviewer.rescore sessions/*.json            # print a table
  python3 -m interviewer.rescore sessions/x.json --level senior --write

Re-applies everything that is pure Python: the quote cap, disputed gates, hint penalties, gate caps,
weights and the verdict rollup, from the LLM outputs already stored in each report. Use it after
changing weights, caps or rollup rules in rubrics.py / evaluator.py (re-run the grader only when a
prompt or the model changes).
"""
import json
import sys
from pathlib import Path

from .casepack import load_all
from .evaluator import rollup
from .rubrics import dimensions, is_tradeoff


def rescore(session: dict, level: str | None = None) -> dict:
    r = session["report"]
    case = load_all()[session["case_id"]]
    level = level or r.get("level", "pm")
    weights = {d["key"]: d["w"] for d in dimensions(case.type_key, is_tradeoff(case))}
    dims = []
    for d in r["dimensions"]:
        d = dict(d, weight=weights[d["key"]], score=d["expected"], caps=[])
        if d["score"] > 2 and not d["quotes"]:
            d["score"] = 2.0
            d["caps"].append("no verbatim candidate quote: capped at 2")
        dims.append(d)
    by_key = {d["key"]: d for d in dims}
    gates = []
    for g in r["gates"]:
        g = dict(g)
        if g["triggered"] and not g.get("auto") and any(
                k in by_key and by_key[k]["expected"] >= 3 and by_key[k]["quotes"] for k in g["dim_cap"]):
            g["triggered"], g["disputed"] = False, True
        gates.append(g)
    roll = rollup(case.type_key, dims, gates, session.get("interventions", []), level)
    return {**r, "level": level, "dimensions": dims, "gates": gates, "rollup": roll,
            "verdict": roll["verdict"], "weighted_mean": roll["weighted_mean"]}


def main(argv):
    level = argv[argv.index("--level") + 1] if "--level" in argv else None
    write = "--write" in argv
    paths = [Path(a) for a in argv if a.endswith(".json")]
    for p in paths:
        s = json.loads(p.read_text())
        if "report" not in s or "dimensions" not in s["report"]:
            continue
        old = s["report"].get("verdict")
        new = rescore(s, level)
        fired = [g["id"] for g in new["gates"] if g["triggered"]]
        disputed = [g["id"] for g in new["gates"] if g.get("disputed")]
        print(f"{p.name:28} {s['case_type']:16} {old or '-':13} -> {new['verdict']:13} "
              f"mean {new['weighted_mean']:<5} gates {fired} disputed {disputed}")
        if write:
            s["report"] = new
            s["report"]["coaching"]["verdict"] = new["verdict"]
            p.write_text(json.dumps(s, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
