"""Labelled check for the reveal gate: does each ask unlock the right clarifications/exhibits?

  python -m interviewer.eval_matching
"""
import time

from .casepack import load_all
from .engine import Interview

LABELS = {
    "A1": [
        ("By orders do we mean placed, confirmed by the restaurant, or delivered?", {"Q1"}),
        ("Second, time frame - sudden or gradual, and which week?", {"Q2", "Q3"}),
        ("Is it uniform across cities, platforms and user types?", {"Q4", "Q5", "Q6"}),
        ("Can I see the funnel?", {"E3"}),
        ("Are app sessions down, or are people coming in and failing to order?", {"E3"}),
        ("Can I break payment success by platform and method?", {"E4"}),
        ("Was there a UPI incident from NPCI or our partner bank last week?", {"E7"}),
        ("Did anything ship on Android around the start of the drop?", {"Q7", "E5"}),
        ("What changed in payments in 4.98?", {"E5"}),
        ("Do we have last year's Diwali week?", {"E6"}),
        ("Were there competitor promotions, rider supply issues or bad weather?", {"E7"}),
        ("Is the dashboard number real, or could tracking be broken?", {"E2"}),
        ("What's the average order value?", set()),
        ("How many restaurants are live on Swiggy?", set()),
        ("Has the drop happened in Instamart too?", {"Q8"}),
    ],
}


def main():
    cases = load_all()
    tp = fp = fn = 0
    for cid, rows in LABELS.items():
        iv = Interview(cases[cid])
        iv.open()
        for ask, want in rows:
            t0 = time.time()
            d = iv._understand(ask)
            got = set(d["asked_clarifications"]) | set(d["requested_exhibits"])
            ok_any = (not want and not got) or (got & want and not (got - want))
            tp += len(got & want); fp += len(got - want); fn += len(want - got)
            print(f"{'OK ' if ok_any else 'BAD'} {time.time()-t0:4.1f}s want={sorted(want)} got={sorted(got)}  {ask[:60]}"
                  f"   short={[(a[:0], c) for a, c in d['shortlist']]}")
    p = tp / (tp + fp) if tp + fp else 0
    r = tp / (tp + fn) if tp + fn else 0
    print(f"\nprecision {p:.2f}  recall {r:.2f}  (fp={fp}, fn={fn})")


if __name__ == "__main__":
    main()
