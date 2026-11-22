"""Interview engine: a per-case state machine that keeps the 7B model on a short leash.

Per candidate turn:
  1. matcher (embeddings, ~0.1 s): which clarifying questions / exhibits the candidate's asks point at.
  2. confirm (7B, tiny JSON, only when the candidate asked something): which of those shortlisted
     items really answer the ask, plus the candidate's intent.
  3. reply: mostly deterministic. Casebook clarification answers and twists are already written in an
     interviewer's voice, so they are spoken verbatim; exhibits, time nudges and acknowledgements are
     templates. The 7B only writes lines for hints and for questions the case has no data for.

So the interviewer can't leak the answer, can't praise, and is fast even on a busy 16 GB machine.
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator

from . import llm
from .casepack import Case
from .matcher import Matcher, extract_asks
from .rubrics import HINT_RE, LEVELS, phase_list, phase_texts, strip_hint_requests

SESSIONS = Path(__file__).resolve().parents[1] / "sessions"

INTENTS = ["analysing", "clarifying", "requesting_data", "recommending", "answering_twist",
           "asking_for_hint", "small_talk"]

ACKS = ["Okay, go on.", "Mm-hm.", "Okay.", "Go ahead.", "Sure, keep going.", "Right. Go on."]
NO_DATA = ["Nothing unusual there, as far as I know.",
           "I don't have a split on that. You can assume it looks normal.",
           "Nothing stands out there. Assume it's in line with usual.",
           "Assume that's normal for now."]
CONFIRMS = ["Yes, that's right. Go ahead.", "Yep, that's the problem. Over to you.",
            "That's right. How would you start?"]
CONFIRM_RE = re.compile(
    r"\b(am i right|is that (right|correct)|did i get (that|it|this) right|have i got (that|it) right|"
    r"does that sound right|is my understanding (right|correct)|correct\?|right\?|fair\?|"
    r"is that a fair (summary|understanding)|are we aligned|sound good\?)", re.I)
FILLER_RE = re.compile(r"(you tell me|let me know|feel free|i hope|does that (help|clarify|answer)|"
                       r"if you (need|have|want)|happy to)\b", re.I)
UNCHANGED_RE = re.compile(r"\b(changed|remained|stayed|still the same|same as (before|usual)|"
                          r"any (changes|updates)|been (updated|modified))\b", re.I)
COMMENTARY_RE = re.compile(r"\b(good|great|nice|interesting) (question|start|point|thinking)|\blet'?s (focus|clarify|"
                           r"look|start|dig|move|confirm|proceed)|\bthat'?s not (directly )?related", re.I)
# Product design: users, segments and pain points are the candidate's to come up with.
USERS_ASK_RE = re.compile(
    r"\b(who (are|is|would be) (the|these|our|those) (users?|customers?|people)|who (is )?complain|"
    r"(which|what) (users?|customers?|segments?|people|groups?|kinds? of users?)|segments?|demographics?|personas?|"
    r"user (base|profile|types?)|pain ?points?|(main|biggest) (pain|problem|complaint)s?|complain (the )?most|"
    r"concentrated|why (are|do) (they|users|people))\b", re.I)
HELD_Q_RE = re.compile(
    r"\b(segments?|which (users|customers|merchants|riders)|who (complains?|uses?)|complain most|concentrated|"
    r"uniform|main pain|pain points?|where in the flow)\b", re.I)
HAND_BACK = ["What's your guess? Who do you think it is?", "Tell me your hypothesis first. Who would you expect?",
             "You tell me first. Who do you think is most affected?"]
# Casebook answers that already hand the question back ("Tell me what you'd hypothesise", "Your call").
SELF_HANDBACK_RE = re.compile(r"\b(tell me what you|i'?ll (show|share)|your call|find out|you tell me)\b", re.I)
GENERIC_WORDS = {"orders", "order", "items", "users", "people", "customers", "complain", "complaints", "most",
                 "illustrative", "think", "would", "guess", "maybe", "probably", "these", "those"}


def _stems(t: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z][a-z0-9-]{3,}", t.lower()) if w not in GENERIC_WORDS}


NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")

# Closing detection. STRONG phrases mean "this is my final answer" on their own. BROAD ones also show
# up mid-case ("so about 6.7M adults", "my pick is X" before the guardrails), so they only trigger a
# tiny 7B yes/no check (_is_closing). Measured on the 66 casebook walkthroughs: the old single regex
# never fired on 51 of them, so most interviews never reached the twist.
RECOMMEND_RE = re.compile(
    r"\b(my recommendation|i('d| would) recommend|i recommend|to summari[sz]e|in summary|to conclude|"
    r"my final (answer|estimate|number|call|recommendation)|here'?s my (landing|recommendation|final)|"
    r"to land (it|this)|bringing it together|to wrap up|to close|bottom line)\b", re.I)
BROAD_RE = re.compile(
    r"\b(so the root cause|the root cause (is|was)|root cause( #?1)?\s*[:,]|final (estimate|answer|number)|"
    r"so my (estimate|answer|number)|my north star (would be|is)|the north star (should|would|is)|"
    r"my (decision|call|pick|choice|landing|plan) (is|would be)|here'?s (what i'?d do|my (call|decision|answer|plan))|"
    r"landing it|in short|my answer is|so the answer is|i('d| would) (go with|ship|delay|launch|hold|pause|roll ?back|cut)|"
    r"so i('d| would)|that gives (us |me )?(about|roughly|around|~)|so (about|roughly|around) [\d₹$]|"
    r"(three|two|four) things|fix(es)?\s*:|short term\s*:)\b"
    r"|(^|[.!?]\s+)(ship|delay|launch|hold|pause|roll back|move|cut|go with|keep)\b", re.I | re.M)


@dataclass
class Turn:
    speaker: str   # "Interviewer" | "Candidate"
    text: str
    t: float
    meta: dict = field(default_factory=dict)


@dataclass
class Event:
    """What the UI receives for each interviewer turn."""
    kind: str                     # "say" | "exhibit" | "end"
    text: str = ""
    exhibit_id: str = ""


class Interview:
    def __init__(self, case: Case, level: str = "pm", sim_words_per_min: float | None = None):
        if level not in LEVELS:
            raise ValueError(f"level must be one of {list(LEVELS)}")
        self.case = case
        self.level = level
        self.sim_words_per_min = sim_words_per_min
        self.phases = phase_list(case.type_key)
        self.turns: list[Turn] = []
        self.revealed_q: list[str] = []
        self.revealed_e: list[str] = []
        self.phases_touched: list[str] = []
        self.hints = 0
        self.interventions: list[dict] = []   # {kind, turn, target}: graded by the hint taxonomy
        self.landed = False                   # candidate reached a recommendation / final answer
        self.twist_id: str | None = None
        self.twist_turns = 0
        self.time_nudged = False
        self.started = time.time()
        self.done = False
        self.rng = random.Random(int(self.started))
        self.matcher = Matcher(case.clarifications, case.exhibits)
        self._step_vecs = llm.embed([f"search_document: {t}" for t in phase_texts(case.type_key)])
        self._prompt_vec = llm.embed([f"search_query: {case.prompt}"])[0]

    # ---------- public API ----------

    def open(self) -> Event:
        text = (f"Hi, thanks for joining. Here's your case. {self.case.prompt} "
                f"Take a moment, and walk me through how you'd approach it.")
        self._add("Interviewer", text)
        return Event("say", text)

    def respond(self, candidate_text: str) -> Iterator[Event]:
        """Process one candidate turn. Yields exhibit events, then 'say' text (possibly streamed)."""
        t0 = time.time()
        self._add("Candidate", candidate_text)
        d = self._understand(candidate_text)
        self.turns[-1].meta = d
        intent = d["intent"]

        # Clarifications may be asked again (a casebook answer bundles several facts and only the part
        # that was asked gets said), so only exhibits are one-time.
        asked_q = list(dict.fromkeys(d["asked_clarifications"]))
        # Product design: users, segments and pain points are the candidate's to find. A held clarification
        # is answered only as far as it confirms what the candidate guessed; with no guess, hand it back.
        held_reply = ""
        if self.case.type_key == "product_design":
            case_held = [q.id for q in self.case.clarifications if HELD_Q_RE.search(q.q)
                         and not SELF_HANDBACK_RE.search(q.a)]
            held = [q for q in asked_q if q in case_held]
            users_ask = bool(USERS_ASK_RE.search(candidate_text))
            pending = getattr(self, "_pending_held", [])
            free = not (set(asked_q) - set(held)) and not d["requested_exhibits"]
            if not held and free and (users_ask or pending):
                held = pending or case_held   # a guess or "could you tell me?" after a hand-back
            if held or (users_ask and free):
                asked_q = [q for q in asked_q if q not in held]
                held_reply, confirmed = self._held_line(held, candidate_text)
                self._pending_held = [] if confirmed else held
                for q in confirmed:
                    if q not in self.revealed_q:
                        self.revealed_q.append(q)
                        self._intervene("clarification", q)
        new_q = [q for q in asked_q if q not in self.revealed_q]
        new_e = [e for e in d["requested_exhibits"] if e not in self.revealed_e]
        self.revealed_q += new_q
        self.revealed_e += new_e
        for iid in new_q + new_e:
            self._intervene("clarification", iid)
        for p in d["phases_touched"]:
            if p not in self.phases_touched:
                self.phases_touched.append(p)
        for e in new_e:
            yield Event("exhibit", self.matcher_body(e), e)

        # "Could you give me a nudge?" is a hint request, not a question about the case.
        unanswered = [] if intent == "asking_for_hint" else d.get("unanswered_asks", [])
        # Mention a table that's already up only when nothing new answers the turn.
        shown = [] if (asked_q or new_e) else d.get("already_shown", [])
        parts: list[str] = []
        if d.get("confirm") and not held_reply:
            parts.append(self._confirm_restatement(candidate_text))
        if held_reply:
            parts.append(held_reply)
            if not asked_q:
                unanswered = []
        if asked_q:
            parts.append(self._answer_clarifications(candidate_text, asked_q))
        if new_e:
            parts.append(self._share_line(new_e))
        if shown:
            parts.append(self._share_line(shown, already=True))
        if unanswered:
            same = [a for a in unanswered if UNCHANGED_RE.search(a)]
            if same:
                parts.append(self.rng.choice(["Nothing's changed there.", "No, that's stayed the same.",
                                              "No changes there."]))
            elif not parts:
                parts.append(self._no_data_line(candidate_text, unanswered))

        move, ending, llm_instruction = self._plan(intent)
        if move:
            parts.append(move)

        if llm_instruction:
            reply = " ".join(parts + [self._guarded_line(llm_instruction, unanswered, intent)]).strip()
            yield Event("say", reply)
        else:
            reply = " ".join(parts) if parts else self.rng.choice(ACKS)
            yield Event("say", reply)

        self._add("Interviewer", reply, {"revealed": new_q + new_e, "latency": round(time.time() - t0, 2)})
        if ending:
            self.done = True
            yield Event("end")

    def hint(self) -> Iterator[Event]:
        yield from self.respond("I'm a bit stuck. Could you give me a nudge?")

    def end(self) -> None:
        self.done = True

    @property
    def elapsed_min(self) -> float:
        return (time.time() - self.started) / 60

    @property
    def progress(self) -> float:
        """How far through the time box we are. Simulations have no real clock, so they set
        sim_words_per_min and progress is measured in candidate words instead (casebook walkthroughs
        average about 36 candidate words per minute of time box)."""
        box = max(1, self.case.time_box_minutes)
        if self.sim_words_per_min:
            words = sum(len(t.text.split()) for t in self.turns if t.speaker == "Candidate")
            return words / (self.sim_words_per_min * box)
        return self.elapsed_min / box

    def save(self) -> Path:
        SESSIONS.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started))
        path = SESSIONS / f"{stamp}_{self.case.id}.json"
        path.write_text(json.dumps(self.to_dict(), indent=1))
        return path

    def to_dict(self) -> dict:
        return {
            "case_id": self.case.id, "title": self.case.title, "case_type": self.case.case_type,
            "started": self.started, "minutes": round(self.elapsed_min, 1),
            "time_box": self.case.time_box_minutes, "hints": self.hints, "level": self.level,
            "source": "simulated" if self.sim_words_per_min else "practice",
            "interviewer_model": getattr(self, "interviewer_model", None),
            "interventions": self.interventions, "landed": self.landed,
            "revealed_clarifications": self.revealed_q, "revealed_exhibits": self.revealed_e,
            "phases_touched": self.phases_touched, "twist": self.twist_id,
            "turns": [asdict(t) for t in self.turns],
        }

    def matcher_body(self, eid: str) -> str:
        return self._emap[eid].body

    # ---------- internals ----------

    @property
    def _qmap(self):
        return {c.id: c for c in self.case.clarifications}

    @property
    def _emap(self):
        return {e.id: e for e in self.case.exhibits}

    def _intervene(self, kind: str, target: str = "") -> None:
        n = sum(t.speaker == "Candidate" for t in self.turns)
        self.interventions.append({"kind": kind, "turn": n, "target": target})

    def _add(self, speaker: str, text: str, meta: dict | None = None):
        self.turns.append(Turn(speaker, text, round(time.time() - self.started, 1), meta or {}))

    @staticmethod
    def _clean(s: str) -> str:
        s = s.strip().strip('"')
        return re.sub(r"^(interviewer|candidate)\s*:\s*", "", s, flags=re.I)

    @staticmethod
    def _drop_filler(line: str, source: str) -> str:
        """The 7B likes to end with "Let me know if..." / "You tell me if that helps". Keep such a sentence
        only when the casebook line itself has it (e.g. "You tell me whether that matters.")."""
        sents = re.split(r"(?<=[.!?])\s+", line.strip())
        keep = [x for x in sents if not (FILLER_RE.match(x) and x.lower()[:12] not in source.lower())]
        return " ".join(keep).strip()

    def _exhibit_line(self, eid: str) -> str:
        title = self._emap[eid].title.rstrip(":. ")
        return self.rng.choice([f"Sure. I'm sharing {eid} on your screen: {title}.",
                                f"Here you go, {eid}: {title}. Take a look.",
                                f"I have that. Putting up {eid}, {title}."])

    def _plan(self, intent: str) -> tuple[str, bool, str]:
        """The interviewer's move this turn -> (fixed text to say, ends_interview, llm_instruction)."""
        c = self.case
        if self.twist_id:
            self.twist_turns += 1
            if self.twist_turns >= 2 or intent in ("answering_twist", "recommending"):
                return ("Okay, thanks. That's time. I'll put your feedback together now.", True, "")
            return ("", False, "They are answering your follow-up question. If their answer is vague, probe once "
                               "with a short question. Otherwise just say 'Okay.' Do not evaluate it.")

        late = self.progress >= 0.8
        if intent == "recommending":
            self.landed = True
            if c.twists:
                tw = self.rng.choice(c.twists)
                self.twist_id = tw.id
                return (f"Okay. Let me push on that. {tw.twist}", False, "")
            return ("Okay, thanks. That's time. I'll put your feedback together now.", True, "")

        if late and not self.time_nudged:
            self.time_nudged = True
            self._intervene("pacing")
            return ("We have a few minutes left, so start moving towards your recommendation.", False, "")

        if intent == "asking_for_hint":
            self.hints += 1
            key = self._hint_target()
            nxt = dict(self.phases)[key]
            # Help escalates, and so does its cost in grading (see rubrics.HINT_KINDS).
            kind = ["nudge", "redirect", "rescue"][min(self.hints, 3) - 1]
            self._intervene(kind, key)
            how = {
                "nudge": "Give a gentle, Socratic nudge: one guiding question that points them towards it.",
                "redirect": "Name that step plainly and suggest they work through it now.",
                "rescue": "Tell them what that step should cover, in one or two short sentences, so they can carry on.",
            }[kind]
            return ("", False, f"The candidate needs help with this part of the case: '{nxt}'. {how} "
                               "Never name the answer, the cause or the recommendation.")
        return ("", False, "")

    # ---- understanding the candidate's turn ----

    RESTATE_SIM = 0.8   # restatements score 0.92-0.95 against the prompt; clarifying questions 0.5-0.7

    def _is_restatement(self, utterance: str) -> bool:
        """The candidate playing the problem back in their first turns, either asking "am I right?" or,
        in their very first turn, just saying it. Not a data request, so it skips matching."""
        n_cand = sum(t.speaker == "Candidate" for t in self.turns)
        if n_cand > 2 or not (CONFIRM_RE.search(utterance) or n_cand == 1):
            return False
        from .matcher import _cos
        try:
            return _cos(llm.embed([f"search_query: {utterance[-1500:]}"])[0], self._prompt_vec) >= self.RESTATE_SIM
        except Exception:
            return False

    def _understand(self, utterance: str) -> dict:
        if self._is_restatement(utterance):
            return {"intent": "clarifying", "asked_clarifications": [], "requested_exhibits": [],
                    "unanswered_asks": [], "phases_touched": [], "shortlist": [], "confirm": True}
        exclude: set[str] = set()   # shown exhibits stay matchable, so a re-ask gets "it's already up"
        asks = extract_asks(utterance)
        # "...and I'm supposed to find the root cause. Am I right?" is a check on the problem statement,
        # not a data request. "So DAU is logged-in users, right?" is a real clarification, though, so a
        # confirmation ask only counts as one when nothing in the case matched it.
        try:
            short = self.matcher.shortlist(utterance, exclude)
        except Exception:
            short = []
        matched = {a for a, _ in short}
        confirm = [a for a in asks if CONFIRM_RE.search(a) and a not in matched and len(a.split()) <= 20]
        asks = [a for a in asks if a not in confirm]
        phases = self._phases(utterance)
        d = {"intent": self._heuristic_intent(utterance), "asked_clarifications": [], "requested_exhibits": [],
             "unanswered_asks": [], "phases_touched": phases, "shortlist": short, "confirm": bool(confirm)}
        if not short:
            d["unanswered_asks"] = asks
            return d
        conf = self._confirm(utterance, short)
        picked = conf.get("picked", [])
        d["already_shown"] = [m for m in picked if m in self.revealed_e]
        picked = [m for m in picked if m not in self.revealed_e]
        if conf.get("plan_check") and not picked:
            d["confirm"] = True
        d["asked_clarifications"] = [m for m in picked if m.startswith("Q")]
        d["requested_exhibits"] = [m for m in picked if m.startswith("E")]
        d["unanswered_asks"] = [a for i, (a, _) in enumerate(short) if i not in conf.get("answered", set())]
        d["unanswered_asks"] += [a for a in asks if a not in {s for s, _ in short}]
        return d

    def _heuristic_intent(self, u: str) -> str:
        if HINT_RE.search(u):
            return "asking_for_hint"
        if self.twist_id:
            return "answering_twist"
        if RECOMMEND_RE.search(u):
            return "recommending"
        # Broad phrases only count once the case is well under way: a north star picked before the
        # breakdown and guardrails, or a subtotal before the sanity check, is not the close.
        n_cand = sum(t.speaker == "Candidate" for t in self.turns)
        if n_cand >= 4 and self.progress >= self.CLOSE_PROGRESS and BROAD_RE.search(u) and self._is_closing(u):
            return "recommending"
        return "analysing"

    def _hint_target(self) -> str:
        """Which step the hint should aim at: the earliest step the candidate has skipped or done too
        thinly, judged by the 7B from everything they have said. Embedding phase tags are too noisy for
        this (they mark a step as done on one passing sentence). Falls back to the first untagged step."""
        keys = [k for k, _ in self.phases]
        fallback = next((k for k in keys if k not in self.phases_touched), keys[-1])
        said = [strip_hint_requests(t.text) for t in self.turns if t.speaker == "Candidate"]
        said = "\n".join(f"- {x}" for x in said if x)[-3000:]
        if not said:
            return keys[0]
        steps = "\n".join(f"{i}. {k}: {txt}" for i, (k, txt) in enumerate(zip(keys, phase_texts(self.case.type_key)), 1))
        prompt = (f"Case question: {self.case.prompt}\n\nWhat the candidate has said so far:\n{said}\n\n"
                  f"Steps of a PM answer to this case:\n{steps}\n\nWhich steps has the candidate ALREADY done? "
                  "For each one, copy the exact words where they do it. A passing mention does not count. "
                  "Leave out steps they have not done. "
                  "Answer with JSON {\"done\": [{\"step\": \"<step key>\", \"quote\": \"<their exact words>\"}]}.")
        norm = lambda x: re.sub(r"[^a-z0-9 ]+", " ", x.lower()).split()
        said_n = " ".join(norm(said))
        try:
            out = llm.chat_json([{"role": "user", "content": prompt}],
                                {"type": "object", "properties": {"done": {"type": "array", "items": {
                                    "type": "object", "properties": {"step": {"type": "string", "enum": keys},
                                                                     "quote": {"type": "string"}},
                                    "required": ["step", "quote"]}}}, "required": ["done"]}, temperature=0)
            # Same rule as the grader: a step only counts as done with a verbatim quote.
            done = {d["step"] for d in out.get("done", [])
                    if len(norm(d.get("quote", ""))) >= 3 and " ".join(norm(d["quote"])) in said_n}
            return next((k for k in keys if k not in done), keys[-1])
        except Exception:
            return fallback

    def _is_closing(self, u: str) -> bool:
        """One tiny 7B call: is this the candidate's final answer to the whole case, or a step on the way?"""
        prompt = (f"Case question: {self.case.prompt}\n\nThe candidate just said:\n\"{u[:1200]}\"\n\n"
                  "Is the candidate now giving their FINAL answer to the whole case question (their final "
                  "recommendation or decision, final estimate, or final root cause with a fix), rather than an "
                  "intermediate step (one segment, one sub-calculation, a hypothesis, picking a metric before "
                  "breaking it down or adding guardrails)? Answer with JSON {\"final\": true|false}.")
        try:
            out = llm.chat_json([{"role": "user", "content": prompt}],
                                {"type": "object", "properties": {"final": {"type": "boolean"}},
                                 "required": ["final"]}, temperature=0)
            return bool(out.get("final"))
        except Exception:
            return False

    def _phases(self, utterance: str) -> list[str]:
        sents = [s for s in re.split(r"(?<=[.?!])\s+|\n+", utterance) if len(s.split()) >= 4][:12]
        if not sents:
            return []
        try:
            vecs = llm.embed([f"search_query: {s}" for s in sents])
        except Exception:
            return []
        from .matcher import _cos
        keys = [k for k, _ in self.phases]
        hit = set()
        for v in vecs:
            scores = [_cos(v, sv) for sv in self._step_vecs]
            best = max(range(len(scores)), key=scores.__getitem__)
            if scores[best] >= 0.6:
                hit.add(keys[best])
        return [k for k in keys if k in hit]

    CLOSE_PROGRESS = 0.45  # share of the time box used before a broad closing phrase counts
    AUTO_ACCEPT = 0.72   # embedding score above which the top match is taken without asking the LLM
    MARGIN = 0.05        # LLM picks must score within this of the ask's best embedding match
    E_MARGIN = 0.03      # ...and exhibits within this (a wrong table on screen is worse than a missed one)
    AUTO_GAP = 0.03      # auto-accept needs the top match this far clear of the runner-up
    MAX_NEW_EXHIBITS = 2 # tables shared in one turn

    def _option_text(self, iid: str) -> str:
        if iid.startswith("Q"):
            q = self._qmap[iid]
            return f"A prepared answer to \"{q.q}\": {q.a}"
        return f"A data table, {self.matcher.text(iid)[:200]}"

    def _confirm(self, utterance: str, short: list) -> dict:
        """Per ask: auto-accept a very strong, clear top match. Otherwise the items close to the top
        (within MARGIN, E_MARGIN for tables) each get a strict yes/no from the 7B, all in one call.
        Judging one pair at a time is far more reliable for the 7B than picking from a list."""
        picked, answered = [], set()
        pending = []
        for i, (ask, cands) in enumerate(short):
            close_unshown = any(iid not in self.revealed_e and s >= cands[0][1] - self.MARGIN
                                for iid, s in cands[1:])
            clear = len(cands) == 1 or cands[0][1] - cands[1][1] >= self.AUTO_GAP
            if cands[0][1] >= self.AUTO_ACCEPT and clear and not (cands[0][0] in self.revealed_e and close_unshown):
                picked.append(cands[0][0])
                answered.add(i)
            else:
                pending.append(i)
        # Sentences of one question all point at the same item: once it is picked, they are covered.
        for i in list(pending):
            if short[i][1][0][0] in picked:
                pending.remove(i)
                answered.add(i)
        pairs = []
        for i in pending:
            ask, cands = short[i]
            top = cands[0][1]
            for iid, sc in cands[:3]:
                if sc >= top - (self.MARGIN if iid.startswith("Q") else self.E_MARGIN):
                    pairs.append((i, iid))
        if pairs:
            lines = [f'{n}. The candidate asked: "{short[i][0][:220]}"\n   Item: {self._option_text(iid)}'
                     for n, (i, iid) in enumerate(pairs, 1)]
            prompt = ("A case interviewer has prepared items to share. For each numbered pair, answer true only "
                      "if this item answers the candidate's question directly: it is about the same thing they "
                      "asked about, and they would get what they asked for from it. Answer false if the item is "
                      "about something else that only sounds related (a different product, platform, cause or "
                      "metric), or if the candidate is not asking for information at all.\n\n"
                      + "\n".join(lines)
                      + f"\n\nAnswer with JSON {{\"answers\": [true|false, ...]}}: exactly {len(pairs)} values, in order.")
            schema = {"type": "object", "properties": {"answers": {"type": "array", "items": {"type": "boolean"},
                      "minItems": len(pairs), "maxItems": len(pairs)}}, "required": ["answers"]}
            try:
                verdicts = llm.chat_json([{"role": "user", "content": prompt}], schema, temperature=0).get("answers", [])
            except Exception:
                verdicts = []
            for (i, iid), ok in zip(pairs, verdicts):
                if ok is True:
                    picked.append(iid)
                    answered.add(i)
        picked = list(dict.fromkeys(picked))
        new_ex = [x for x in picked if x.startswith("E") and x not in self.revealed_e]
        picked = [x for x in picked if not (x in new_ex and new_ex.index(x) >= self.MAX_NEW_EXHIBITS)]
        plan_check = any(CONFIRM_RE.search(short[i][0]) for i in pending if i not in answered)
        return {"picked": picked, "answered": answered, "intent": None, "plan_check": plan_check}

    # ---- natural replies, checked in Python so the 7B can't invent facts ----

    VOICE = ("You are a friendly, experienced PM case interviewer talking out loud to a candidate. Speak the "
             "way a person does in a live interview: short, plain, conversational, first person. No lists, no "
             "markdown, no quotation marks. Never praise or judge the candidate, never hint at the answer, "
             "never call them 'Candidate'.")

    def _public_context(self) -> str:
        # Background minus sentences that point at the answer (A1's "Diwali ... a known dip" leaked the
        # root cause when the candidate asked for it).
        from .agent import safe_context
        return safe_context(self.case)[:1500]

    @staticmethod
    def _nums(s: str) -> set[str]:
        return {n.replace(",", "") for n in NUM_RE.findall(s)}

    def _grounded(self, line: str, facts: str) -> bool:
        """Every number in the line must come from the facts (or the candidate's own words)."""
        allowed = self._nums(facts) | self._nums(self.case.prompt)
        allowed |= self._nums(" ".join(t.text for t in self.turns if t.speaker == "Candidate"))
        return bool(line) and self._nums(line) <= allowed

    def _confirm_restatement(self, utterance: str) -> str:
        """The candidate played the problem back and asked if they have it right. Later in the case a
        "right?" is them checking their approach, which the interviewer doesn't judge."""
        if sum(t.speaker == "Candidate" for t in self.turns) > 2:
            return self.rng.choice(["That's your call. Go ahead.", "Up to you. Keep going.",
                                    "Your call. Walk me through it."])
        prompt = (f"Case prompt given to the candidate:\n{self.case.prompt}\n\nThe candidate restated it as:\n"
                  f"\"{utterance[:1000]}\"\n\nDoes the restatement keep the facts of the prompt (company, metric, "
                  "size and direction of the change, time frame, and the task)? Paraphrase and extra politeness "
                  "are fine. Answer with JSON {\"matches\": true|false}.")
        try:
            ok = llm.chat_json([{"role": "user", "content": prompt}],
                               {"type": "object", "properties": {"matches": {"type": "boolean"}},
                                "required": ["matches"]}, temperature=0).get("matches", True)
        except Exception:
            ok = True
        if ok:
            return self.rng.choice(CONFIRMS)
        return f"Not quite. Here it is again: {self.case.prompt.strip(chr(34) + ' ')}"

    def _share_line(self, eids: list[str], already: bool = False) -> str:
        names = [f"{e}, {self._emap[e].title.rstrip(':. ').replace(' (illustrative)', '')}" for e in eids]
        listed = names[0] if len(names) == 1 else ", and ".join([", ".join(names[:-1]), names[-1]])
        if already:
            return f"You already have that in {listed}."
        return self.rng.choice([f"I'm putting up {listed}.", f"Here's {listed}.", f"Sure, sharing {listed}."])

    def _answer_clarifications(self, utterance: str, qids: list[str]) -> str:
        """Say only the part of the prepared answers that the candidate asked about, in spoken English.
        The 7B picks which casebook sentences answer the question, then words the reply. Python falls
        back to the kept sentences if the reply adds a number, brings back a named thing from a dropped
        sentence, coaches ("good question", "let's focus"), asks something back, or runs long."""
        sents = [x.strip() for q in qids for x in re.split(r"(?<=[.!?])\s+", self._qmap[q].a) if x.strip()]
        notes = "\n".join(f"{i}. {x}" for i, x in enumerate(sents))
        names = lambda t: set(re.findall(r"\b[A-Z][a-zA-Z-]{2,}", t))
        # A sentence naming something the candidate asked about ("...or does it include Instamart?") is
        # the answer to that part, so it is always kept, and the reply has to say it.
        asked_names = names(utterance) - names(self.case.prompt) - {"Okay", "And", "What", "Is", "Do", "Or", "So"}
        must = {i for i, x in enumerate(sents) if names(x) & asked_names}
        must_say = sorted(names(" ".join(sents[i] for i in must)) & asked_names)
        user = (f"Case: {self.case.prompt}\n\nThe candidate just said:\n\"{utterance[-900:]}\"\n\n"
                f"Your notes, one fact per line (the only facts you may use):\n{notes}\n\n"
                "Step 1, keep: the numbers of the note lines that answer a question the candidate asked. Drop "
                "a line that answers a different question (another product, segment, platform or period than "
                "the one they asked about). Keep a line like 'You tell me whether that matters.'\n"
                "Step 2, reply: say the kept facts to the candidate in one or two short spoken sentences, with "
                "numbers exactly as written. If they offered options, say which one it is. Only facts: no "
                "comments on their question, no suggestions, no questions back."
                + (f" They asked about {', '.join(must_say)}: say plainly what your notes say about it."
                   if must_say else "")
                + "\n\nAnswer with JSON {\"keep\": [line numbers], \"reply\": \"...\"}.")
        schema = {"type": "object", "properties": {
            "keep": {"type": "array", "items": {"type": "integer"}}, "reply": {"type": "string"}},
            "required": ["keep", "reply"]}
        try:
            out = llm.chat_json([{"role": "system", "content": self.VOICE},
                                 {"role": "user", "content": user}], schema, temperature=0.2)
        except Exception:
            out = {}
        keep = {i for i in out.get("keep", []) if isinstance(i, int) and 0 <= i < len(sents)}
        keep = sorted(keep | must) or list(range(len(sents)))
        kept = " ".join(sents[i] for i in keep)
        line = self._drop_filler(self._clean(out.get("reply", "")), kept)
        leaked = (names(" ".join(sents)) - names(" ".join([kept, utterance, self.case.prompt]))) & names(line)
        # The model often words the rest well but skips the named part: add that casebook sentence back.
        for i in sorted(must):
            if not (names(sents[i]) & asked_names & names(line)):
                line = f"{line} {sents[i]}".strip()
        bad = (not line or leaked or COMMENTARY_RE.search(line) or "?" in line
               or not self._grounded(line, kept) or len(line.split()) > 50)
        return kept if bad else line

    GUESS_RE = re.compile(r"\b(i (think|guess|suspect|feel|bet|believe|would say|'d say|'d expect|would expect)|"
                          r"probably|maybe|my (guess|hypothesis)|could it be|is it)\b", re.I)

    def _held_line(self, held: list[str], utterance: str) -> tuple[str, list[str]]:
        """Confirm only the parts of a held answer the candidate named (matched by word stems against
        their last three turns); otherwise ask for their guess. Deterministic, so nothing new leaks."""
        guess = " ".join([t.text for t in self.turns if t.speaker == "Candidate"][-3:])
        g = _stems(guess)
        named, confirmed = [], []
        for q in held:
            ans = re.sub(r"\s*\((illustrative|assumed?)[^)]*\)\.?", "", self._qmap[q].a, flags=re.I)
            pieces = [p.strip(" .'\"") for p in re.split(r"[;,]|\.\s", ans) if p.strip(" .'\"")]
            own = g - _stems(self._qmap[q].q)
            hit = [p for p in pieces if _stems(p) & own]
            if hit:
                named += hit
                confirmed.append(q)
        if named:   # confirm without restating the list, so no unnamed part slips out
            return self.rng.choice(["Yes, that's a big part of what we see.", "Yes, that shows up a lot.",
                                    "That's right, that's where a lot of it comes from."]), confirmed
        asking = re.search(r"\b(could|can|would|will) you\b|\b(tell|show|give) me\b|\bdo (we|you) have\b", utterance, re.I)
        if held and self.GUESS_RE.search(utterance) and not asking:
            return "Not especially, from what we see. Any other guess?", []
        return self.rng.choice(HAND_BACK), []

    def _no_data_line(self, utterance: str, unanswered: list[str]) -> str:
        """A question the case has no prepared answer for: answer from the public context if it covers
        it, otherwise say there's nothing notable there."""
        ctx = self._public_context()
        user = (f"Case: {self.case.prompt}\n\nPublic background you may share:\n{ctx or '(none)'}\n\n"
                f"Their question: {' / '.join(unanswered)}\n\n"
                "If the background answers it, answer in one short sentence using its numbers exactly. If they "
                "ask how to define or scope something and the background doesn't say, tell them to pick the "
                "usual definition and state it as an assumption. Otherwise say briefly that nothing unusual "
                "shows up there, or that they can assume it's normal. Only that: no comments on their question, "
                "no suggestions, no questions back. Answer with JSON {\"reply\": \"...\"}.")
        try:
            out = llm.chat_json([{"role": "system", "content": self.VOICE}, {"role": "user", "content": user}],
                                {"type": "object", "properties": {"reply": {"type": "string"}},
                                 "required": ["reply"]}, temperature=0.2)
            line = self._drop_filler(self._clean(out.get("reply", "")), "")
        except Exception:
            line = ""
        if (not line or COMMENTARY_RE.search(line) or "?" in line or not self._grounded(line, ctx)
                or len(line.split()) > 40):
            return self.rng.choice(NO_DATA)
        return line

    def _guarded_line(self, instruction: str, unanswered: list[str], intent: str) -> str:
        """Hints and twist probes. Not streamed, so it can be checked first: qwen3 knows a lot about
        real companies and volunteers causes ("Diwali overlap") that only the case data should reveal.
        A line naming anything the candidate hasn't said is replaced by a fixed one; a nudge keeps only
        its guiding question."""
        hint = self.interventions[-1] if intent == "asking_for_hint" and self.interventions else None
        step = dict(self.phases).get(hint["target"], "the next step").lower() if hint else ""
        fallback = {"nudge": f"Before you go further, what would you want to check on {step}?",
                    "redirect": f"I'd spend a moment on {step} now. How would you approach it?",
                    "rescue": f"Let's work through {step} together. Take it one piece at a time."
                    }.get(hint["kind"] if hint else "", "Okay.")
        try:
            line = self._clean(llm.chat(self._speaker_messages(instruction, unanswered), temperature=0.4))
        except Exception:
            return fallback
        line = self._drop_filler(line, "")
        if hint and hint["kind"] == "nudge":
            qs = [x for x in re.split(r"(?<=[.!?])\s+", line) if x.endswith("?")]
            line = qs[0] if qs else ""
        names = lambda t: set(re.findall(r"\b[A-Z][a-zA-Z-]{2,}", t))
        said = " ".join([self.case.prompt] + [t.text for t in self.turns if t.speaker == "Candidate"])
        allowed = names(said) | {"What", "How", "Why", "Which", "When", "Where", "Before", "Have", "Did", "Could",
                                 "Would", "Can", "Is", "Are", "Do", "Does", "Think", "Okay", "Let", "Let's",
                                 "Try", "Start", "If", "You", "Your", "The", "That", "This", "And", "But", "So"}
        if not line or names(line) - allowed or not self._grounded(line, "") or len(line.split()) > 45:
            return fallback
        return line

    # ---- speaker (only for hints and probes) ----

    def _speaker_messages(self, instruction: str, unanswered: list[str]) -> list[dict]:
        c = self.case
        system = ("You write ONE short spoken line (max 2 sentences) for a PM case interviewer. Plain speech, no "
                  "lists or markdown. Never invent facts or numbers. Never praise or say whether the candidate is "
                  "right. Never reveal the answer. Never address them as 'Candidate'.")
        last = "\n".join(f"{t.speaker}: {t.text[:400]}" for t in self.turns[-3:])
        user = (f"Case: {c.prompt}\n\nRecent conversation:\n{last}\n\n"
                + (f"Questions you have no data for: {' / '.join(unanswered)}\n\n" if unanswered else "")
                + f"Instruction: {instruction}\n\nWrite only the line.")
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]
