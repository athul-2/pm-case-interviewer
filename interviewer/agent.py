"""API interviewer: a capable model (Claude or Gemini) runs the interview itself.

Unlike the local pipeline (engine.py), the model sees the whole conversation and decides what to say. It
still never sees the answer key: its brief holds only what an interviewer may share (prompt, clarifying
answers, exhibit summaries, background with answer-revealing sentences removed, twists). It acts through
tools, so the app and the grader get the same logs as before:

  share_exhibit(id)          -> exhibit event on screen; logged as a clarification
  record_help(kind, step)    -> logged as nudge / redirect / rescue (graded by the hint taxonomy)
  present_twist(id)          -> marks the recommendation as landed; the twist is the next thing said
  end_interview()            -> closes the case

Every API call goes through models.charge_guard first, so the monthly budget can't be crossed.
"""
from __future__ import annotations

import json
import random
import re
import time
from typing import Iterator

from . import models
from .casepack import Case
from .engine import HELD_Q_RE, USERS_ASK_RE, Event, Interview, Turn
from .matcher import exhibit_summary
from .rubrics import HINT_RE, LEVELS, phase_list

BANNED_RE = re.compile(
    r"[^.?!]*\b(tell me what you (see|make of it|think it (says|means))|what does (it|this|that) tell you|"
    r"take a look and tell me|let me give you|there you go,? that'?s|what would you (like|want) to (check|look at|explore|dig into) next|let me check that\'?s the right)[^.?!]*[.?!]?",
    re.I)
REFUSAL_RE = re.compile(r"\bi'?m not going to (do|give|work)[^.?!,]*?for you\b[,;]?\s*(but\s*)?", re.I)
# Advertising what else is in the notes ("I've got which categories complain most, if that's useful") hands
# the candidate a lead they didn't find. Whole sentences are dropped.
OFFER_RE = re.compile(
    r"[^.?!]*\b(i'?ve got|i have|i do have|i can (share|give|show|tell)|there'?s (some|data))\b[^.?!]*"
    r"\bif (that'?s|that would be|that'd be|it'?s|it would be|it'd be|it helps|you('d| would)? (find|like|want))"
    r"[^.?!]*[.?!]?"
    r"|[^.?!]*\b(want me to|would you like me to|shall i|should i) (share|show|put|pull|give|tell)[^.?!]*\?",
    re.I)


# Saying what a fact means is the candidate's job ("The drop is happening on the product page itself").
INTERP_RE = re.compile(
    r"(?:(?<=[.!?])\s+|^)[^.?!]*\b(the (drop|problem|issue|shift|change|decline) is (happening|coming|on|in|at|from|driven)|"
    r"which (means|suggests|tells us|points)|that (means|suggests|points)|so (the|this|that) (drop|problem|issue|rate) is|"
    r"is what'?s (down|driving|causing)|that'?s where (the|it)|it'?s just being)\b[^.?!]*[.!]",
    re.I)
SO_CLAUSE_RE = re.compile(r",\s*so (the|it|this|that|there)\b[^.?!]*(?=[.!])", re.I)


def names_called(calls: list[dict], names: set[str]) -> bool:
    return any(c.get("name") in names for c in calls)


def _drop_banned(text: str) -> str:
    """Remove phrases that make the interviewer sound scripted or unhelpful, whatever the model says."""
    no_offer = OFFER_RE.sub("", re.sub(r"\bthere you go\b\s*[—–,:-]*\s*", "", text or "", flags=re.I))
    out = REFUSAL_RE.sub("", BANNED_RE.sub("", INTERP_RE.sub("", SO_CLAUSE_RE.sub("", no_offer))))
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;.")
    if out and out[-1:] not in ".?!":
        tail = out[-25:]   # use the punctuation that followed the kept text, not the end of the dropped part
        i = (text or "").find(tail)
        ch = text[i + len(tail)] if i >= 0 and i + len(tail) < len(text) else ""
        out += ch if ch in ".?!" else "."
    out = out[:1].upper() + out[1:] if out else out
    return out or "Okay, go on."


STUCK_RE = re.compile(
    r"\b(i('m| am) (stuck|lost|confused|not sure)|i don'?t (know|understand|get)|no idea|not sure (what|where|how)|"
    r"where (do|should) i (go|look|start)|what (should|do) i do|can'?t be right|doesn'?t add up|makes no sense)\b", re.I)
SHOW_RE = re.compile(
    r"\b(see|show|look at|share|pull up|send|data|numbers?|figures?|breakdown|break(ing)? (it )?down|split|trend|"
    r"funnel|chart|table|exhibit|graph|dashboard|by (platform|city|cities|segment|channel|method|version|day|week))\b",
    re.I)
# A committed call: root cause, recommendation, decision, metric or final estimate. Twists wait for one.
COMMIT_RE = re.compile(
    r"\b(i('d| would) (recommend|go with|pick|choose|ship|launch|not ship|hold|roll ?back|prioriti[sz]e|delay)|"
    r"my (recommendation|call|decision|answer|estimate|final (number|answer|estimate)|north star)|"
    r"i recommend|final (number|estimate|answer)|(so|overall),? (the|my) (root cause|answer|estimate)|"
    r"the root cause is|in summary|to summari[sz]e|to (close|conclude|wrap up)|bottom line|"
    r"(we|i) should (ship|launch|go|build|not|roll ?back|fix|revert)|(roll ?back|revert) the|^immediately\b|"
    r"(two|three|both|the main|the root) causes?\b|causes? (are|combined)|"
    r"(roughly|about|so|that'?s) [\d.,]+ ?(lakh|crore|million|mn|k|thousand|billion)? ?(in total|total|per day|a day|daily))",
    re.I)
HEDGE_RE = re.compile(r"\b(it (all )?depends|could go either way|both (options|have)|hard to say)\b", re.I)
# Follow-ups that make the candidate defend, test or extend their own claim.
PROBE_RE = re.compile(
    r"\b(why( that| this| not| over| would| do you| is)?\b|how would you (validate|verify|check|confirm|test|know|"
    r"measure|tell|separate|size|compare|decide|weigh|choose|prioriti[sz]e)|what data would|where does (that|this|the)\b[^?]{0,30}come from|"
    r"what makes you (say|think|sure)|what('s| is) (that|this) based on|what would (change your mind|make you|you give up|you need)|"
    r"what are you giving up|what do you give up|does that (explain|account for)|how do you know|"
    r"how (big|many|much) is (that|this)|what if\b|suppose\b|imagine\b|which (one|would you) .{0,40}first|"
    r"if you had to (pick|choose|decide|keep)|if you could (only )?(ship|keep|pick)|how could .{0,40}"
    r"(game|inflate|gamed)|what('s| is) the (risk|downside|trade-?off)|who (pays|loses)|walk me through (why|how))",
    re.I)
NUM_WORD_RE = re.compile(r"\b(\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                         r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|"
                         r"fifty|half|percent)\b", re.I)
# Help that carries data is an answer, not a hint. Fallbacks when a help reply brings in numbers.
HELP_FALLBACK = {
    "nudge": {"rca": "Take a step back. What ways of splitting the drop haven't you tried yet?",
              "_": "Take a step back. Which part of your structure haven't you covered yet?"},
    "redirect": {"rca": "It might help to split the drop by who and where it's happening, and see if it's uniform.",
                 "_": "It might help to go back to your structure and take the next step you haven't done."},
    "rescue": {"rca": "Try segmenting the drop, by geography, user type and platform, and look for the one slice "
                      "that moved much more than the rest.",
               "_": "Go back to your structure, pick the step you skipped, and walk me through it out loud."},
}
VAGUE_DATA_RE = re.compile(r"\b(give|show|share|send|tell|get) (me|us) (that|the|this|those|some|any) "
                           r"(data|numbers?|figures?|info(rmation)?|breakdown|split)\b|\b(that|this) data\b", re.I)
VAGUE_WORDS = {"that", "this", "those", "with", "have", "give", "just", "okay", "show", "what", "like", "want", "look", "your", "from", "them", "then",
               "data", "numbers", "number", "figures", "table", "breakdown", "split", "share", "please", "could",
               "change", "changes", "before", "after", "illustrative", "exhibit", "drop", "dropped"}
LEVEL_NAMES = {"apm": "associate PM", "pm": "PM", "senior": "senior PM"}
STOP = {"about", "after", "their", "there", "these", "those", "which", "would", "could", "should", "while",
        "where", "other", "every", "first", "since", "until", "under", "known", "because", "between", "during"}


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z-]{4,}", t.lower()) if w not in STOP}


def safe_context(case: Case) -> str:
    """The case's public background, minus sentences that point at the answer. A sentence is dropped if it
    shares two or more distinctive words with the answer key that the shareable material (prompt,
    clarifying answers, exhibit titles) doesn't already use, or if it reads as a hint ("flag", "known
    dip"). A1's "Diwali ... a known food-delivery dip as households eat at home" is the case that prompted it."""
    ctx = re.sub(r"\(?https?://\S+\)?", "", case.context or "")
    ctx = re.sub(r"\([^)]*\.(json|md)[^)]*\)", "", ctx)
    key = _words(" ".join([case.answer or "", " ".join(case.pitfalls or []),
                           " ".join(str(t.get("text", "")) for t in (case.walkthrough or [])
                                    if t.get("speaker") == "Candidate")]))
    shareable = _words(" ".join([case.prompt, *(c.q + " " + c.a for c in case.clarifications),
                                 *(e.title for e in case.exhibits)]))
    risky = key - shareable
    keep = []
    for s in re.split(r"(?<=[.;])\s+", re.sub(r"\s+", " ", ctx).strip()):
        if not s:
            continue
        hits = _words(s) & risky
        if len(hits) >= 2 or re.search(r"\b(flag|known (dip|cause|issue)|likely cause|explains)\b", s, re.I):
            continue
        keep.append(s)
    return " ".join(keep)[:2500]


TOOLS = [
    {"name": "share_exhibit",
     "description": "Put one of your data tables on the candidate's screen. Only when they ask to see data, "
                    "numbers or a breakdown. For a yes/no or factual question, answer in words instead.",
     "schema": {"type": "object", "properties": {"exhibit_id": {"type": "string"}}, "required": ["exhibit_id"]}},
    {"name": "record_help",
     "description": "Call this whenever you give the candidate help beyond answering their question: a guiding "
                    "question (nudge), naming the step they should do now (redirect), or explaining what a step "
                    "should cover (rescue). Use the level the turn note names. It is logged for grading.",
     "schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["nudge", "redirect", "rescue"]},
         "step": {"type": "string"}}, "required": ["kind", "step"]}},
    {"name": "present_twist",
     "description": "Call this once the candidate has given their final recommendation or answer to the whole "
                    "case. It returns the follow-up twist; put it to them in your own words as your reply.",
     "schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "end_interview",
     "description": "Close the interview: after the candidate has responded to the twist, or when time is up "
                    "and they have landed. Say a short closing line, like \"Thanks, that's time.\"",
     "schema": {"type": "object", "properties": {}, "required": []}},
]


# What changes by case type. Each section is added to the brief after the general rules and wins where
# they differ (from ~/Desktop/Cases/interviewer_behaviour_research/report.md, section 3).
TYPE_RULES = {
    "rca": """
This is a root-cause case: you hold the data, they choose every cut
- Data-integrity and definition questions: answer straight when asked, and never volunteer them.
- A vague data ask ("what else do we have?", "any other data?") gets "What would you like to look at, and why?", not a list of what you have.
- When they name a cause, don't confirm it. Ask how they'd validate it, or whether it explains the whole drop ("Does that explain the full 20%, or only part of it?").
- You may push back with facts they already have ("You said seasonality, but iOS barely moved. How does that fit?"), never with new ones.
- Once they've stated the cause, if they haven't said, ask what they'd do right now, the fix, and how they'd know it's fixed.
""",
    "product_design": """
Probes for a design case
- After they pick a segment, pain point or solution, ask why that one over the others they listed, in their words ("Why commuters over students?").
- Once they have a solution, ask for the first version if they haven't scoped one ("If you could ship one thing first, what is it?"), and how they'd measure success and what they'd watch so it doesn't hurt something else, if they haven't said.
- If they check their plan of steps ("I'll start with users, then pain points, okay?"), a short "Sounds good, go ahead" is fine. Content (who, which pain, which idea) is still theirs.
""",
    "product_strategy": """
This is a strategy case: the choices are theirs, the context is yours
- Goals, constraints, budget and facts in your notes: answer straight when asked.
- Options, markets, build/partner/buy and the final pick are theirs. If they ask what you'd do: "That's your call. Which would you pick, and why?" Never hint at a preferred option.
- Once they pick, push back on that pick as a stakeholder, with the same force whatever they picked ("Putting on my CFO hat: this burns cash for two years. Why fund it?"). Hold the pushback for one exchange unless they give a new reason.
- Ask what would make them change course or kill it, if they haven't said.
""",
    "guesstimate": """
This is a guesstimate: every assumption is theirs. These rules override the general ones above.
- Scope questions (the unit, the period, the geography, what counts): answer straight. If they ask for a widely published figure by name ("India's population?"), give it once.
- Driver values (penetration, frequency, shares, conversion, rates) are theirs. If they ask for one, or whether an assumption is okay: "Your call. State it and tell me why." Only give a number when a prepared answer or table of yours covers exactly what they asked.
- Accept top-down or bottom-up and never prefer one. Acknowledge assumptions neutrally ("Okay, noted"), never "that's reasonable".
- Don't correct their arithmetic or say a number looks off, and don't ask them to walk you through it. Once they have a total, ask how they'd sanity-check it, if they don't.
- Get a final number with units before you call present_twist. After it, you may challenge one assumption as a condition ("Suppose frequency were half that. What changes?").
""",
    "metrics": """
This is a metrics case: the metric choice and the call are theirs
- Goal, surface, constraints and what exists today: answer straight. The north star, levers, guardrails and any ship decision are theirs.
- If they list several metrics: "If you could keep only one, which?"
- Probe the metric they chose: how a team could push it up without users being better off, and what guardrail they'd set, if they haven't said.
- In an "A up, B down: do we ship?" case, get a clear ship, don't ship or ramp call first. A blend or "run it longer" with no direction isn't a call: ask which way they'd go today.
- Readout data: one segment or period per ask, and never say what it means.
""",
    "situational": """
This is a situational case: the follow-ups are the interview
- Answer facts about the scenario straight, one at a time.
- After each decision, go one layer deeper, one question per turn: why that; what they give up and who pays for it; what they'd need to know and how they'd check it in the next hour; what would change their mind; who they'd tell first.
- Push back as the stakeholder in the scenario and hold it for one exchange unless they give a new reason ("The business head still says Friday is fixed.").
- Never offer options, name a value or leadership principle for them, or say which call you'd make.
- Every layer ends with a clear decision. If they sit on the fence, ask them to choose.
- Before the end, if they haven't said, ask what they'd do if it went wrong anyway, and what they'd change next time.
""",
}


class AgentInterview(Interview):
    MAX_OUT = 1500

    def __init__(self, case: Case, level: str = "pm", model_id: str = "claude-opus-5-5"):
        if level not in LEVELS:
            raise ValueError(f"level must be one of {list(LEVELS)}")
        m = models.MODELS[model_id]
        if m["provider"] == "ollama":
            raise ValueError("AgentInterview is for API models")
        self.case, self.level, self.model_id, self.meta_model = case, level, model_id, m
        self.sim_words_per_min = None
        self.phases = phase_list(case.type_key)
        self.turns: list[Turn] = []
        self.revealed_q: list[str] = []
        self.revealed_e: list[str] = []
        self.phases_touched: list[str] = []
        self.hints = 0
        self.interventions: list[dict] = []
        self.landed = False
        self.twist_id: str | None = None
        self.twist_turns = 0
        self.time_nudged = False
        self.started = time.time()
        self.done = False
        self.cost = 0.0
        self.budget_stopped = False
        self._helped_this_turn = False
        self._edited_last: str | None = None
        self.rng = random.Random(int(self.started))
        self.system = self._brief()
        self._history: list = []          # provider-native messages
        self._client = None

    # ---------- the brief ----------

    def _brief(self) -> str:
        c = self.case
        design = c.type_key == "product_design"
        held = lambda q: design and bool(HELD_Q_RE.search(q.q))
        qa = "\n".join(f"- {q.id}. If asked: {q.q}\n  Answer: {q.a}"
                       + ("\n  HOLD: only after they've named their own guess; then confirm or correct just that part"
                          if held(q) else "")
                       for q in c.clarifications) or "(none)"
        design_rules = """
This is a product design case: the candidate owns the users, segments, pain points and solution ideas
- Those are what's being tested, so don't supply them. If they ask who the users are, which segments, what the pain is, or why users feel this way, before giving their own view, hand it back in one line: "Who do you think is complaining most?" or "How would you segment them?"
- Once they've named their own guess, you may confirm or correct only the part they named, from your notes ("Yes, group orders are a big share."). Don't add other segments, categories or causes they didn't mention.
- Clarifications marked HOLD below follow this rule. Goal, scope, constraints and what exists today you can answer straight.
- Never react to their ideas with data that steers them ("that one complains most"), and never suggest a solution, feature or metric.
""" if design else ""
        ex = "\n".join(f"- {e.id}: {exhibit_summary(e, 700)}" for e in c.exhibits) or "(none)"
        n_tw = len(c.twists)
        steps = ", ".join(k for k, _ in self.phases)
        bg = safe_context(c) or "(none)"
        return f"""You are an experienced product management interviewer running a live {c.case_type} case interview, out loud, with one candidate. Your replies are spoken by text-to-speech, so write plain conversational sentences: no lists, markdown, headings or emoji.

How a real case interviewer talks
- Brief and natural: usually one or two sentences, like a person across the table. Respond to what they actually said.
- Answer in words. When they ask about something your notes or tables cover, give the fact yourself, the way an interviewer reads it off their sheet.
- Reveal one layer at a time. Give the smallest true answer to the exact question, then stop and let them ask the next one:
  - A prepared answer often covers several questions at once ("platforms and any new release?"). Say only the part they asked about. Asked about platforms -> the platforms, not whether there was a release. Asked about competitors -> competitors, not outages or capacity.
  - Never add a second fact "while you're at it", and never end with what the facts add up to ("so the drop is on the product page").
  - "Did anything change or ship?" -> confirm whether it did and what it was, in a few words ("Yes, an Android release went out to everyone last Friday."). Not what it changed, unless they ask.
  - "What changed in it?" -> the main change only. Error logs, side effects and causes only if they ask for those.
  - "Have we seen this before?" -> the fact ("Last Diwali week, orders were about 8% below the prior four weeks."). Not a comparison with now.
  - A data question -> the number they asked for. Not other rows or other periods.
  - A breakdown across several groups ("split by card, UPI and COD") -> put the table up, or say every group they asked for. Never answer with only some of the groups.
- Never do their thinking. Don't compare, add up, subtract or interpret numbers for them, don't say what a fact implies ("so the drop is real", "this time it's bigger", "what's left is 12 points"), and don't point at where to look ("the last step is where the shift is"). If their maths is off, ask them to walk you through it rather than correcting it.
- Put a table on screen (share_exhibit) only when they ask to see data, numbers or a breakdown. If the request is vague ("can you give me that data?", "show me the numbers") and doesn't say which breakdown or metric, ask which one they want instead of choosing a table for them. Say one short line ("Sure, I'll put that up."); don't read it out or ask what it tells them.
- Make them prioritise. If they list several things to check at once, ask which first and why, then answer that one.
- Keep the case shape. If they jump into data before saying how they'll approach the problem, ask once how they plan to structure it. After that they drive the order.
- When they play the problem back, confirm it or correct the specific detail.
- When they check a hypothesis, assumption or choice of theirs ("It's internal, right?"), don't judge it: "Your call."
- When they don't understand a fact you gave, explain what it is in plain words (that's a clarification, not help). Don't add what it means for the case.
- When they ask for something you have no data for, say so naturally ("Nothing unusual there") or ask what they'd expect to see, and stop. Never invent facts.
- Never advertise what else is in your notes ("I've got X, if that's useful", "Want me to share Y?"). They find the right questions; that's part of the test.
- Never praise, grade or say whether they're on the right track.
- You do not know the answer to this case, and you never reveal or guess one. If they ask for the answer or the solution, decline lightly and hand it back.
- If they seem confused about the case itself (wrong company or metric), set them straight in one sentence.
{design_rules}{TYPE_RULES.get(c.type_key, "")}
Probing: half the job
- "Okay" or "Go on" is for when they're mid-thought or laying out their plan.
- When they commit to something (a cause, a cut, a segment, a pain point, a solution, a metric, an option, an assumption, a decision or a number), ask one short follow-up in their own words that makes them defend or test it: why that one, how they'd check it, what it costs them, what would change their mind. ("You said it's the payment step. How would you confirm that?")
- About one probe per step of the case. Never two questions in one turn, and don't probe the same claim twice.
- A probe is about something they already said, and asks about their reasoning only: no new facts, no hint at the answer, no judgement.
- Never use a question to steer: don't point at the next step or where to look ("what about the conversion from product page to cart?", "what would you split it by to find where it's concentrated?"), don't list options for them ("geography, category, or something else?"), and don't keep asking "what next?". If they're working through it, let them.
- If they hedge ("it depends", or two options and no pick), ask them to choose: "If you had to pick today, which one?"
- Before the end, get a committed call: their root cause, design, recommendation, decision, metric or final number. Only then call present_twist.
Helping when they're stuck
- Help only when they ask for it, or are clearly stuck: going in circles, "I don't know where to go", maths that's lost its way, or long confused turns.
- Each turn's note tells you how much help you've already given and what the next level is. Use exactly that level, and call record_help with it:
  - nudge: one short guiding question that makes them think, with no facts and no answer. It never names the specific segment, place, group or change where you suspect the problem is (not "is it the same in metros?" but "which split haven't you tried?") ("What share of the drop would Diwali alone explain?").
  - redirect: name the step they should do now, as a suggestion ("It might help to separate the seasonal drop from everything else first.").
  - rescue: explain what that step involves in a sentence or two, still without giving the result.
- Help with how to think, never with the result. Don't refuse bluntly ("I'm not going to do that for you"); give the right level of help instead.
- Steps of this case type: {steps}.

The ending
- When they give their final recommendation for the whole case, call present_twist and put the twist to them in your own words.
- After the twist, add no new facts. If their answer is thin, ask one short follow-up question. Then close: thank them, say that's time, and call end_interview.
- If a clock note says time is nearly up, ask them to move towards a recommendation.

Examples
Candidate: "Has the definition or the tracking tool changed?"
  Bad: "Let me give you the data integrity check. There you go. What does it tell you?"
  Good: "No. Same definition, and the order count matches the payments ledger and restaurant confirmations within 0.3%."
Candidate: "Have there been any updates to the Swiggy UPI plugin?"
  Bad: "Version 4.98 made Swiggy UPI the default and moved other UPI apps below the fold."
  Good: "Yes, Android version 4.98 went out to all users last Friday."
Candidate: "Have we seen this drop before, around this time last year?"
  Bad: "Last Diwali orders were 8% down. This time it's 20%."
  Good: "Diwali week last year, orders were about 8% below the prior four weeks, and recovered the week after."
Candidate (stuck on arithmetic): "So it's 92% times 20%... that's 38%? That can't be right."
  Bad: "What's left over is about 12 points."  Bad: "I'm not going to do the arithmetic for you."
  Good (nudge, with record_help): "Take it one step at a time. If Diwali alone explains about 8 points, how much of the 20 is still unexplained?"
Candidate: "So I think it's the payment step, because that conversion dropped the most."
  Bad: "Okay, go on."  Bad: "Right, payments is where the problem is."
  Good: "How would you confirm it's payments and not something earlier in the funnel?"
Candidate: "Okay, so the product-page views are flat but add-to-cart fell."
  Bad: "That rate drop is your signal. Which segment would you split it by — geography, category, or something else?"
  Good: "Okay." (they're mid-thought; let them pick the next cut)
Candidate: "Honestly it depends. Both options have merits."
  Good: "If you had to pick one today, which would it be?"
Candidate: "Has there been a policy change, or did competitors do something, or weather?"
  Good: "That's a few things. Which would you check first, and why?"

The case (you already read the prompt to the candidate)
{c.prompt}

Prepared answers to clarifying questions (say these facts in your own words, keeping the numbers exact)
{qa}

Your data tables (speak a fact from them when asked; share_exhibit puts the whole table on their screen)
{ex}

Public background you may share if asked
{bg}

Twist: {"this case has a follow-up twist. You'll get its text when you call present_twist, after their final recommendation" if n_tw else "this case has none, so after their final recommendation, call end_interview"}.

The candidate is interviewing at {LEVEL_NAMES.get(self.level, self.level)} level. Time box: {c.time_box_minutes} minutes."""

    # ---------- public API ----------

    def respond(self, candidate_text: str) -> Iterator[Event]:
        t0 = time.time()
        self._add("Candidate", candidate_text)
        n_cand = sum(t.speaker == "Candidate" for t in self.turns)
        user = f"{self._turn_note(candidate_text)}\nCandidate: {candidate_text}"
        events: list[Event] = []
        ending = False
        try:
            text, calls = self._run(user, events)
        except models.BudgetExceeded as e:
            self.budget_stopped = True
            text, calls = ("We'll have to stop here: this month's API budget for practice is used up. "
                           "I'll put your feedback together with what we have."), [{"name": "end_interview"}]
        except Exception as e:  # network or provider error: keep the session alive
            err = str(e)
            self.turns[-1].meta = {"error": err[:300]}
            label = models.PROVIDER_LABEL.get(self.meta_model["provider"], "The provider")
            if re.search(r"HTTP 40[13]\b|authentication|api key|unauthori", err, re.I):
                text = (f"(Setup problem, not part of the interview: {label} rejected the API key. "
                        "Fix it on the Models screen, then start the case again.)")
            elif re.search(r"HTTP 402|insufficient|credits", err, re.I):
                text = f"(Setup problem: your {label} account is out of credits. Top it up, then try again.)"
            elif re.search(r"HTTP 4\d\d", err):
                text = f"(Setup problem: {label} refused the request: {err[:160]})"
            else:
                text = "Sorry, I lost you for a second there. Could you say that again?"
            calls = []
        for c in calls:
            if c["name"] == "end_interview":
                ending = True
        text = _drop_banned(self._review(candidate_text, text, calls))
        if VAGUE_DATA_RE.search(candidate_text):
            kw = lambda t: {w.rstrip("s") for w in re.findall(r"[a-z][a-z]{3,}", t.lower())} - VAGUE_WORDS - STOP
            if not any(kw(candidate_text) & kw(f"{e.title} {e.body}") for e in self.case.exhibits):
                self._vague_ask = True
        if getattr(self, "_vague_ask", False):
            # They asked for "that data" without naming a cut: the reply may not list the cuts we have.
            self._vague_ask = False
            said = " ".join(t.text for t in self.turns if t.speaker == "Candidate").lower()
            table_words = {w for e in self.case.exhibits for w in re.findall(r"[a-z]{4,}", e.title.lower())}
            leaked = {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w in table_words and w not in said} \
                - VAGUE_WORDS - STOP - {"rate", "daily", "events"}
            if (leaked or "?" not in text) and not any(e.kind == "exhibit" for e in events):
                text = self.rng.choice(["Which cut would you like to see?", "Which data do you want? Tell me the cut.",
                                        "Sure, which breakdown?"])
        if any(e.kind == "exhibit" for e in events) and NUM_WORD_RE.search(text):
            # The table is on screen; reading its numbers out does the candidate's reading for them.
            text = self.rng.choice(["Sure, that's up on your screen.", "Okay, I've put that up.", "That's on your screen now."])
        helping = bool(STUCK_RE.search(candidate_text) or HINT_RE.search(candidate_text))
        if helping and not names_called(calls, {"present_twist", "end_interview"}):
            new_nums = {n.lower() for n in NUM_WORD_RE.findall(text)} - {n.lower() for n in NUM_WORD_RE.findall(candidate_text)}
            if new_nums:   # numbers in a hint mean it handed over data; swap in the fixed line for that level
                level = next((c["args"].get("kind") for c in calls if c.get("name") == "record_help"), None) \
                    or self._next_help()
                lines = HELP_FALLBACK.get(level, HELP_FALLBACK["nudge"])
                self.turns[-1].meta = {**(self.turns[-1].meta or {}), "help_draft": text[:600]}
                text = lines.get(self.case.type_key, lines["_"])
        if self.twist_id and not any(c["name"] == "present_twist" for c in calls):
            self.twist_turns += 1
            asked_back = bool(re.search(r"\?|\b(explain|what is|what does|what do you mean)\b", candidate_text, re.I))
            if (self.twist_turns >= 3 or (self.twist_turns >= 2 and not asked_back)) and not ending:
                if not re.search(r"\b(that's time|thank)", text, re.I):
                    text = (text + " Okay, thanks. That's time.").strip()
                ending = True
        if HINT_RE.search(candidate_text) and not any(c["name"] == "record_help" for c in calls) \
                and not self._helped_this_turn:
            self._log_help(self._next_help(), "")
        for e in events:
            yield e
        text = text.strip() or "Okay, go on."
        yield Event("say", text)
        self._add("Interviewer", text, {"tools": calls, "latency": round(time.time() - t0, 2),
                                        "cost": round(self.cost, 5), "model": self.model_id})
        if ending:
            self.done = True
            yield Event("end")

    # ---------- per-turn guidance ----------

    def _circling(self) -> bool:
        """Going in circles: the last three candidate turns are long and mostly the same words."""
        cand = [t.text.lower() for t in self.turns if t.speaker == "Candidate"][-3:]
        if len(cand) < 3:
            return False
        sets = [set(re.findall(r"[a-z]{4,}", c)) for c in cand]
        overlap = len(sets[0] & sets[1] & sets[2]) / max(1, min(len(x) for x in sets))
        return overlap > 0.5

    def _replies_since_probe(self) -> int:
        n = 0
        for t in reversed(self.turns[1:]):
            if t.speaker != "Interviewer":
                continue
            if "?" in t.text and PROBE_RE.search(t.text):
                break
            n += 1
        return n

    def _committed(self) -> bool:
        return any(t.speaker == "Candidate" and COMMIT_RE.search(t.text) for t in self.turns)

    def _next_help(self) -> str:
        return ["nudge", "redirect", "rescue"][min(self.hints, 2)]

    def _turn_note(self, candidate_text: str) -> str:
        """What the model needs to know this turn but can't work out reliably itself: the clock, how much
        help it has given (so help escalates nudge -> redirect -> rescue), where the twist stands, and the
        edited wording of its last reply if the review pass changed it."""
        self._helped_this_turn = False
        parts = [f"Clock: {self.elapsed_min:.0f} of {self.case.time_box_minutes} minutes used"
                 + ("; time is up" if self.progress >= 1.0 else "; nearly out of time" if self.progress >= 0.8 else "")]
        kinds = [i["kind"] for i in self.interventions if i["kind"] in ("nudge", "redirect", "rescue")]
        parts.append(f"Help given so far: {', '.join(kinds) if kinds else 'none'}. If they need help this turn, "
                     f"give a {self._next_help()} and call record_help")
        if STUCK_RE.search(candidate_text) or HINT_RE.search(candidate_text):
            parts.append("They may be stuck or asking for help")
        if HEDGE_RE.search(candidate_text):
            parts.append("They hedged without picking: ask them to choose one")
        elif not self.twist_id and self._replies_since_probe() >= 3 and "?" not in candidate_text:
            parts.append("You haven't probed in a while. If they've just committed to something, ask one short "
                         "follow-up on it in their words; if they're mid-thought, let them go on")
        if self.case.type_key == "product_design" and USERS_ASK_RE.search(candidate_text):
            parts.append("They're asking about users, segments or pain points. If they haven't named their own "
                         "guess, ask who they think it is instead of answering. If they have, confirm or correct "
                         "only what they named, and add no other segments or causes")
        if self.twist_id:
            asked_back = bool(re.search(r"\?|\b(explain|what is|what does|what do you mean)\b", candidate_text, re.I))
            parts.append("They are answering the twist. Add no new facts; ask at most one short follow-up"
                         if self.twist_turns == 0 else
                         "They asked you something about the twist: answer it in a sentence or two from your notes, "
                         "then let them respond. Add nothing beyond what they asked"
                         if self.twist_turns == 1 and asked_back else
                         "Close the interview now: if they asked you something, answer it in one sentence first; then "
                         "thank them, say that's time, and call end_interview. No new facts or questions")
        if getattr(self, "_edited_last", None):
            parts.append(f'What you actually said last turn (edited for pacing): "{self._edited_last}"')
            self._edited_last = None
        return "[" + ". ".join(parts) + ".]"

    # ---------- review pass ----------

    REVIEW_RULES = """You check one reply from a PM case interviewer before it is spoken, and trim it if needed.
Rules the reply must follow:
1. It answers only the question the candidate just asked, with the smallest true answer. Facts they did not ask for are removed (side details, causes, mechanisms, error logs, other rows or periods). Check each fact separately: a fact that answers a different question than the one asked is removed, even if it sits in the same note (asked about platforms -> "no app release" is removed; asked about competitors -> "no UPI outage" is removed). A closing remark on what the facts add up to is removed.
2. It does no thinking for the candidate: no comparisons, sums or differences of numbers, no "so this means...", no saying what a fact implies, no pointing at where the answer is.
3. It never states the answer or the cause of the case problem.
4. It sounds like a person: one or two short sentences, no lists.
5. It never offers or lists other data the interviewer has ("I've got X if that's useful"); such offers are removed.
6. In a product design case, segments, user groups, categories and pain points the candidate did not name themselves are removed (keep only what confirms or corrects what they said). If nothing is left, the reply hands it back: "Who do you think it is?"
7. Questions don't steer: a follow-up is about something the candidate already said. Remove questions that point at the next step or where to look, or list options for them, and lines saying what a fact means ("that's your signal", "so it's X, not Y"). If nothing is left, the reply is "Okay."
8. A nudge or redirect never names the specific segment, place, group or change where the problem might be ("is it the same in metros?" becomes "Which way of splitting it haven't you tried yet?").
9. Help is allowed only as the level named in the note (a guiding question for a nudge; naming a step for a redirect; explaining a step for a rescue), and never gives the result.
You may only remove or reword. Never add a fact or number that isn't in the reply. Never replace a factual answer to their question with a question of your own: if they asked something the draft answers, keep the (trimmed) answer. Keep a guiding question if it is good help.
Answer with JSON: {"reply": "<the reply, trimmed if needed>", "changed": true|false, "help": "none|nudge|redirect|rescue", "step": "<step key if help, else empty>"}
"help" says whether the final reply helps the candidate think (a guiding question, naming a step, explaining a step) beyond answering a factual question. A follow-up about the candidate's own claim ("Why that one?", "How would you confirm it?", "What would change your mind?", "Which would you pick?") is ordinary interviewing: help "none"."""

    def _review(self, candidate_text: str, text: str, calls: list[dict]) -> str:
        names = {c.get("name") for c in calls}
        if not text or text.startswith("(Setup problem") or names & {"present_twist", "end_interview"}:
            return text
        stuck = bool(STUCK_RE.search(candidate_text) or HINT_RE.search(candidate_text))
        users_ask = self.case.type_key == "product_design" and bool(USERS_ASK_RE.search(candidate_text))
        multi = len(re.findall(r"[.!?;—](?=\s|$)", text.strip())) >= 2 or bool(re.search(r",\s*(and|so|but)\b", text))
        if (not re.search(r"\d", text) and len(text.split()) < 25 and not stuck and not users_ask
                and not (multi and len(text.split()) >= 8) and "?" not in text):
            return text   # acks and single plain facts without numbers need no check; questions can steer
        prev = next((t.text for t in reversed(self.turns[:-1]) if t.speaker == "Interviewer"), "")
        shown = [c["args"].get("exhibit_id") for c in calls if c.get("name") == "share_exhibit"]
        prompt = (f"Case: {self.case.prompt}\nSteps of this case type: {', '.join(k for k, _ in self.phases)}\n"
                  f"Help given before this turn: {self.hints}. Next allowed help level: {self._next_help()}.\n"
                  + ("The candidate asked for help or is stuck, so the reply is meant to help: rule 1 does not apply "
                     "to the guiding question or the step it names. Keep the help at the allowed level, and remove "
                     "only results, numbers they didn't ask for, and anything stronger than that level.\n"
                     if stuck else "")
                  + (f"Tables put on screen this turn: {', '.join(map(str, shown))}\n" if shown else "")
                  + f"\nInterviewer's previous line: {prev[-600:]}\nCandidate just said: {candidate_text[-1500:]}\n"
                  f"Interviewer's draft reply: {text}")
        try:
            rules = self.REVIEW_RULES if self.case.type_key == "product_design" else \
                re.sub(r"\n6\. In a product design case[^\n]*", "", self.REVIEW_RULES)
            out = self._complete(rules, prompt)
        except models.BudgetExceeded:
            return text
        except Exception:
            return text
        reply = (out.get("reply") or "").strip()
        help_kind = out.get("help") if out.get("help") in ("nudge", "redirect", "rescue") else None
        # Count help when the candidate was stuck or asked, or when the hint points at specifics (it carries
        # numbers or is long); generic "what next?" questions are ordinary interviewing, not help.
        specific = bool(re.search(r"\d", reply or text)) or len((reply or text).split()) >= 15
        if help_kind and (stuck or specific) and "record_help" not in names and not self._helped_this_turn:
            self._log_help(help_kind, str(out.get("step") or ""))
            self._helped_this_turn = True
        nums = lambda t: set(re.findall(r"\d+(?:\.\d+)?", t))
        answered_to_question = (not users_ask and "?" in candidate_text and reply.rstrip().endswith("?")
                                and not text.rstrip().endswith("?"))
        if (not reply or reply == text or not nums(reply) <= nums(text) or answered_to_question
                or len(reply.split()) > len(text.split()) + 8):
            return text   # the review may only trim or reword; anything else keeps the draft
        self._edited_last = reply
        self.turns[-1].meta = {**(self.turns[-1].meta or {}), "draft": text[:600]}
        return reply

    def _complete(self, system: str, user: str) -> dict:
        """One small JSON completion on the interview model, without tools or history. Budget-checked."""
        models.charge_guard(self.model_id, len(system) + len(user) + 500, 400)
        prov, m = self.meta_model["provider"], self.meta_model
        if prov == "anthropic":
            import anthropic
            client = self._client if isinstance(self._client, anthropic.Anthropic) else anthropic.Anthropic(
                api_key=models.api_key("anthropic"))
            resp = client.messages.create(model=m["model"], max_tokens=1200, system=system,
                                          messages=[{"role": "user", "content": user}],
                                          output_config={"effort": "low"})
            u = resp.usage
            self.cost += models.record(self.model_id, inp=u.input_tokens or 0, out=u.output_tokens or 0,
                                       session=self.case.id)
            out = "".join(b.text for b in resp.content if b.type == "text")
        elif prov == "gemini":
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=models.api_key("gemini"))
            cfg = dict(system_instruction=system, max_output_tokens=600, temperature=0,
                       response_mime_type="application/json")
            if m["model"].startswith("gemini-2.5"):
                cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
            resp = client.models.generate_content(model=m["model"], contents=user,
                                                  config=types.GenerateContentConfig(**cfg))
            um = resp.usage_metadata
            self.cost += models.record(self.model_id, inp=(um.prompt_token_count or 0) if um else 0,
                                       out=((um.candidates_token_count or 0) + (um.thoughts_token_count or 0)) if um else 0,
                                       session=self.case.id)
            out = resp.text or ""
        else:
            client = self._client if isinstance(self._client, _OpenAICompat) else _OpenAICompat()
            resp = client.post(models.base_url(self.model_id) + "/chat/completions", models.api_key(prov),
                               {"model": m["model"], "max_tokens": 600, **m.get("extra", {}),
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            u = resp.get("usage") or {}
            cached = u.get("prompt_cache_hit_tokens") or (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
            self.cost += models.record(self.model_id, inp=max(0, (u.get("prompt_tokens") or 0) - cached),
                                       out=u.get("completion_tokens") or 0, cache_read=cached, session=self.case.id,
                                       actual_cost=u.get("cost") if m.get("reported_cost") else None)
            out = _strip_think(((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        a, b = out.find("{"), out.rfind("}")
        try:
            return json.loads(out[a:b + 1]) if a >= 0 else {}
        except json.JSONDecodeError:
            return {}

    def hint(self) -> Iterator[Event]:
        yield from self.respond("I'm a bit stuck. Could you give me a nudge?")

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({"interviewer_model": self.model_id, "api_cost": round(self.cost, 5),
                  "budget_stopped": self.budget_stopped})
        return d

    # ---------- tools ----------

    def _log_help(self, kind: str, step: str) -> None:
        keys = [k for k, _ in self.phases]
        step = step if step in keys else next((k for k in keys if k in step), keys[0])
        kind = kind if kind in ("nudge", "redirect", "rescue") else "nudge"
        self.hints += 1
        self._intervene(kind, step)

    def _execute(self, name: str, args: dict, events: list[Event]) -> str:
        if name == "share_exhibit":
            eid = str(args.get("exhibit_id", "")).strip().upper()
            asked = self.turns[-1].text if self.turns and self.turns[-1].speaker == "Candidate" else ""
            if not SHOW_RE.search(asked):
                # Real interviewers answer "has X changed?" out loud; a table on screen is for "can I see...".
                return ("Not shared: they didn't ask to see data. Answer their question in words, in a sentence, "
                        "using the facts from that table.")
            if eid not in self._emap:
                return f"No exhibit {eid}. Valid ids: {', '.join(self._emap)}."
            if eid in self.revealed_e:
                return f"{eid} is already on the candidate's screen."
            # "Can you give me that data?" names no cut, so the model would be choosing the table for them.
            # The request has to share a word with the table's title or body (tier, pincode, category...).
            ex = self._emap[eid]
            kw = lambda t: {w.rstrip("s") for w in re.findall(r"[a-z][a-z]{3,}", t.lower())} - VAGUE_WORDS - STOP
            if not kw(asked) & kw(f"{ex.title} {ex.body}"):
                self._vague_ask = True
                return ("Not shared: they didn't say which data or breakdown they want. Ask them which cut "
                        "they'd like, in one short question, without suggesting one.")
            self.revealed_e.append(eid)
            self._intervene("clarification", eid)
            events.append(Event("exhibit", self._emap[eid].body, eid))
            return f"{eid} is now on the candidate's screen."
        if name == "record_help":
            asked = self.turns[-1].text if self.turns and self.turns[-1].speaker == "Candidate" else ""
            if self._helped_this_turn:
                return "Already logged this turn."
            # Ordinary interviewer questions ("Where would you go next?") are not help; only log help given to
            # someone who is stuck or asked for it, so grading isn't capped by false hints.
            if not (STUCK_RE.search(asked) or HINT_RE.search(asked) or self._circling()):
                return "Not logged: they aren't stuck. Don't give help; just respond normally."
            self._log_help(str(args.get("kind", "")) or self._next_help(), str(args.get("step", "")))
            self._helped_this_turn = True
            return "Logged."
        if name == "present_twist":
            # The twist text is handed over only now: several twists assume the case's answer.
            self.landed = True
            if self.twist_id:
                return "You have already presented the twist. Don't present another."
            if not self._committed() and self.progress < 0.85:
                return ("Not yet: they haven't committed to a call. Ask for it in one short question (\"If you had "
                        "to decide today, what would you do?\"), then call present_twist once they answer.")
            if not self.case.twists:
                return "This case has no twist. Call end_interview instead."
            tw = self.rng.choice(self.case.twists)
            self.twist_id = tw.id
            return f"The twist to put to them now, in your own words: {tw.twist}"
        if name == "end_interview":
            return "The interview is closing. Say a short closing line."
        return "Unknown tool."

    def _note_clarifications(self, text: str) -> None:
        """Log which prepared answers were used, for the report (numbers or distinctive words reused)."""
        low = text.lower()
        for q in self.case.clarifications:
            if q.id in self.revealed_q:
                continue
            nums = set(re.findall(r"\d+(?:\.\d+)?", q.a))
            words = _words(q.a)
            if (nums and nums & set(re.findall(r"\d+(?:\.\d+)?", text))) or len(words & _words(low)) >= 3:
                self.revealed_q.append(q.id)
                self._intervene("clarification", q.id)

    # ---------- providers ----------

    def _run(self, user: str, events: list[Event]) -> tuple[str, list[dict]]:
        prov = self.meta_model["provider"]
        run = {"anthropic": self._run_anthropic, "gemini": self._run_gemini}.get(prov, self._run_openai)
        text, calls = run(user, events)
        self._note_clarifications(text)
        return text, calls

    def _guard(self) -> None:
        chars = len(self.system) + sum(len(t.text) for t in self.turns) + 2000
        models.charge_guard(self.model_id, chars, self.MAX_OUT)

    def _run_anthropic(self, user: str, events: list[Event]) -> tuple[str, list[dict]]:
        import anthropic
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=models.api_key("anthropic"))
        tools = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]} for t in TOOLS]
        self._history.append({"role": "user", "content": user})
        texts, calls = [], []
        model = self.meta_model["model"]
        for _ in range(4):
            self._guard()
            kw = dict(model=model, max_tokens=self.MAX_OUT, system=self.system, tools=tools,
                      messages=self._history, cache_control={"type": "ephemeral"},
                      output_config={"effort": "low"})
            if model in ("claude-opus-5-5", "claude-sonnet-5-5"):
                # Server-side fallback on a safety decline (none expected in a case interview).
                kw.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            resp = self._client.beta.messages.create(**kw)
            u = resp.usage
            self.cost += models.record(self.model_id, inp=u.input_tokens or 0, out=u.output_tokens or 0,
                                       cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                                       cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
                                       billed_model=getattr(resp, "model", None), session=self.case.id)
            if resp.stop_reason == "refusal":
                self._history.pop()   # drop the declined turn so the conversation stays valid
                return "Let's keep going with the case. Where would you like to take it?", calls
            self._history.append({"role": "assistant", "content": resp.content})
            texts += [b.text for b in resp.content if b.type == "text" and b.text.strip()]
            uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not uses:
                break
            results = []
            for b in uses:
                calls.append({"name": b.name, "args": dict(b.input or {})})
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": self._execute(b.name, dict(b.input or {}), events)})
            self._history.append({"role": "user", "content": results})
        return (texts[-1] if texts else ""), calls

    def _run_gemini(self, user: str, events: list[Event]) -> tuple[str, list[dict]]:
        from google import genai
        from google.genai import types
        if self._client is None:
            self._client = genai.Client(api_key=models.api_key("gemini"))
        model = self.meta_model["model"]
        cfg = dict(system_instruction=self.system, max_output_tokens=self.MAX_OUT, temperature=0.5,
                   tools=[types.Tool(function_declarations=[
                       {"name": t["name"], "description": t["description"],
                        **({"parameters": t["schema"]} if t["schema"]["properties"] else {})} for t in TOOLS])],
                   automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        if model.startswith("gemini-2.5"):
            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        config = types.GenerateContentConfig(**cfg)
        self._history.append(types.Content(role="user", parts=[types.Part(text=user)]))
        texts, calls = [], []
        for _ in range(4):
            self._guard()
            resp = self._client.models.generate_content(model=model, contents=self._history, config=config)
            um = resp.usage_metadata
            prompt = (um.prompt_token_count or 0) if um else 0
            cached = (um.cached_content_token_count or 0) if um else 0
            out = ((um.candidates_token_count or 0) + (um.thoughts_token_count or 0)) if um else 0
            self.cost += models.record(self.model_id, inp=prompt - cached, out=out, cache_read=cached,
                                       session=self.case.id)
            cand = resp.candidates[0].content if resp.candidates and resp.candidates[0].content else None
            if cand is None:
                break
            self._history.append(cand)   # keep the whole content (thought signatures included)
            texts += [p.text for p in (cand.parts or []) if getattr(p, "text", None) and not getattr(p, "thought", False)]
            fcs = [p.function_call for p in (cand.parts or []) if getattr(p, "function_call", None)]
            if not fcs:
                break
            parts = []
            for fc in fcs:
                args = dict(fc.args or {})
                calls.append({"name": fc.name, "args": args})
                parts.append(types.Part.from_function_response(
                    name=fc.name, response={"result": self._execute(fc.name, args, events)}))
            self._history.append(types.Content(role="user", parts=parts))
        return (texts[-1].strip() if texts else ""), calls


def _post_json(url: str, key: str, body: dict, timeout: float = 60) -> dict:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code} from {url.split('/')[2]}: {detail}") from None


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


class _OpenAICompat:
    """Chat Completions with tools for DeepSeek, Qwen (Model Studio) and MiniMax. Assistant messages go back
    into the history exactly as returned, reasoning_content included: DeepSeek (with tools) and MiniMax
    require it. Transport is stdlib urllib so no extra SDK is needed; tests swap `post`."""
    post = staticmethod(_post_json)


def _run_openai(self, user: str, events: list[Event]) -> tuple[str, list[dict]]:
    m = self.meta_model
    if self._client is None:
        self._client = _OpenAICompat()
        self._history = [{"role": "system", "content": self.system}]
    key = models.api_key(m["provider"])
    url = models.base_url(self.model_id) + "/chat/completions"
    tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                               "parameters": t["schema"]}} for t in TOOLS]
    self._history.append({"role": "user", "content": user})
    texts, calls = [], []
    for _ in range(4):
        self._guard()
        body = {"model": m["model"], "messages": self._history, "tools": tools, "max_tokens": self.MAX_OUT,
                **m.get("extra", {})}
        resp = self._client.post(url, key, body)
        u = resp.get("usage") or {}
        cached = (u.get("prompt_cache_hit_tokens")
                  or (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        prompt = u.get("prompt_tokens") or 0
        self.cost += models.record(self.model_id, inp=max(0, prompt - cached), out=u.get("completion_tokens") or 0,
                                   cache_read=cached, session=self.case.id,
                                   cache_write=(u.get("prompt_tokens_details") or {}).get("cache_write_tokens") or 0,
                                   actual_cost=u.get("cost") if m.get("reported_cost") else None)
        msg = ((resp.get("choices") or [{}])[0]).get("message") or {}
        if not msg:
            break
        msg = {k: v for k, v in msg.items() if v is not None}
        msg["role"] = "assistant"
        self._history.append(msg)
        if (msg.get("content") or "").strip():
            texts.append(_strip_think(msg["content"]))
        tcs = msg.get("tool_calls") or []
        if not tcs:
            break
        for tc in tcs:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"name": fn.get("name"), "args": args})
            self._history.append({"role": "tool", "tool_call_id": tc.get("id"),
                                  "content": self._execute(fn.get("name", ""), args, events)})
    texts = [t for t in texts if t]
    return (texts[-1] if texts else ""), calls


AgentInterview._run_openai = _run_openai


def make_interview(case: Case, level: str, model_id: str | None = None):
    """The interview object for the chosen interviewer model: the local pipeline or an API agent."""
    model_id = model_id or models.settings()["interviewer_model"]
    m = models.MODELS.get(model_id, models.MODELS[models.DEFAULT_MODEL])
    if m["provider"] == "ollama":
        from . import llm
        llm.INTERVIEW_MODEL = m["model"]   # the pipeline's small calls use this default
        return Interview(case, level)
    return AgentInterview(case, level, model_id)
