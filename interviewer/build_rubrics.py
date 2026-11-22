"""Compile research/results/*.json into interviewer/rubric_data.json.

  python3 -m interviewer.build_rubrics

The research files are verbose and keyed by long dimension names. This keeps only what the grader
needs (definition, evidence hint, weights, 1-4 anchors, coaching tip, flags, exemplars, level notes)
and gives every dimension a short stable key. Gates are NOT taken from here: they need machine-readable
caps and yes/no wording, so they are hand-written in rubrics.py (with the research gate text as source).
Re-run this whenever the research is regenerated; rubrics.py reads the JSON at import.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

RESEARCH = Path(__file__).resolve().parents[1] / "research" / "results"
OUT = Path(__file__).with_name("rubric_data.json")

FILES = {
    "rca": "Root_Cause_Analysis_metric_drop.json",
    "product_design": "Product_Design_Improvement.json",
    "product_strategy": "Product_Strategy_plan_and_decision_questions.json",
    "situational": "Situational_judgment_questions.json",
    "metrics": "Success_Metrics_North_Star.json",
    "guesstimate": "Guesstimate_market_sizing.json",
}

# Short keys, in the research's dimension order. Checked against the files below so a renamed
# dimension fails loudly instead of silently losing its weight.
KEYS = {
    "rca": ["clarify", "data_check", "decompose", "hypotheses", "validate", "conclude", "communication"],
    "product_design": ["goal", "segment", "pain_points", "solutions", "tradeoffs", "metrics", "communication"],
    "product_strategy": ["goal", "insight", "options", "tradeoffs", "recommendation", "viability", "execution",
                         "communication"],
    "situational": ["framing", "options", "decision", "stakeholders", "integrity", "execution", "ownership"],
    "metrics": ["goal", "north_star", "operationalise", "levers", "guardrails", "ship_decision", "communication"],
    "guesstimate": ["scoping", "approach", "segmentation", "assumptions", "calculation", "sanity", "communication"],
}
# Dimensions that aren't a stage of the case (no hint should steer towards them).
NOT_A_PHASE = {"communication", "integrity", "ownership", "stakeholders"}


def _clean(name: str) -> str:
    return re.sub(r"^\d+\.\s*", "", name).strip()


def _weights(w: dict) -> dict[str, tuple[float, float | None]]:
    rows = w.get("per_dimension") or w.get("recommended") or w.get("table")
    out = {}
    for r in rows:
        pct = r.get("weight_pct", r.get("weight"))
        out[_clean(r["dimension"])] = (float(pct), r.get("tradeoff_profile_pct"))
    return out


def build() -> dict:
    data = {}
    for tkey, fname in FILES.items():
        d = json.loads((RESEARCH / fname).read_text())
        weights = _weights(d["weights"])
        dims = []
        for key, dim in zip(KEYS[tkey], d["dimensions"], strict=True):
            name = _clean(dim["name"])
            if name not in weights or name not in d["score_anchors"]:
                raise SystemExit(f"{tkey}: dimension {name!r} missing from weights or anchors")
            w, w_trade = weights[name]
            dims.append({
                "key": key, "name": name,
                "definition": dim["definition"],
                "signal": dim.get("signal_sought", ""),
                "evidence": dim.get("transcript_evidence", dim.get("evidence_to_quote", "")),
                "weight": w,
                "weight_tradeoff": float(w_trade) if w_trade is not None else None,
                "anchors": {str(k): v for k, v in d["score_anchors"][name].items()},
                "coach": d["coachable_feedback"].get(name, ""),
                "phase": key not in NOT_A_PHASE,
            })
        total = sum(x["weight"] for x in dims)
        if abs(total - 100) > 0.5:
            raise SystemExit(f"{tkey}: weights sum to {total}")
        ex = d["calibration_exemplars"]
        data[tkey] = {
            "dimensions": dims,
            "green_flags": d["green_flags"],
            "red_flags": d["red_flags"],
            "recitation_vs_insight": d["recitation_vs_insight"],
            "exemplars": {k: ex[f"score_{k}"] if f"score_{k}" in ex else ex[k] for k in "1234"},
            "level_expectations": d["level_expectations"],
            "llm_grading_notes": d["llm_grading_notes"],
        }
    return data


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=1, ensure_ascii=False))
    print(f"wrote {OUT}")
