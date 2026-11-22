"""Grading rubrics, baked from research/ (see build_rubrics.py and research/report.md).

Per case type:
  - dimensions: key, definition, evidence hint, weight (sums to 100), 1-4 behavioural anchors, coaching tip.
    Metrics also has a trade-off weight profile ("A went up, B went down: ship?").
  - gates: failures that cap the overall verdict whatever the average. Hand-written here as yes/no
    checks with machine-readable caps; `auto` gates are decided in code, not by the LLM.
  - green/red flags, recitation-vs-insight notes, whole-case exemplars and level notes (prompt context).

The numbers are the research's recommended defaults. They are inferred (no company publishes weights),
so recalibrate against hand-scored transcripts before trusting small differences.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_DATA = json.loads(Path(__file__).with_name("rubric_data.json").read_text())

VERDICTS = ["No hire", "Lean no hire", "Lean hire", "Hire", "Strong hire"]   # worst -> best
LEVELS = {"apm": -0.25, "pm": 0.0, "senior": 0.25}                          # threshold shift

# Interviewer interventions, from the universal research's hint taxonomy.
#   dim_delta: points off the targeted dimension; dim_cap: ceiling on it; overall_cap: verdict ceiling.
HINT_KINDS = {
    "clarification": {"dim_delta": 0.0, "dim_cap": None, "overall_cap": None},
    "pacing":        {"dim_delta": 0.0, "dim_cap": None, "overall_cap": None},
    "nudge":         {"dim_delta": -0.25, "dim_cap": None, "overall_cap": None},
    "redirect":      {"dim_delta": 0.0, "dim_cap": 2, "overall_cap": None},
    "rescue":        {"dim_delta": 0.0, "dim_cap": 2, "overall_cap": "Lean hire"},
}

MARKETPLACE_RE = re.compile(r"swiggy|zomato|instamart|uber|rapido|ola\b|blinkit|zepto|urban company|doordash|"
                            r"dunzo|porter|ownly|district|meesho|flipkart|amazon", re.I)
INCIDENT_RE = re.compile(r"outage|incident|\bdown\b|double[- ]charg|crash|breach|failure|went wrong|bug", re.I)
TRADEOFF_RE = re.compile(r"\bship\b|a/b|experiment|went up.{0,60}(went |fell|dropped|down)|"
                         r"(fell|dropped|down).{0,60}(went up|rose|increased)", re.I)


def G(id, check, cap, dim_cap=None, when=None, auto=False, requires=None, fires_on=True, order=None):
    """A gate. `check` is a yes/no question; the gate fires when the answer equals `fires_on`.
    Questions are phrased POSITIVELY ("did the candidate ever...?") with fires_on=False where the failure
    is an absence: an 8B model gets "did they NEVER..." backwards. `requires` is an optional
    precondition (question, fires_on) asked first; the gate fires only if both match.
    `order` = {"before": {key: event, ...}, "after": (key, event)} turns an "X before Y" gate into
    extraction: the LLM names the candidate turn where each event first happens (with a verified quote)
    and Python compares the turn numbers. The gate fires if "after" happens and some "before" event
    never happened or happened later. (The 8B model can't judge order across a long transcript.)"""
    if isinstance(requires, str):
        requires = (requires, True)
    return {"id": id, "check": check, "fires_on": fires_on, "cap": cap, "dim_cap": dim_cap or {},
            "when": when, "auto": auto, "requires": requires, "order": order}


Y, N = True, False   # fires_on: on a YES answer / on a NO answer

GATES = {
    "rca": [
        G("no_clarify", "Did the candidate start listing possible causes before asking or stating anything about "
          "the metric's definition, comparison baseline/timeframe, or whether the drop was sudden or gradual?",
          "Lean hire", {"clarify": 1},
          order={"before": {"clarify": "asks or states the metric's definition, baseline/timeframe, or whether "
                                       "the drop was sudden or gradual"},
                 "after": ("causes", "names a possible cause of the drop")}),
        G("no_data_check", "At any point, did the candidate consider whether the drop is real: logging, "
          "tracking, pipeline or dashboard issues, or seasonality / normal day-of-week variation?",
          "Lean hire", {"data_check": 1}, fires_on=N),
        G("solution_first", "Did the candidate propose fixes or solutions (discounts, redesigns, campaigns) "
          "BEFORE identifying any likely cause?", "Lean hire",
          order={"before": {"cause": "names a specific most-likely cause, backed by data"},
                 "after": ("fix", "proposes a fix or solution (rollback, discount, redesign, campaign)")}),
        G("unranked", "By the end, did the candidate name one most-likely cause (or rank their causes)?",
          "Lean hire", {"hypotheses": 2}, fires_on=N),
        G("ignored_data", "Did the candidate ignore or contradict data the interviewer revealed, e.g. keep "
          "pursuing a cause the interviewer's data had ruled out, or re-ask a question already answered?",
          "Lean no hire"),
        G("no_supply_side", "At any point, did the candidate consider the supply side of the marketplace "
          "(restaurants, sellers, stores, delivery partners, drivers, service partners)?", "Lean hire",
          when="marketplace", fires_on=N),
    ],
    "product_design": [
        G("solution_first", "Did the candidate propose solutions or features BEFORE choosing a target user "
          "segment AND a specific pain point?", "Lean no hire",
          order={"before": {"segment": "chooses one target user segment to focus on",
                            "pain": "states a specific pain point or need of users"},
                 "after": ("solution", "proposes a solution or feature")}),
        G("no_goal", "Did the candidate give a reason or criterion for at least one of their choices (segment, "
          "pain point or solution)?", "Lean hire", {"goal": 1}, fires_on=N,
          requires=("At any point, did the candidate state a goal or business objective for the product?", N)),
        G("one_idea", "Did the candidate propose only one solution, or several ideas that are really variants "
          "of a single concept?", "Lean hire", {"solutions": 2}),
        G("research_dodge", "Did the candidate answer mainly with 'I would first do user research / run surveys' "
          "instead of generating their own ideas?", "Lean no hire"),
        G("drift", "Is the candidate's final recommended solution connected to the segment and pain point they "
          "chose earlier?", "Lean hire", fires_on=N),
    ],
    "product_strategy": [
        G("no_recommendation", "By the end, did the candidate commit to a clear recommendation (not 'it depends' "
          "or a list of options)?", "Lean no hire", {"recommendation": 1}, fires_on=N),
        G("no_goal", "Before discussing options, did the candidate state an objective or the company's "
          "context?", "Lean hire", {"goal": 1}, fires_on=N,
          order={"before": {"goal": "asks about or states the objective, what success means, or the company's "
                                    "context"},
                 "after": ("options", "proposes a strategic option or move")}),
        G("feature_level", "Did at least one of the candidate's options involve a strategic move (entry mode, "
          "segment choice, business model, partner/build/buy, pricing), not just a UI feature?", "Lean hire",
          fires_on=N),
        G("no_tradeoff", "Did the candidate name at least one downside, cost, risk or alternative given up for "
          "their recommendation?", "Lean hire", {"tradeoffs": 2}, fires_on=N),
        G("collapse", "Did the candidate reverse their recommendation without any new information or stated "
          "reason?", "Lean hire", {"recommendation": 2},
          requires="Did the interviewer push back on or challenge the candidate's recommendation?"),
        G("ignores_core", "Did the candidate acknowledge that harm or risk to the core business?", "Lean hire",
          fires_on=N, requires="Would the candidate's final recommendation clearly harm the company's core "
                               "business (cannibalisation, regulatory or brand risk)?"),
    ],
    "situational": [
        G("no_decision", "Did the candidate commit to a decision (not just considerations, 'it depends' or "
          "'I'd gather more data')?", "Lean hire", {"decision": 1}, fires_on=N),
        G("harms_users", "Did the candidate choose an option that knowingly harms or deceives users, or breaks "
          "legal, privacy or security obligations?", "No hire", {"integrity": 1}),
        G("unilateral_or_deferral", "Did the candidate either force the decision on others with no "
          "consultation, OR only escalate to leadership without making their own recommendation?", "Lean hire"),
        G("blame", "Did the candidate blame engineering, leadership or another team and stop there, without "
          "owning the response?", "Lean hire", {"ownership": 1}),
        G("past_story", "Did the candidate say what THEY would do in this scenario (rather than only telling "
          "a past story or reciting a framework)?", "Lean hire", fires_on=N),
        G("no_mitigation", "In their first substantive response, did the candidate include immediate "
          "mitigation or communication to users/stakeholders?", "Lean hire", when="incident", fires_on=N,
          requires="Is this case about a live incident or outage that is hurting users now?"),
    ],
    "metrics": [
        G("no_goal", "Before listing metrics, did the candidate state the product's goal or the users / sides "
          "it serves?", "Lean hire", {"goal": 1}, fires_on=N,
          order={"before": {"goal": "asks about or states the product's goal, or the users / sides it serves"},
                 "after": ("metrics", "names a specific metric")}),
        G("no_single_nsm", "Did the candidate commit to ONE North Star / primary metric?", "Lean hire",
          {"north_star": 2}, fires_on=N),
        G("no_guardrail", "Did the candidate name at least one guardrail or counter-metric?", "Lean hire",
          {"guardrails": 1}, fires_on=N),
        G("no_ship_call", "The interviewer raised a trade-off (one metric up, another down, or a launch "
          "decision). By the end, did the candidate give an explicit decision (ship, don't ship, partial ship, "
          "or extend the test)?", "Lean hire", {"ship_decision": 1}, when="tradeoff_twist", fires_on=N),
        G("one_sided_ship", "Did they address the metric that went down, or the long-term / North Star impact?",
          "No hire", {"ship_decision": 1}, when="tradeoff", fires_on=N,
          requires="Did the candidate decide to ship?"),
        G("vanity_nsm", "Is the candidate's North Star a pure vanity metric (downloads, page views, sign-ups) or "
          "a bare average/ratio, with no acknowledgement of its weakness?", "Lean hire", {"north_star": 2}),
    ],
    "guesstimate": [
        G("arith_magnitude", "auto: an arithmetic step is off by 3x or more and is never corrected",
          "Lean no hire", {"calculation": 1}, auto=True),
        G("implausible", "Did the candidate accept a final number that is implausible (e.g. more units than "
          "people, a share of the population that can't be right) without questioning it?", "Lean hire",
          {"sanity": 1}),
        G("no_final_number", "By the end, did the candidate commit to a final number?", "Lean no hire",
          fires_on=N),
        G("no_structure", "Before calculating, did the candidate state an equation, approach or plan?",
          "Lean hire", {"approach": 2}, fires_on=N,
          order={"before": {"plan": "states an approach, equation or plan for the estimate (e.g. 'I'll go from "
                                    "orders', 'queries = orders x contact rate')"},
                 "after": ("calc", "states a specific number or does a calculation")}),
        G("wrong_quantity", "Did the candidate estimate a different quantity from the one asked (e.g. revenue "
          "instead of orders, a country instead of a city, per year instead of per day) and never reconcile "
          "it?", "Lean no hire"),
        G("black_box", "Did the candidate give reasoning for at least one of their key inputs?", "Lean no hire",
          {"assumptions": 1}, fires_on=N),
    ],
}

# Gates every type shares; decided in code from the engine log.
COMMON_GATES = [
    G("never_landed", "auto: the interview ended before the candidate gave a recommendation / final answer",
      "Lean no hire", auto=True),
    G("rescued", "auto: two or more substantive rescues by the interviewer", "No hire", auto=True),
]

# Product Design: a 1 on any of these means No hire whatever the average (Lenny's / Ben Erez must-pass).
MUST_PASS = {"product_design": ["segment", "pain_points", "solutions", "communication"]}


def is_tradeoff(case) -> bool:
    return case.type_key == "metrics" and bool(TRADEOFF_RE.search(case.prompt))


def dimensions(type_key: str, tradeoff: bool = False) -> list[dict]:
    """The case type's dimensions, with `w` set to the active weight profile."""
    dims = []
    for d in _DATA[type_key]["dimensions"]:
        d = dict(d)
        d["w"] = d["weight_tradeoff"] if tradeoff and d.get("weight_tradeoff") is not None else d["weight"]
        dims.append(d)
    return dims


def gates_for(case, twist_text: str = "") -> list[dict]:
    """Gates that apply to this case (conditional ones filtered by company / prompt / twist)."""
    ctx = f"{case.company} {case.title} {case.prompt}"
    out = []
    for g in GATES[case.type_key] + COMMON_GATES:
        w = g["when"]
        if w == "marketplace" and not MARKETPLACE_RE.search(ctx):
            continue
        if w == "incident" and not INCIDENT_RE.search(case.prompt):
            continue
        if w == "tradeoff" and not is_tradeoff(case):
            continue
        if w == "tradeoff_twist" and not (is_tradeoff(case) or TRADEOFF_RE.search(twist_text or "")):
            continue
        if w == "tradeoff_twist" and is_tradeoff(case):
            g = dict(g, cap="No hire")   # no ship call on a trade-off question proper is a no hire
        out.append(g)
    return out


def type_notes(type_key: str) -> dict:
    d = _DATA[type_key]
    return {k: d[k] for k in ("green_flags", "red_flags", "recitation_vs_insight", "exemplars",
                              "level_expectations", "llm_grading_notes")}


# A candidate asking for help ("I'm a bit stuck...") is not evidence of anything. The engine uses this to
# spot hint requests, and the grader drops these sentences so it can't quote them.
HINT_RE = re.compile(r"\b(i('?m| am) (a bit |kind of |a little )?stuck|give me a (hint|nudge)|can you help|not sure where to go|"
                     r"(can|could|may) (i|you|we) (get|have|give me) (a |any )?(hint|nudge|pointer))\b", re.I)


def strip_hint_requests(text: str) -> str:
    sents = re.split(r"(?<=[.?!])\s+|\n+", text)
    return " ".join(x for x in sents if not HINT_RE.search(x)).strip()


def phase_list(type_key: str) -> list[tuple[str, str]]:
    """Dimensions that are stages of the case (used by the engine to track progress and aim hints)."""
    return [(d["key"], d["name"]) for d in _DATA[type_key]["dimensions"] if d["phase"]]


def phase_texts(type_key: str) -> list[str]:
    return [f"{d['name']}. {d['definition']}" for d in _DATA[type_key]["dimensions"] if d["phase"]]
