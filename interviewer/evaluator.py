"""Post-interview grading with Qwen3 8B, built on the eval research (see rubrics.py).

Pipeline (all arithmetic, caps and the verdict are in Python, never in the LLM):
  1. Dimensions: ONE LLM pass per dimension, quotes first, then reason, then score. The score is the
     expected value over the 1-4 token probabilities (less lenient and steadier than one sample).
  2. Quote check: every quote must be a substring of the candidate's own turns. A score above 2 with no
     valid quote is capped at 2 (stops credit for interviewer lines and invented evidence).
  3. Gates: one yes/no pass per gate; `auto` gates come from the engine log and the arithmetic checker.
  4. Hint penalties from the logged interventions (nudge / redirect / rescue).
  5. Rollup: weighted mean, minimum bar, gate caps and level shift -> verdict.
  6. Content (vs the casebook answer) and coaching passes, as before.

The case prompt + transcript is the same leading system message on every pass, so Ollama reuses its
KV cache and each extra pass only pays for the short dimension / gate text.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from typing import Callable

from . import arith, llm
from .casepack import Case
from .rubrics import (HINT_RE, HINT_KINDS, LEVELS, MUST_PASS, VERDICTS, dimensions, gates_for, is_tradeoff, strip_hint_requests,
                      phase_list, type_notes)

NUM_CTX = 16384
THINK_DIMS = os.environ.get("GRADER_THINK", "0") == "1"   # thinking per dimension: better, ~3x slower


# ---------- transcript and quote check ----------

def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[–—-]", " ", s)
    s = re.sub(r"[^a-z0-9%₹$'. ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .")


def candidate_text(session: dict) -> str:
    return _norm(" ".join(strip_hint_requests(t["text"]) for t in session["turns"] if t["speaker"] == "Candidate"))


def quote_ok(quote: str, cand_norm: str) -> bool:
    """Verbatim (after normalising case/punctuation). '...' splits a quote into fragments, each checked."""
    quote = re.sub(r"\[?[CI]\d+\]?\s*(CANDIDATE|INTERVIEWER)?\s*:\s*", " ", quote)   # line labels
    frags = [_norm(f) for f in re.split(r"\.\.\.|…", quote)]
    frags = [f for f in frags if f]
    return bool(frags) and all(len(f.split()) >= 2 and f in cand_norm for f in frags)


def numbered_transcript(session: dict) -> str:
    lines, c, i = [], 0, 0
    for t in session["turns"]:
        if t["speaker"] == "Candidate":
            c += 1
            said = strip_hint_requests(t["text"])
            lines.append(f"[C{c}] CANDIDATE: {said}" if said else
                         f"[C{c}] CANDIDATE: (asks the interviewer for a hint; not evidence of anything)")
        else:
            i += 1
            lines.append(f"[I{i}] INTERVIEWER: {t['text']}")
    return "\n".join(lines)


# ---------- shared prefix ----------

def _system(case: Case, session: dict) -> str:
    notes = type_notes(case.type_key)
    rvi = notes["recitation_vs_insight"]
    rvi = rvi if isinstance(rvi, str) else " ".join(rvi)
    lgn = notes["llm_grading_notes"]
    lgn = lgn if isinstance(lgn, str) else " ".join(f"- {x}" for x in lgn)
    return f"""You are a strict, calibrated senior PM interviewer grading ONE dimension at a time of a {case.case_type} case interview. Most candidates score 2. A 3 is solid and specific to this case. A 4 is rare.

Rules for every dimension:
- Judge only what the CANDIDATE said ([C#] lines). Facts the INTERVIEWER revealed ([I#] lines) earn credit only for asking for them or using them.
- Quotes must be copied word for word from [C#] lines. Never quote an [I#] line. Never paraphrase.
- Do not reward length, or framework words (MECE, funnel, internal/external, CIRCLES) without case-specific content.
- Generic content that would fit any product scores at most 2.

Telling recitation from insight in this case type: {rvi}

Known grading pitfalls for this case type:
{lgn}

CASE {case.id}: {case.title} ({case.company})
PROMPT: {case.prompt}

TRANSCRIPT:
{numbered_transcript(session)}"""


# ---------- grader backend: local Ollama, or an API model chosen on the Models screen ----------

GRADER = {"id": "local:qwen3:8b", "session": ""}   # set per run by evaluate()


def _grader_chat(messages: list[dict], schema: dict, *, think: bool = False, temperature: float = 0,
                 logprobs: bool = False) -> dict:
    """One grader call. Returns an Ollama-shaped response ({"message": {"content"}, "logprobs": [...]}), so
    _expected_score works the same for every backend."""
    gid = GRADER["id"]
    if gid.startswith("local:"):
        return llm.chat(messages, model=llm.GRADER_MODEL, fmt=schema, think=think, temperature=temperature,
                        num_ctx=NUM_CTX, top_logprobs=5 if logprobs else 0, raw=True)
    from . import models
    from .agent import _post_json, _strip_think
    m = models.MODELS[gid]
    # JSON mode plus the schema in words (structured-output support varies by OpenRouter provider). Field
    # order matters: "score" must come last so its logprobs sit at the end.
    order = list(schema.get("properties", {}))
    msgs = [dict(x) for x in messages]
    msgs[-1]["content"] += ("\n\nReply with one JSON object only, with exactly these fields in this order: "
                            + ", ".join(order) + f". JSON schema: {json.dumps(schema)}")
    body = {"model": m["model"], "messages": msgs, "max_tokens": 4000 if think else 1500,
            "temperature": temperature, "response_format": {"type": "json_object"},
            "reasoning": {"effort": "medium" if think else "none"}}
    if logprobs:
        body.update(logprobs=True, top_logprobs=5, provider={"require_parameters": True})
    models.charge_guard(gid, sum(len(x["content"]) for x in msgs), body["max_tokens"])
    r = _post_json(models.base_url(gid) + "/chat/completions", models.api_key(m["provider"]), body, timeout=180)
    u = r.get("usage") or {}
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    models.record(gid, inp=max(0, (u.get("prompt_tokens") or 0) - cached), out=u.get("completion_tokens") or 0,
                  cache_read=cached, session="grade:" + GRADER["session"],
                  actual_cost=u.get("cost") if m.get("reported_cost") else None)
    ch = (r.get("choices") or [{}])[0]
    content = _strip_think((ch.get("message") or {}).get("content") or "")
    lps = [{"token": x.get("token", ""), "logprob": x.get("logprob", 0),
            "top_logprobs": [{"token": a.get("token", ""), "logprob": a.get("logprob", 0)} for a in x.get("top_logprobs") or []]}
           for x in ((ch.get("logprobs") or {}).get("content") or [])]
    return {"message": {"content": content}, "logprobs": lps}


def _ask(system: str, user: str, schema: dict, *, think: bool = False, logprobs: bool = False) -> tuple[dict, dict]:
    resp = _grader_chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                        schema, think=think, temperature=0, logprobs=logprobs)
    return llm.parse_json(resp["message"]["content"]), resp


def _expected_score(resp: dict, fallback: int) -> tuple[float, dict]:
    """Expected value over the 1-4 probabilities at the token after "score"."""
    lps = resp.get("logprobs") or []
    # "score" is the last field, so walk back from the end: the last 1-4 digit token whose preceding
    # tokens spell `"score": `. (Quotes can contain the word "score" and digits, so never scan forwards.)
    for i in range(len(lps) - 1, -1, -1):
        lp = lps[i]
        tok = lp.get("token", "")
        if tok.strip() not in {"1", "2", "3", "4"}:
            continue
        before = "".join(x.get("token", "") for x in lps[max(0, i - 4):i])
        if not re.search(r'score"\s*:\s*$', before) or int(tok.strip()) != fallback:
            continue
        probs = {}
        for alt in lp.get("top_logprobs", []) or [lp]:
            k = alt["token"].strip()
            if k in {"1", "2", "3", "4"}:
                probs[int(k)] = probs.get(int(k), 0) + math.exp(alt["logprob"])
        z = sum(probs.values())
        if z > 0:
            dist = {k: round(v / z, 3) for k, v in sorted(probs.items())}
            return round(sum(k * p for k, p in dist.items()), 2), dist
    return float(fallback), {}


# ---------- 1-2. dimensions + quote check ----------

DIM_SCHEMA = {"type": "object", "properties": {
    "quotes": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
    "reason": {"type": "string"},
    "score": {"type": "integer", "enum": [1, 2, 3, 4]}},
    "required": ["quotes", "reason", "score"]}


def _dim_prompt(d: dict, extra: str = "") -> str:
    a = d["anchors"]
    return f"""DIMENSION: {d['name']}
Definition: {d['definition']}
Signal sought: {d['signal']}
What to quote: {d['evidence']}
{extra}
Score anchors (behaviour you can see in the [C#] lines):
1 = {a['1']}
2 = {a['2']}
3 = {a['3']}
4 = {a['4']}

Return JSON: "quotes" = up to 3 short verbatim quotes from [C#] lines that are the evidence for your score (empty list if the candidate showed nothing for this dimension); "reason" = one sentence; "score" = 1-4. A 3 or 4 requires at least one quote."""


def grade_dimensions(case: Case, session: dict, system: str, extra: dict[str, str],
                     progress: Callable[[str], None]) -> list[dict]:
    cand = candidate_text(session)
    out = []
    for d in dimensions(case.type_key, is_tradeoff(case)):
        progress(f"  - {d['name']}")
        res, resp = _ask(system, _dim_prompt(d, extra.get(d["key"], "")), DIM_SCHEMA,
                         think=THINK_DIMS, logprobs=True)
        sampled = int(res.get("score", 1) or 1)
        ev, dist = _expected_score(resp, sampled)
        quotes = [q for q in res.get("quotes", []) if isinstance(q, str) and q.strip()]
        good = [q for q in quotes if quote_ok(q, cand)]
        bad = [q for q in quotes if q not in good]
        caps = []
        score = ev
        if score > 2 and not good:
            score = 2.0
            caps.append("no verbatim candidate quote: capped at 2")
        out.append({"key": d["key"], "name": d["name"], "weight": d["w"], "llm_score": sampled,
                    "expected": ev, "dist": dist, "score": score, "quotes": good, "rejected_quotes": bad,
                    "reason": res.get("reason", ""), "caps": caps, "coach": d["coach"]})
    return out


# ---------- 3. gates ----------

YN_SCHEMA = {"type": "object", "properties": {
    "reason": {"type": "string"}, "answer": {"type": "string", "enum": ["yes", "no"]}},
    "required": ["reason", "answer"]}


def _yes_no(system: str, question: str) -> tuple[bool, str]:
    res, _ = _ask(system, f"""Answer one yes/no question about this transcript.
QUESTION: {question}
Return JSON: "reason" = one sentence citing what the candidate did or did not say; then "answer" = "yes" or "no". The answer must agree with your reason.""",
                  YN_SCHEMA)
    return res.get("answer") == "yes", res.get("reason", "")


def _split_sentences(session: dict) -> list[str]:
    """Candidate sentences, each labelled with its turn ([C3] ...) so order-based gates can be judged."""
    out, c = [], 0
    for t in session["turns"]:
        if t["speaker"] == "Candidate":
            c += 1
            out += [f"[C{c}] {x.strip()}" for x in re.split(r"(?<=[.?!])\s+|\n+", t["text"])
                    if len(x.split()) >= 4 and not HINT_RE.search(x)]
    return out


def _nearest_sentences(question: str, sents: list[str], k: int = 4) -> list[str]:
    if not sents:
        return []
    try:
        from .matcher import _cos
        vecs = llm.embed([f"search_query: {question}"] + [f"search_document: {x}" for x in sents])
    except Exception:
        return []
    q, docs = vecs[0], vecs[1:]
    ranked = sorted(range(len(sents)), key=lambda i: -_cos(q, docs[i]))
    return [sents[i][:300] for i in ranked[:k]]


def _order_gate(system: str, order: dict, session: dict) -> tuple[bool, str]:
    """LLM finds the first [C#] turn of each event (with a quote); Python verifies and compares."""
    events = dict(order["before"])
    after_key, after_desc = order["after"]
    events[after_key] = after_desc
    item = {"type": "object", "properties": {"turn": {"type": "integer", "minimum": 0},
                                             "quote": {"type": "string"}}, "required": ["turn", "quote"]}
    schema = {"type": "object", "properties": {k: item for k in events}, "required": list(events)}
    lines = "\n".join(f'- {k}: the first [C#] line where the candidate {d}' for k, d in events.items())
    res, _ = _ask(system, f"""For each event below, find the FIRST candidate line ([C#]) where it happens. Give its number as "turn" (C3 -> 3) and a short verbatim "quote" from that line. If it never happens, turn = 0 and quote = "".
{lines}""", schema)
    cand = [_norm(t["text"]) for t in session["turns"] if t["speaker"] == "Candidate"]

    def first(k):
        e = res.get(k) or {}
        n = int(e.get("turn") or 0)
        if n <= 0:
            return 0, True
        ok = 1 <= n <= len(cand) and quote_ok(e.get("quote", ""), cand[n - 1])
        if not ok:   # quote not in that turn: find the turn the quote is really in, if any
            n2 = next((i + 1 for i, c in enumerate(cand) if quote_ok(e.get("quote", ""), c)), 0)
            return n2, n2 > 0
        return n, True

    t_after, ok_after = first(after_key)
    if not ok_after or t_after == 0:
        return False, f"'{after_key}' not found in a verified candidate turn"
    late = []
    for k in order["before"]:
        t, ok = first(k)
        if not ok:
            return False, f"could not verify when '{k}' happened; not firing"
        if t == 0 or t > t_after:
            late.append(f"{k} {'never' if t == 0 else f'at C{t}'}")
    if late:
        return True, f"{after_key} at C{t_after} came before: {', '.join(late)}"
    return False, f"{after_key} at C{t_after}; " + ", ".join(f"{k} at C{first(k)[0]}" for k in order["before"])


def grade_gates(case: Case, session: dict, system: str, checks: list[dict],
                progress: Callable[[str], None], dims: list[dict] | None = None) -> list[dict]:
    cand_sents = _split_sentences(session)
    twist = next((t.twist for t in case.twists if t.id == session.get("twist")), "")
    rescues = sum(1 for i in session.get("interventions", []) if i["kind"] == "rescue")
    landed = session.get("landed", bool(session.get("twist")))
    out = []
    for g in gates_for(case, twist):
        disputed = False
        if g["auto"]:
            if g["id"] == "never_landed":
                hit, why = False, "the engine saw a recommendation / final answer"
                if not landed:   # the engine's closing detector can miss; confirm before capping
                    ans, why = _yes_no(system, "By the end, did the candidate give a clear final answer to the "
                                       "case (a committed recommendation or decision, a final estimate, or a "
                                       "named root cause with a fix)?")
                    hit = not ans
            elif g["id"] == "rescued":
                hit, why = rescues >= 2, f"{rescues} rescue(s) by the interviewer"
            elif g["id"] == "arith_magnitude":
                bad = arith.magnitude_error(checks)
                hit = bad is not None
                why = (f"'{bad['text']}' is off by {bad['ratio']}x (computed {arith.human(bad['computed'])})"
                       if bad else "no uncorrected order-of-magnitude slip found")
            else:
                continue
        elif g.get("order"):
            progress(f"  - gate: {g['id']} (order)")
            hit, why = _order_gate(system, g["order"], session)
        else:
            progress(f"  - gate: {g['id']}")
            disputed = False
            # Precondition first (an 8B model answers only half of an AND), then the main question.
            steps = ([g["requires"]] if g.get("requires") else []) + [(g["check"], g["fires_on"])]
            hit, why = True, ""
            for q, fires_on in steps:
                ans, why = _yes_no(system, q)
                if ans != fires_on:
                    hit = False
                    break
            # Re-check before firing, with evidence in front of the model. An 8B model misses things said
            # once in a long transcript, so absence gates ("did they ever...?") over-fire. Evidence = the
            # candidate's sentences closest to the question (embeddings) plus verified quotes from any
            # dimension this gate would cap that scored 3+.
            if hit:
                ev = _nearest_sentences(g["check"], cand_sents)
                for d in dims or []:
                    if d["key"] in g["dim_cap"] and d["expected"] >= 3:
                        ev += d["quotes"][:2]
                ev = list(dict.fromkeys(ev))
                if ev:
                    quoted = "\n".join(f'- "{x}"' for x in ev)
                    ans, why2 = _yes_no(system, f"{g['check']}\nBefore answering, read these lines the candidate "
                                                f"actually said (they may or may not be relevant):\n{quoted}")
                    if ans != g["fires_on"]:
                        hit, why = False, f"cleared on re-check with the candidate's own lines: {why2}"
            # Still firing, but it caps a dimension the focused per-dimension pass scored 3+ on verified
            # quotes: the two passes disagree about the same behaviour. Trust the quoted dimension score,
            # report the gate as disputed, and don't let it cap anything.
            if hit and any(d["key"] in g["dim_cap"] and d["expected"] >= 3 and d["quotes"] for d in dims or []):
                disputed = True
                hit = False
        out.append({"id": g["id"], "triggered": hit, "reason": why, "cap": g["cap"], "dim_cap": g["dim_cap"],
                    "check": g["check"], "auto": g["auto"], "disputed": g["auto"] is False and disputed})
    return out


# ---------- 4-5. penalties and rollup (pure Python) ----------

def _cap_verdict(v: str, cap: str) -> str:
    return VERDICTS[min(VERDICTS.index(v), VERDICTS.index(cap))]


def rollup(type_key: str, dims: list[dict], gates: list[dict], interventions: list[dict],
           level: str = "pm") -> dict:
    by_key = {d["key"]: d for d in dims}
    phase_keys = [k for k, _ in phase_list(type_key)]

    # hint penalties on the targeted dimension
    for iv in interventions:
        rule = HINT_KINDS.get(iv["kind"], {})
        d = by_key.get(iv.get("target", ""))
        if not d or not rule:
            continue
        if rule["dim_delta"]:
            d["score"] = max(1.0, d["score"] + rule["dim_delta"])
            d["caps"].append(f"{iv['kind']} from interviewer: {rule['dim_delta']:+}")
        if rule["dim_cap"] and d["score"] > rule["dim_cap"]:
            d["score"] = float(rule["dim_cap"])
            d["caps"].append(f"{iv['kind']} from interviewer: capped at {rule['dim_cap']}")

    # gate caps on dimensions
    fired = [g for g in gates if g["triggered"]]
    for g in fired:
        for k, cap in g["dim_cap"].items():
            d = by_key.get(k)
            if d and d["score"] > cap:
                d["score"] = float(cap)
                d["caps"].append(f"gate {g['id']}: capped at {cap}")

    total_w = sum(d["weight"] for d in dims)
    mean = round(sum(d["score"] * d["weight"] for d in dims) / total_w, 2)
    lowest = min(round(d["score"]) for d in dims)
    shift = LEVELS[level]
    reasons = [f"weighted mean {mean} (level {level}, thresholds {shift:+})"]

    if mean >= 3.5 + shift and lowest >= 3:
        v = "Strong hire"
    elif mean >= 3.0 + shift and lowest >= 2:
        v = "Hire"
    elif mean >= 2.5 + shift:
        v = "Lean hire"
    elif mean >= 2.0 + shift:
        v = "Lean no hire"
    else:
        v = "No hire"
    if mean >= 3.5 + shift and lowest < 3:
        reasons.append("minimum bar: a dimension below 3 blocks Strong hire")
    if mean >= 3.0 + shift and lowest < 2:
        reasons.append("minimum bar: a dimension at 1 blocks Hire")

    nudges = sum(i["kind"] == "nudge" for i in interventions)
    redirects = sum(i["kind"] == "redirect" for i in interventions)
    rescues = sum(i["kind"] == "rescue" for i in interventions)
    if v == "Strong hire" and nudges > 1:
        v = "Hire"
        reasons.append(f"{nudges} nudges: Strong hire allows at most one")
    allowed_redirects = 1 if level == "apm" else 0
    if redirects > allowed_redirects:
        v2 = _cap_verdict(v, "Lean hire")
        if v2 != v:
            reasons.append(f"{redirects} redirect(s) from the interviewer: max Lean hire at {level}")
        v = v2
    if rescues:
        v2 = _cap_verdict(v, HINT_KINDS["rescue"]["overall_cap"])
        if v2 != v:
            reasons.append("interviewer rescue: max Lean hire")
        v = v2

    for g in fired:
        v2 = _cap_verdict(v, g["cap"])
        reasons.append(f"gate {g['id']}: max {g['cap']}" + (" (applied)" if v2 != v else ""))
        v = v2
    # Several gates compound. The research says 2+ = No hire, but its caps are inferred and an 8B
    # gate check still has false positives, so (calibrated on the mid-level scripts): 2 = at most
    # Lean no hire, 3+ = No hire.
    n_fired = len([g for g in fired if g["id"] != "never_landed"])
    if n_fired >= 3:
        if v != "No hire":
            reasons.append(f"{n_fired} gates: No hire")
        v = "No hire"
    elif n_fired == 2:
        v2 = _cap_verdict(v, "Lean no hire")
        if v2 != v:
            reasons.append("two gates: max Lean no hire")
        v = v2

    for k in MUST_PASS.get(type_key, []):
        if k in by_key and round(by_key[k]["score"]) <= 1:
            if v != "No hire":
                reasons.append(f"must-pass dimension {k} at 1: No hire")
            v = "No hire"

    return {"verdict": v, "weighted_mean": mean, "lowest": lowest, "reasons": reasons,
            "interventions": {"nudge": nudges, "redirect": redirects, "rescue": rescues,
                              "clarification": sum(i["kind"] == "clarification" for i in interventions),
                              "pacing": sum(i["kind"] == "pacing" for i in interventions)},
            "phases_covered": [k for k in phase_keys]}


# ---------- 6. content + coaching ----------

def case_twist(case: Case, twist_id: str | None):
    t = next((t for t in case.twists if t.id == twist_id), None)
    return {"twist": t.twist, "response": t.strong_response} if t else None


def grade_content(case: Case, session: dict) -> dict:
    pitfalls = "\n".join(f"P{i}: {p}" for i, p in enumerate(case.pitfalls, 1))
    schema = {
        "type": "object",
        "properties": {
            "answer_quality": {"type": "integer", "enum": [1, 2, 3, 4]},
            "answer_comment": {"type": "string"},
            "key_points_hit": {"type": "array", "items": {"type": "string"}},
            "key_points_missed": {"type": "array", "items": {"type": "string"}},
            "pitfalls_fallen_into": {"type": "array", "items": {"type": "string"}},
            "guardrails_mentioned": {"type": "array", "items": {"type": "string"}},
            "twist_score": {"type": "integer", "enum": [0, 1, 2, 3, 4]},
            "twist_comment": {"type": "string"},
        },
        "required": ["answer_quality", "answer_comment", "key_points_hit", "key_points_missed",
                     "pitfalls_fallen_into", "guardrails_mentioned", "twist_score", "twist_comment"],
    }
    twist = case_twist(case, session.get("twist"))
    prompt = f"""You compare a candidate's case interview answer with the casebook's reference solution. Credit only what the CANDIDATE ([C#] lines) said, not what the interviewer revealed.

REFERENCE ANSWER (the candidate never saw this):
{case.answer}

REFERENCE GUARDRAIL METRICS:
{case.guardrails}

COMMON PITFALLS:
{pitfalls}

FOLLOW-UP TWIST ASKED: {twist['twist'] if twist else "none"}
STRONG TWIST RESPONSE: {twist['response'] if twist else "n/a"}

Instructions:
- answer_quality 1-4: did the candidate reach the reference conclusion (or an equally well-argued alternative)? 1 = missing or wrong, 2 = partial or generic, 3 = right and supported, 4 = right, prioritised and tied to the goal.
- key_points_hit / key_points_missed: the 3-6 most important ideas of the reference answer, each as a short phrase, split by whether the candidate said them.
- pitfalls_fallen_into: pitfall ids (P1, P2...) with a few words on how, only if the transcript shows it.
- guardrails_mentioned: guardrail or counter-metrics the candidate named.
- twist_score: 0 if no twist was asked, else 1-4 versus the strong response.
A different answer can score well if it is well supported by the data the interviewer gave.

TRANSCRIPT:
{numbered_transcript(session)}"""
    return llm.parse_json(_grader_chat([{"role": "user", "content": prompt}], schema, think=True,
                                       temperature=0.2)["message"]["content"])


def coach(case: Case, dims: list[dict], gates: list[dict], roll: dict, content: dict) -> dict:
    strong = [d for d in dims if round(d["score"]) >= 3 and d["quotes"]]
    weak = sorted(dims, key=lambda d: (d["score"], -d["weight"]))[:3]
    schema = {
        "type": "object",
        "properties": {
            "one_line_summary": {"type": "string"},
            "strengths": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
            "fixes": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
                "type": "object",
                "properties": {"issue": {"type": "string"}, "why_it_matters": {"type": "string"},
                               "say_this_instead": {"type": "string"}},
                "required": ["issue", "why_it_matters", "say_this_instead"]}},
            "practice_next": {"type": "string"},
        },
        "required": ["one_line_summary", "strengths", "fixes", "practice_next"],
    }
    strengths_src = ("\n".join(f"- {d['name']} ({d['score']}/4): \"{d['quotes'][0]}\"" for d in strong)
                     if strong else "NONE. Return an empty strengths list; do not invent any.")
    weak_src = "\n".join(f"- {d['name']} ({d['score']}/4): {d['reason']} Practise: {d['coach']}" for d in weak)
    fired = [f"- {g['id']}: {g['reason']}" for g in gates if g["triggered"]]
    prompt = f"""You are an honest PM interview coach. Write feedback for the candidate from the grading below. The verdict is already decided: {roll['verdict']}. Do not change it or soften it.

Case: {case.id} {case.title} ({case.case_type}). Prompt: {case.prompt}

STRENGTHS you may mention (only these, each tied to its quote):
{strengths_src}

WEAKEST DIMENSIONS (base the 3 fixes on these, most important first, and use the practise tip):
{weak_src}

GATES TRIGGERED:
{chr(10).join(fired) or "none"}

CONTENT CHECK vs the casebook answer: {json.dumps({k: content.get(k) for k in ("answer_comment", "key_points_missed", "pitfalls_fallen_into")})}

Return: one_line_summary (one honest sentence); strengths (max 3, only from the list above); exactly 3 fixes, each with a concrete 'say_this_instead' line the candidate could have said in THIS case; practice_next (one specific drill)."""
    out = llm.parse_json(_grader_chat([{"role": "user", "content": prompt}], schema, think=False,
                                      temperature=0.3)["message"]["content"])
    if not strong:
        out["strengths"] = []
    out["verdict"] = roll["verdict"]
    return out


# ---------- entry point ----------

def evaluate(case: Case, session: dict, progress: Callable[[str], None] = print,
             level: str | None = None, grader: str | None = None) -> dict:
    level = level or session.get("level", "pm")
    from . import models
    gid = grader or models.settings()["grader_model"]
    if gid not in models.GRADERS:
        gid = models.DEFAULT_GRADER
    if gid != models.DEFAULT_GRADER:
        ok, why = models.can_grade(gid)
        if not ok:
            progress(why)
            gid = models.DEFAULT_GRADER
    GRADER.update(id=gid, session=f"{case.id}")
    progress(f"Grader: {models.GRADERS[gid]['label']}")
    timings = {}
    t0 = time.time()
    cand_turns = [t["text"] for t in session["turns"] if t["speaker"] == "Candidate"]
    checks = arith.check(cand_turns) if case.type_key == "guesstimate" else []
    extra = {}
    if checks:
        lines = [f"  {c['text']}  -> {'OFF by ' + str(c['ratio']) + 'x' + (' (candidate corrected it)' if c['corrected'] else '') if c['error'] else 'correct'}"
                 for c in checks]
        extra["calculation"] = "Arithmetic re-checked in code (trust this over your own maths):\n" + "\n".join(lines)
    stats = {
        "minutes": session["minutes"], "time_box": session["time_box"], "hints": session["hints"],
        "clarifications_unlocked": f"{len(session['revealed_clarifications'])}/{len(case.clarifications)}",
        "exhibits_unlocked": f"{len(session['revealed_exhibits'])}/{len(case.exhibits)}",
        "candidate_words": sum(len(t.split()) for t in cand_turns),
        "phases_touched": f"{len(session['phases_touched'])}/{len(phase_list(case.type_key))}",
        "level": level,
    }

    if gid.startswith("local:") and llm.INTERVIEW_MODEL != llm.GRADER_MODEL:   # same model: keep it loaded
        llm.unload(llm.INTERVIEW_MODEL)
    system = _system(case, session)
    progress("Scoring each dimension...")
    dims = grade_dimensions(case, session, system, extra, progress)
    timings["dimensions"] = round(time.time() - t0)
    progress("Checking gates...")
    t1 = time.time()
    gates = grade_gates(case, session, system, checks, progress, dims)
    timings["gates"] = round(time.time() - t1)
    roll = rollup(case.type_key, dims, gates, session.get("interventions", []), level)
    progress("Checking your answer against the casebook solution...")
    t1 = time.time()
    content = grade_content(case, session)
    timings["content"] = round(time.time() - t1)
    progress("Writing coaching notes...")
    t1 = time.time()
    coaching = coach(case, dims, gates, roll, content)
    timings["coaching"] = round(time.time() - t1)
    timings["total"] = round(time.time() - t0)
    return {
        "case_id": case.id, "level": level, "stats": stats, "timings": timings, "grader_model": gid,
        "verdict": roll["verdict"], "weighted_mean": roll["weighted_mean"], "rollup": roll,
        "tradeoff_profile": is_tradeoff(case),
        "dimensions": dims, "gates": gates, "arithmetic": checks,
        "answer_quality": content.get("answer_quality"), "content": content, "coaching": coaching,
    }
