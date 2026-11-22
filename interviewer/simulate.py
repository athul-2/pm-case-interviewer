"""Replay a scripted candidate through the engine to check reveals and grading.

  python -m interviewer.simulate A1            # strong candidate = casebook walkthrough's candidate lines
  python -m interviewer.simulate A1 --weak     # a weak, generic candidate
  python -m interviewer.simulate A1 --mid a    # mid-level: a = shallow, b = needs hints (mid_scripts.py)
  python -m interviewer.simulate A1 --no-grade
  python -m interviewer.simulate A1 --level apm|pm|senior
"""
import sys
import time

from .casepack import load_all
from .engine import Interview

# Deliberately weak, generic candidates, one per case type (written to trip that type's gates).
WEAK = {
    "rca": [
        "Okay so orders dropped. I think it's probably because Zomato is giving more discounts.",
        "Was there a bug? Was there rain? Was there a strike? Did fees go up?",
        "I would run a survey of users to understand why they left.",
        "My recommendation is to give everyone a discount coupon to bring them back.",
        "I'd just keep the coupons running and see.",
    ],
    "product_design": [
        "I think we should add a feature that shows a photo of the actual portion before ordering.",
        "Also maybe an AI that personalises the menu for everyone.",
        "Honestly, I would first do user research and surveys to understand the problem better.",
        "My recommendation is to build the photo feature because it is the best idea.",
        "I think users will like it.",
    ],
    "product_strategy": [
        "Swiggy should add a 10-minute food button on the home screen.",
        "We could also add a dark mode and better search filters to make the app nicer.",
        "There are many options and they all have pros and cons, so it really depends.",
        "To summarise, it depends on the situation, we should probably try a few things and see.",
        "Yes, you're right, then we shouldn't do it.",
    ],
    "situational": [
        "This happened to me at my last internship, our feature was late and we just worked weekends.",
        "Engineering should have planned better, honestly this is on them.",
        "I would escalate it to my manager and let leadership decide.",
        "In summary, I'd tell leadership and wait for their call.",
        "It depends on what leadership says.",
    ],
    "metrics": [
        "I'd track DAU, MAU, orders, revenue, downloads, and retention.",
        "Also page views and app installs are important, and time spent.",
        "All of these matter, it depends on what the company wants.",
        "My north star would be downloads, because more downloads means more growth.",
        "I'd ship it, the numbers are up.",
    ],
    "guesstimate": [
        "India has 140 crore people. Say 10 crore use Swiggy.",
        "Each one contacts support 2 times a day, so 10 crore x 2 = 2 crore queries a day.",
        "Say 50% are about delivery, so 2 crore x 50% = 10 lakh delivery queries.",
        "So my final answer is around 2 crore queries a day.",
        "I think that's right.",
    ],
}


def main(argv):
    case = load_all()[argv[0].upper()]
    weak = "--weak" in argv
    level = argv[argv.index("--level") + 1] if "--level" in argv else "pm"
    mid = argv[argv.index("--mid") + 1] if "--mid" in argv else None
    if mid:
        from .mid_scripts import MID
        lines = MID[case.id][mid]
    elif weak:
        lines = WEAK[case.type_key]
    else:
        lines = [t["text"] for t in case.walkthrough if t["speaker"] == "Candidate"]
    iv = Interview(case, level, sim_words_per_min=36)
    print("IV:", iv.open().text)
    for line in lines:
        if iv.done:
            break
        print(f"\nCAND: {line[:300]}")
        t0 = time.time()
        out, ex = "", []
        for ev in (iv.hint() if line == "/hint" else iv.respond(line)):
            if ev.kind == "say":
                out += ev.text
            elif ev.kind == "exhibit":
                ex.append(ev.exhibit_id)
        meta = iv.turns[-2].meta
        print(f"  [intent={meta.get('intent')} q={meta.get('asked_clarifications')} "
              f"e={meta.get('requested_exhibits')} phases={meta.get('phases_touched')}  {time.time()-t0:.1f}s]")
        if ex:
            print(f"  [EXHIBITS SHOWN: {ex}]")
        print(f"IV: {out.strip()}")
    path = iv.save()
    print(f"\nsaved {path}\nunlocked Q {iv.revealed_q} E {iv.revealed_e} twist {iv.twist_id}")
    if "--no-grade" not in argv:
        from .cli import grade_file
        t0 = time.time()
        grade_file(path)
        print(f"grading took {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main(sys.argv[1:])
