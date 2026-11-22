"""Check closing detection (when the interviewer hands over the twist) on the casebook walkthroughs.

  python3 -m interviewer.eval_closing            # all 66 cases
  python3 -m interviewer.eval_closing A1 D1 F1

For each case, replays the model candidate's lines (word count as the clock) through Interview._heuristic_intent and reports the
first line that counts as "recommending", as a fraction of the way through. Good = fires, and late
(>= 0.6). Early fires cut the case short; misses mean no twist (the grader double-checks landing).
"""
import sys
import time

from .casepack import load_all
from .engine import Interview, Turn


def main(argv):
    cases = load_all()
    ids = [a.upper() for a in argv] or list(cases)
    good = early = miss = 0
    t0 = time.time()
    for cid in ids:
        case = cases[cid]
        lines = [t["text"] for t in case.walkthrough if t["speaker"] == "Candidate"]
        p = Interview(case, sim_words_per_min=36)
        first = None
        for i, line in enumerate(lines):
            p.turns.append(Turn("Candidate", line, 0))
            if p._heuristic_intent(line) == "recommending":
                first = i
                break
        if first is None:
            miss += 1
            print(f"{cid:4} MISS")
            continue
        pos = first / max(1, len(lines) - 1)
        tag = "ok" if pos >= 0.6 else "EARLY"
        good += tag == "ok"
        early += tag == "EARLY"
        print(f"{cid:4} {tag:5} at {pos:.2f}  {lines[first][:90]!r}")
    n = len(ids)
    print(f"\nlate fire {good}/{n}, early {early}/{n}, miss {miss}/{n}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main(sys.argv[1:])
