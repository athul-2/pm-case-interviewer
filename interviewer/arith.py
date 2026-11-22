"""Re-check the candidate's spoken guesstimate arithmetic in Python (LLMs are bad at this).

Finds chains like "1.2 crore households x 30% = 36 lakh" or "50 lakh times 2 is 1 crore" in the
candidate's turns, evaluates them left to right, and reports steps that are off. Indian units
(lakh, crore) and Western ones (k, million, billion) are both understood; "into" means times.
It only checks what it can parse confidently, so silence is not proof the maths was right.
"""
from __future__ import annotations

import re

UNITS = {
    "%": 0.01, "percent": 0.01, "per cent": 0.01,
    "k": 1e3, "thousand": 1e3, "thousands": 1e3,
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "million": 1e6, "millions": 1e6, "mn": 1e6, "mil": 1e6, "m": 1e6,
    "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "billion": 1e9, "billions": 1e9, "bn": 1e9,
}
_UNIT = r"(?:per cent|percent|%|thousands?|k|lakhs?|lacs?|millions?|mn|mil|m|crores?|cr|billions?|bn)(?![a-z])"
_NUM = rf"(?<![A-Za-z\d.,])(?:₹|rs\.?\s?|\$)?(\d[\d,]*(?:\.\d+)?)\s?({_UNIT})?"
_FILLER = r"(?:\s+(?!(?:x|times|into|of|by|divided|is|are|equals|gives|comes|makes|that's|which|so)\b)[a-z'/-]+){0,3}"
_OP = r"\s*(x|×|\*|times|into|multiplied by|of|/|÷|divided by)\s*"
_EQ = (r"\s*(?:=|≈|equals|gives(?: us| you| me)?|comes to|comes out to|makes|that's|that is|which is|"
       r"is about|is roughly|is around|is)\s*(?:about|roughly|around|approximately|~)?\s*(?:₹|rs\.?\s?|\$)?")
CHAIN = re.compile(rf"{_NUM}{_FILLER}(?:{_OP}{_NUM}{_FILLER}){{1,5}}?{_EQ}{_NUM}", re.I)
TERM = re.compile(rf"{_NUM}{_FILLER}", re.I)
OPS = re.compile(_OP, re.I)
CORRECTION = re.compile(r"\b(sorry|actually|correction|let me (redo|re-?calculate|fix)|i made a mistake|"
                        r"that should be|that's wrong|my mistake|wait)\b", re.I)


def _val(num: str, unit: str | None) -> float:
    v = float(num.replace(",", ""))
    return v * UNITS.get((unit or "").lower(), 1.0)


def _parse(chunk: str):
    """Split a matched chain into operand values and operators; the last operand is the stated result."""
    nums = list(re.finditer(_NUM, chunk, re.I))
    if len(nums) < 3:
        return None
    ops = []
    for a, b in zip(nums, nums[1:]):
        between = chunk[a.end():b.start()]
        m = OPS.search(between)
        ops.append(m.group(1).lower() if m else "=")
    if ops[-1] != "=" or "=" in ops[:-1]:
        return None
    vals = [(_val(m.group(1), m.group(2)), m.group(2)) for m in nums]
    return vals, ops[:-1]


def _evaluate(vals, ops) -> float:
    acc = vals[0][0]
    for (v, _), op in zip(vals[1:-1], ops):
        if op in ("/", "÷", "divided by"):
            acc = acc / v if v else float("inf")
        else:
            acc *= v
    return acc


def check(candidate_turns: list[str]) -> list[dict]:
    """Return one entry per parsed calculation: text, computed, stated, ratio, error, corrected."""
    out = []
    for i, turn in enumerate(candidate_turns):
        for m in CHAIN.finditer(turn):
            before = turn[:m.start()].rstrip()[-1:]
            after = turn[m.end():].lstrip()[:1]
            if before in "+-x×*/" and before or after in ("+", "-", "x", "×", "*"):
                continue   # part of a longer sum or a chain continuing a previous result: can't check alone
            if re.search(r"\bout of\b", m.group(0), re.I):
                continue   # "1 in N" style ratios, not products
            parsed = _parse(m.group(0))
            if not parsed:
                continue
            vals, ops = parsed
            computed = _evaluate(vals, ops)
            stated, unit = vals[-1]
            if computed <= 0 or stated <= 0:
                continue
            # A unitless result often means "in the same unit as the inputs" ("2 crore x 50% = 1").
            cands = [stated] + ([stated * UNITS[u.lower()] for _, u in vals[:-1] if u and u != "%"]
                                if not unit else [])
            ratio = min(max(c, computed) / min(c, computed) for c in cands)
            if not unit and ratio >= 50:
                continue   # unitless result far off is almost always an unspoken unit ("4.4 tonnes")
            out.append({
                "text": m.group(0).strip(), "turn": i + 1,
                "computed": computed, "stated": stated, "ratio": round(ratio, 2),
                "error": ratio > 1.15,
                "corrected": ratio > 1.15 and bool(CORRECTION.search(
                    turn[m.end():] + " " + " ".join(candidate_turns[i + 1:i + 2]))),
            })
    return out


def magnitude_error(checks: list[dict]) -> dict | None:
    """First uncorrected step that is off by 3x or more (the guesstimate gate)."""
    return next((c for c in checks if c["ratio"] >= 3 and not c["corrected"]), None)


def human(v: float) -> str:
    for unit, n in (("crore", 1e7), ("lakh", 1e5), ("k", 1e3)):
        if v >= n:
            return f"{v / n:,.3g} {unit}"
    return f"{v:,.3g}"
