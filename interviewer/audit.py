"""Score the interviewer, not the candidate: a per-session scorecard of how the interview was run.

  python3 -m interviewer.audit sessions/*.json                 # one summary row per session + averages
  python3 -m interviewer.audit sessions/x.json -v              # every check, with the turns that tripped it
  python3 -m interviewer.audit sessions/*.json --practice      # real sessions only
  python3 -m interviewer.audit sessions/*.json --write         # store the scorecard in session["interviewer_audit"]

The checks come from ../interviewer_behaviour_research (report.md, results/*.json): release discipline,
probing, commitment and twist handling, neutrality and turn shape. Everything here is regex, counting and
the session's own logs (tool calls, interventions, the grader report), with no model calls, so it is free
and repeatable. Heuristics flag turns to look at; they are not proof. Thresholds marked "inferred" in the
research (turn length, talk share, probe counts) are starting points to tune against real sessions.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

from .agent import COMMIT_RE, HEDGE_RE, OFFER_RE, PROBE_RE, REFUSAL_RE, SHOW_RE, STUCK_RE
from .casepack import Case, load_all
from .rubrics import HINT_RE

SEV_ORDER = {"critical": 0, "major": 1, "minor": 2}

# ---------- phrase patterns ----------

# Offers the agent's OFFER_RE misses: bare "Want the split?" / "Want to see …?" / "Shall I go on with …?".
BARE_OFFER_RE = re.compile(r"\b(want|need) (the|a|to see|me)\b[^.?!]*\?", re.I)
INTERP_RE = re.compile(
    r"\b(so the drop is|this time (it'?s|the drop)|which (suggests|means|points|tells)|what'?s left|"
    r"that leaves|the (last|biggest|main) (step|shift|drop|change) is|is where the|notably|clearly|"
    r"the real (issue|problem|cause)|so it'?s (not|the)|the (drop|problem|issue|change|loss) is "
    r"(happening|on|in|at|coming)|(is|that'?s) your (signal|answer|clue)|so it'?s the [^.?]{0,40}, not)\b", re.I)
# Questions that point the way instead of testing the candidate's claim.
STEER_RE = re.compile(r"(,|—|-) ?[\w-]+( [\w-]+){0,2}, [\w-]+( [\w-]+){0,2},? or something else\?|"
                      r"\bwhat (would you|do you want to|else would you) (split|cut|break) (it|that|this)? ?by\b|"
                      r"\bwhat about the\b[^?]*\?|\bwhat else do you have to work with\b", re.I)
PRAISE_RE = re.compile(
    r"\b(great|good (point|question|start|thinking|catch|answer|idea)|nice|excellent|perfect(?! orders?)|well done|"
    r"spot on|right track|(^|[.!?] )exactly[.!]|love (that|it)|smart|impressive|that'?s (correct|a good))\b", re.I)
ACK_RE = re.compile(r"^\W*(okay|ok|sure|right|got it|mm-?hm|mhm|yes|go on|go ahead|keep going|noted|thanks)"
                    r"[\s,.!]*((go on|go ahead|keep going|continue)[.!]?)?\s*$", re.I)
PUSH_RE = re.compile(r"\b(if you had to|pick one|which one|choose|decide|your call|which would you|commit)\b", re.I)
GOTCHA_RE = re.compile(r"\b(your (plan|idea|answer) (fails|won'?t work|doesn'?t work)|obviously|you missed|"
                       r"you forgot|that'?s wrong)\b", re.I)
CONDITIONAL_RE = re.compile(r"\b(suppose|what if|imagine|let'?s say|say that|now|turns out|actually|but)\b", re.I)
TIME_RE = re.compile(r"\b(minutes? (left|to go|remaining)|running (out|short) of time|time is (up|nearly)|"
                     r"wrap (it )?up|towards a recommendation|we'?re (nearly|almost) (out|done)|that'?s time)\b", re.I)
FIXED_LINE_RE = re.compile(r"^(sorry, i lost you|\(setup problem)", re.I)
# "I don't understand what that means" asks for a clarification, which isn't help (the brief says so).
MEANING_RE = re.compile(r"\b(what (this|that|it|they) means?|what (is|does|do you mean)|explain|what'?s)\b", re.I)
RESTATE_ASK_RE = re.compile(r"\b(am i right|is that right|correct\?|right\?|did i get that)\b", re.I)
STOP = set("""about after again against their there these those which would could should while where when
what with from this that have been were they them then than into your yours also just only some such very
more most other over under because before being between both each doing does done here itself itself
let's okay right sure think going like really thing things want need says said said""".split())


_DF: dict[str, int] | None = None


def _rare_words(cases: dict[str, Case]) -> dict[str, int]:
    """Document frequency of each word across all cases' text, so the leak check can ignore words common
    to many cases ("recommendation", "issue", "second") and keep the case-specific ones ("pre-link", "4.98")."""
    global _DF
    if _DF is None:
        _DF = {}
        for c in cases.values():
            text = " ".join([c.prompt, c.context, c.answer, " ".join(c.pitfalls)]
                            + [q.q + " " + q.a for q in c.clarifications] + [e.body for e in c.exhibits])
            for w in _words(text):
                _DF[w] = _DF.get(w, 0) + 1
    return _DF


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z'-]{4,}", t.lower()) if w not in STOP}


def _nums(t: str) -> set[str]:
    """Numbers in a line, leaving out years and exhibit ids/titles ("E1, Order base, Q2 FY27")."""
    t = re.sub(r"\b(E\d+|Q[1-4]|FY ?\d{2,4}|(19|20)\d{2})\b", "", re.sub(r"putting up [^.]*\.", "", t, flags=re.I))
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", t)}


def _clip(t: str, n: int = 110) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _check(name, severity, ok, value, target, evidence=None, note=""):
    return {"name": name, "severity": severity, "status": "na" if ok is None else ("pass" if ok else "fail"),
            "value": value, "target": target, "evidence": evidence or [], "note": note}


def minutes_used(session: dict) -> float:
    return session.get("minutes") or 0


def time_box(session: dict, case: Case | None) -> float:
    return session.get("time_box") or (case.time_box_minutes if case else 30)


# ---------- the audit ----------

def audit(session: dict, case: Case | None = None) -> dict:
    case = case or load_all().get(session["case_id"])
    tk = case.type_key if case else ""
    turns = session.get("turns") or []
    # Pair each interviewer reply (after the opening prompt) with the candidate turn it answers.
    pairs: list[tuple[int, str, dict]] = []   # (turn index, previous candidate text, interviewer turn)
    prev_c = ""
    for i, t in enumerate(turns):
        if t["speaker"] == "Candidate":
            prev_c = t["text"]
        elif i > 0:
            pairs.append((i, prev_c, t))
    cand = [(i, t["text"]) for i, t in enumerate(turns) if t["speaker"] == "Candidate"]
    iv_text = [t["text"] for _, _, t in pairs]
    checks = []

    # Where the twist was put: the API interviewer logs a present_twist call; the local engine says
    # "Let me push on that" before it.
    twist_at = next((i for i, _, t in pairs if any(c.get("name") == "present_twist"
                                                    for c in (t.get("meta") or {}).get("tools", []))), None)
    if twist_at is None and session.get("twist"):
        twist_at = next((i for i, _, t in pairs if re.search(r"let me push on that", t["text"], re.I)), None)
    pre_twist = [(i, c, t) for i, c, t in pairs if twist_at is None or i < twist_at]

    # --- release discipline ---
    opening = turns[0]["text"] if turns and turns[0]["speaker"] == "Interviewer" else ""
    checks.append(_check("opening_hides_twist", "critical", not re.search(r"\btwist\b|follow-?up:", opening, re.I),
                         _clip(opening, 90), "the prompt read out without the follow-up twist"))
    untrig = []
    # The tool log keeps refused share_exhibit calls too; only count a table the first time it really went up.
    pending = set(session.get("revealed_exhibits") or [])
    for i, c, t in pairs:
        meta = t.get("meta") or {}
        shared = []
        for x in meta.get("tools", []):
            eid = str(x.get("args", {}).get("exhibit_id", "")).strip().upper()
            if x.get("name") == "share_exhibit" and eid in pending:
                shared.append(eid)
                pending.discard(eid)
        shared += [r for r in meta.get("revealed", []) if str(r).startswith("E")]
        if shared and not SHOW_RE.search(c):
            untrig.append((i, f"{', '.join(map(str, shared))} after: {_clip(c, 70)}"))
    checks.append(_check("table_shown_unasked", "critical", not untrig, len(untrig), "0 tables shown without "
                         "a request to see data", untrig))

    offers = [(i, _clip(t["text"])) for i, _, t in pairs
              if OFFER_RE.search(t["text"]) or BARE_OFFER_RE.search(t["text"])]
    checks.append(_check("offered_data", "major", not offers, len(offers), "0 offers of what else is in the notes",
                         offers))

    interp = [(i, _clip(t["text"])) for i, _, t in pre_twist if INTERP_RE.search(t["text"])]
    checks.append(_check("interpreted_for_candidate", "major", not interp, len(interp),
                         "0 turns that compare, sum or say what a fact implies", interp))

    # Answer-key words the interviewer could not have got from its own pack or from the candidate.
    leak = []
    if case:
        shareable = _words(" ".join([case.prompt, case.context] + [q.q + " " + q.a for q in case.clarifications]
                                    + [e.title + " " + e.body for e in case.exhibits]))
        df = _rare_words(load_all())
        key = {w for w in _words(" ".join([case.answer, " ".join(case.pitfalls)])) - shareable if df.get(w, 0) <= 2}
        said = set()
        for i, t in enumerate(turns):
            if twist_at is not None and i >= twist_at:
                break
            if t["speaker"] == "Candidate":
                said |= _words(t["text"])
            elif i > 0 and not FIXED_LINE_RE.search(t["text"]):
                hit = (_words(t["text"]) & key) - said
                if hit:
                    leak.append((i, f"{', '.join(sorted(hit))}: {_clip(t['text'], 80)}"))
    checks.append(_check("answer_key_words_before_candidate", "critical", None if not case else not leak,
                         len(leak), "0 answer-key words the candidate hadn't said (check by hand: may be benign)",
                         leak))

    if tk == "guesstimate":
        said_nums: set[str] = set(_nums(case.prompt)) if case else set()
        given = []
        for i, t in enumerate(turns):
            if twist_at is not None and i >= twist_at:
                break
            if t["speaker"] == "Candidate":
                said_nums |= _nums(t["text"])
            elif i > 0:
                new = _nums(t["text"]) - said_nums
                if new:
                    given.append((i, f"{', '.join(sorted(new))}: {_clip(t['text'], 80)}"))
        checks.append(_check("numbers_given_in_guesstimate", "critical", not given, len(given),
                             "0 numbers the candidate didn't say (driver values are theirs; public figures "
                             "they named are fine)", given))

    steer = [(i, _clip(t["text"])) for i, _, t in pre_twist if STEER_RE.search(t["text"])]
    checks.append(_check("no_steering_questions", "major", not steer, len(steer),
                         "0 questions that point at the next cut or list options", steer))

    # --- probing ---
    probes = [(i, c, t) for i, c, t in pre_twist if PROBE_RE.search(t["text"]) and "?" in t["text"]]
    acks = [i for i, _, t in pre_twist if ACK_RE.match(t["text"].strip())]
    n_pre = max(1, len(pre_twist))
    want = 5 if tk == "situational" else 3
    checks.append(_check("probes", "major", len(probes) >= want, len(probes),
                         f">= {want} probes that make the candidate defend or test a claim (inferred)",
                         [(i, _clip(t["text"])) for i, _, t in probes]))
    dependent = [(i, t) for i, c, t in probes if len((_words(t["text"]) & _words(c))) >= 1]
    checks.append(_check("answer_dependent_probes", "major", None if not probes else len(dependent) / len(probes)
                         >= 0.6, f"{len(dependent)}/{len(probes)}", ">= 60% of probes reuse the candidate's own "
                         "words (inferred)"))
    checks.append(_check("bare_acknowledgements", "minor", len(acks) / n_pre <= 0.4, f"{len(acks)}/{n_pre}",
                         "<= 40% of replies are just 'Okay, go on' (inferred)"))

    many = []
    for i, c, t in pairs:
        asks = c.count("?") + len(re.findall(r",? or (has|have|was|were|did|is|are|could)\b", c, re.I))
        if asks >= 3 and not re.search(r"\b(mean|definition|defined|count(ed)?)\b", c, re.I):
            if not re.search(r"\b(which|first|prioriti|start)\b", t["text"], re.I):
                many.append((i, _clip(c, 80)))
    checks.append(_check("prioritise_when_listing", "minor", not many, len(many),
                         "a 'which first?' when the candidate lists several things", many))

    # --- commitment, twist and close ---
    commits = [i for i, c in cand if COMMIT_RE.search(c)]
    first_commit = commits[0] if commits else None
    if twist_at is not None:
        checks.append(_check("commitment_before_twist", "critical", first_commit is not None and
                             first_commit < twist_at, f"commit turn {first_commit}, twist turn {twist_at}",
                             "a committed call/estimate/root cause before the twist"))
        tw = turns[twist_at]["text"]
        checks.append(_check("twist_not_gotcha", "major", not GOTCHA_RE.search(tw), _clip(tw, 90),
                             "worded as a condition about the world, not the candidate's mistake"))
        after = [t for i, _, t in pairs if i > twist_at and "?" in t["text"]]
        hi = 3 if session.get("level") == "senior" else 2
        checks.append(_check("post_twist_followups", "minor", 1 <= len(after) + 1 <= hi + 1, len(after),
                             f"0-{hi} follow-up questions after the twist"))
    elif case and case.twists and session.get("landed") is False:
        checks.append(_check("commitment_before_twist", "critical", None, "no twist reached", ""))
    last_iv = pairs[-1][2] if pairs else {"text": "", "meta": {}}
    closed = any(c.get("name") == "end_interview" for c in (last_iv.get("meta") or {}).get("tools", [])) \
        or bool(re.search(r"\b(that'?s time|thanks for (your time|doing this)|i'?ll put your feedback)\b",
                          last_iv["text"], re.I))
    abandoned = not session.get("landed") and not closed
    checks.append(_check("commitment_reached", "critical", None if abandoned and not commits else
                         bool(commits) or session.get("landed") is True,
                         f"{len(commits)} committing turns, landed={session.get('landed')}",
                         "the candidate gave a call before the end"))

    hedges = []
    for i, c, t in pairs:
        if HEDGE_RE.search(c) and not PUSH_RE.search(t["text"]):
            hedges.append((i, _clip(c, 80)))
    checks.append(_check("hedge_not_accepted", "major", not hedges, len(hedges),
                         "'it depends' always gets 'if you had to pick?'", hedges))

    minutes, box = minutes_used(session), time_box(session, case)
    if minutes >= 0.8 * box:
        warned = any(TIME_RE.search(t) for t in iv_text)
        checks.append(_check("time_warning", "minor", warned, f"{minutes:.0f}/{box} min",
                             "a neutral time warning once past 80% of the time box"))

    # --- help ---
    helped_turns = {iv["turn"] for iv in session.get("interventions", [])
                    if iv.get("kind") in ("nudge", "redirect", "rescue")}
    unlogged, n_c = [], 0
    for i, t in enumerate(turns):
        if t["speaker"] == "Candidate":
            n_c += 1
            stuck = HINT_RE.search(t["text"]) or (STUCK_RE.search(t["text"]) and not MEANING_RE.search(t["text"]))
            if stuck and n_c not in helped_turns:
                unlogged.append((i, _clip(t["text"], 80)))
    checks.append(_check("help_logged_when_stuck", "major", not unlogged, len(unlogged),
                         "every stuck or help-asking turn gets logged help", unlogged))
    refusals = [(i, _clip(t["text"])) for i, _, t in pairs if REFUSAL_RE.search(t["text"])]
    checks.append(_check("no_blunt_refusal", "minor", not refusals, len(refusals),
                         "never 'I'm not going to … for you'", refusals))

    # --- neutrality and turn shape ---
    praise = []
    for i, c, t in pairs:
        if PRAISE_RE.search(t["text"]) and not RESTATE_ASK_RE.search(c):
            praise.append((i, _clip(t["text"])))
    checks.append(_check("no_praise_or_track_signal", "major", not praise, len(praise), "0", praise))
    multi = [(i, _clip(t["text"])) for i, _, t in pairs if t["text"].count("?") >= 2]
    checks.append(_check("one_question_per_turn", "major", len(multi) <= max(1, 0.1 * len(pairs)), len(multi),
                         "<= 10% of turns ask two or more questions", multi))
    words = [len(t.split()) for t in iv_text] or [0]
    cw = sum(len(c.split()) for _, c in cand)
    share = sum(words) / max(1, sum(words) + cw)
    checks.append(_check("turn_length", "minor", statistics.median(words) <= 25,
                         f"median {statistics.median(words):.0f}, max {max(words)} words", "median <= 25 (inferred)"))
    checks.append(_check("talk_share", "minor", share <= 0.35, f"{share:.0%}", "<= 35% of words (inferred)"))
    seen, repeats = {}, []
    for i, _, t in pairs:
        for q in re.findall(r"[^.?!]*\?", t["text"]):
            k = " ".join(sorted(_words(q)))
            if len(k) > 12 and k in seen:
                repeats.append((i, _clip(q, 80)))
            seen.setdefault(k, i)
    checks.append(_check("no_repeated_question", "minor", not repeats, len(repeats), "0", repeats))

    # --- evidence coverage (needs the grader's report) ---
    rep = session.get("report") or {}
    untested = [d["name"] for d in rep.get("dimensions", []) if not d.get("quotes") and d.get("score", 4) <= 1.5]
    if rep.get("dimensions"):
        checks.append(_check("every_dimension_tested", "major", not untested, ", ".join(untested) or "none",
                             "no dimension left with no candidate evidence (ask one open question before the "
                             "close)"))

    fails = [c for c in checks if c["status"] == "fail"]
    return {"case_id": session.get("case_id"), "type": tk, "model": session.get("interviewer_model") or "local",
            "source": session.get("source", "simulated"), "turns": len(pairs),
            "critical": sum(c["severity"] == "critical" for c in fails),
            "major": sum(c["severity"] == "major" for c in fails),
            "minor": sum(c["severity"] == "minor" for c in fails),
            "checks": checks}


# ---------- CLI ----------

def _print(a: dict, name: str, verbose: bool) -> None:
    print(f"\n{name}  {a['case_id']} {a['type']}  {a['model']}  ({a['source']}, {a['turns']} replies)  "
          f"fails: {a['critical']} critical, {a['major']} major, {a['minor']} minor")
    for c in sorted(a["checks"], key=lambda c: (c["status"] != "fail", SEV_ORDER[c["severity"]])):
        if c["status"] == "pass" and not verbose:
            continue
        mark = {"pass": "ok  ", "fail": "FAIL", "na": "n/a "}[c["status"]]
        print(f"  {mark} {c['severity']:8} {c['name']:34} {c['value']}   [{c['target']}]")
        if verbose or c["status"] == "fail":
            for i, ev in c["evidence"][:4]:
                print(f"         turn {i}: {ev}")


def main(argv: list[str]) -> None:
    verbose, write = "-v" in argv, "--write" in argv
    only_practice = "--practice" in argv
    paths = [Path(p) for p in argv if not p.startswith("-")]
    if not paths:
        print(__doc__)
        return
    cases = load_all()
    rows = []
    for p in paths:
        try:
            s = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(s, dict) or "turns" not in s:
            continue
        if only_practice and s.get("source") != "practice":
            continue
        a = audit(s, cases.get(s.get("case_id")))
        rows.append(a)
        _print(a, p.name, verbose)
        if write:
            s["interviewer_audit"] = a
            p.write_text(json.dumps(s, indent=1, ensure_ascii=False))
    if len(rows) > 1:
        print(f"\n{len(rows)} sessions. Fail rate per check:")
        names = sorted({c["name"] for r in rows for c in r["checks"]},
                       key=lambda n: next(SEV_ORDER[c["severity"]] for r in rows for c in r["checks"] if c["name"] == n))
        for n in names:
            cs = [c for r in rows for c in r["checks"] if c["name"] == n and c["status"] != "na"]
            if cs:
                f = sum(c["status"] == "fail" for c in cs)
                print(f"  {cs[0]['severity']:8} {n:34} {f:3}/{len(cs):<3} {f / len(cs):5.0%}")


if __name__ == "__main__":
    main(sys.argv[1:])
