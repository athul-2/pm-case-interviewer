"""Grade saved sessions with another grader and compare with the report already stored (usually local Qwen3).
Doesn't modify the sessions; writes results to sessions/grader_compare.json. API graders cost real money
(counted in the monthly cap).

  python3 -m interviewer.compare_graders or:deepseek/deepseek-v4.1-flash sessions/a.json sessions/b.json ...
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import evaluator, models
from .casepack import load_all

OUT = Path(__file__).resolve().parents[1] / "sessions" / "grader_compare.json"


def main(argv: list[str]) -> None:
    grader, paths = argv[0], argv[1:]
    cases = load_all()
    results = json.loads(OUT.read_text()) if OUT.exists() else []
    for p in paths:
        s = json.loads(Path(p).read_text())
        old = s.get("report") or {}
        level = old.get("level") or s.get("level", "pm")
        spent0, t0 = models.month_spend(), time.time()
        new = evaluator.evaluate(cases[s["case_id"]], s, progress=lambda m: None, level=level, grader=grader)
        row = {
            "session": Path(p).name, "case": s["case_id"], "level": level, "grader": new.get("grader_model"),
            "old": {"verdict": old.get("verdict"), "mean": old.get("weighted_mean"),
                    "dims": {d["key"]: d.get("score") for d in old.get("dimensions", [])}},
            "new": {"verdict": new["verdict"], "mean": new["weighted_mean"],
                    "dims": {d["key"]: d.get("score") for d in new["dimensions"]},
                    "gates": [g.get("id") for g in new["gates"] if g.get("triggered") and not g.get("disputed")],
                    "has_logprobs": any(d.get("dist") for d in new["dimensions"])},
            "seconds": round(time.time() - t0), "cost": round(models.month_spend() - spent0, 4),
        }
        results.append(row)
        OUT.write_text(json.dumps(results, indent=1))
        print(f"{row['session']:28} {level:6} old {row['old']['verdict'] or '-':13} {row['old']['mean'] or 0:4.2f}"
              f" | new {row['new']['verdict']:13} {row['new']['mean']:4.2f} | {row['seconds']}s ${row['cost']:.4f}",
              flush=True)
        for k, v in row["new"]["dims"].items():
            print(f"    {k:28} old {row['old']['dims'].get(k)}  new {v}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
