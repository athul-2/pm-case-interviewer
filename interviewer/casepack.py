"""Load the casebook JSONs and split each case into two packs.

- interviewer pack: what the interviewer may say (prompt, clarification answers, exhibits, twists)
- grader pack: the answer key (walkthrough, recommendation, pitfalls, guardrails)

The interviewer model only ever sees the interviewer pack, so it cannot leak the answer.
Case files are inconsistent (lists vs. numbered strings), so everything is normalised here.
"""
from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

CASEBOOK = Path(os.environ.get("CASEBOOK_DIR", Path(__file__).resolve().parents[1] / "casebook" / "results"))

TYPE_KEYS = {
    "RCA": "rca",
    "Product Design": "product_design",
    "Product Strategy": "product_strategy",
    "Situational": "situational",
    "Metrics": "metrics",
    "Guesstimate": "guesstimate",
}


@dataclass
class Clarification:
    id: str
    q: str
    a: str


@dataclass
class Exhibit:
    id: str
    title: str
    body: str


@dataclass
class Twist:
    id: str
    twist: str
    strong_response: str


@dataclass
class Case:
    id: str
    title: str
    case_type: str          # display name, e.g. "RCA"
    type_key: str           # rubric key, e.g. "rca"
    company: str
    difficulty: str
    time_box: str
    prompt: str
    context: str            # public facts the interviewer may confirm
    clarifications: list[Clarification]
    exhibits: list[Exhibit]
    twists: list[Twist]
    # grader-only
    framework_applied: str
    walkthrough: list[dict]
    answer: str
    guardrails: str
    pitfalls: list[str]
    skills: list[str]
    custom: bool = False

    @property
    def time_box_minutes(self) -> int:
        nums = [int(n) for n in re.findall(r"\d+", self.time_box)]
        return max(nums) if nums else 25

    def interviewer_pack(self) -> dict:
        return {
            "id": self.id, "title": self.title, "case_type": self.case_type,
            "company": self.company, "prompt": self.prompt, "context": self.context,
            "clarifications": [asdict(c) for c in self.clarifications],
            "exhibits": [asdict(e) for e in self.exhibits],
            "twists": [asdict(t) for t in self.twists],
        }


# ---------- normalisers ----------

_NUM_SPLIT = re.compile(r"(?:^|\s|\n)\(?(\d{1,2})[).]\s+")


def _split_numbered(text: str) -> list[str]:
    """Split '1) foo 2) bar' or '1. foo\n2. bar' into items. Falls back to [text]."""
    text = text.strip()
    marks = list(_NUM_SPLIT.finditer(text))
    # keep only an increasing 1,2,3... sequence to avoid splitting on numbers inside sentences
    seq, want = [], 1
    for m in marks:
        if int(m.group(1)) == want:
            seq.append(m)
            want += 1
    if len(seq) < 2:
        return [text]
    items = []
    for i, m in enumerate(seq):
        end = seq[i + 1].start() if i + 1 < len(seq) else len(text)
        items.append(text[m.end():end].strip())
    return [i for i in items if i]


def _as_list(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return _split_numbered(v)
    return []


def _parse_qa(item) -> tuple[str, str]:
    if isinstance(item, dict):
        return item.get("q") or item.get("question", ""), item.get("a") or item.get("answer", "")
    m = re.match(r"\s*Q:\s*(.*?)\s+A:\s*(.*)", item, re.S)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return item.strip(), ""


def _parse_twist(item) -> tuple[str, str]:
    if isinstance(item, dict):
        return item.get("twist", ""), item.get("response", "")
    s = item.strip()
    for pat in (r"Twist:\s*(.*?)\s*(?:Strong\s+)?Response:\s*(.*)",
                r"(.*?)\s*(?:Strong\s+)?[Rr]esponse:\s*(.*)",
                r"(.*?)\s*->\s*(.*)",
                r"('.*?'|\".*?\"|.*?\?)\s*[-–]\s+(.*)"):
        m = re.match(pat, s, re.S)
        if m and m.group(2).strip():
            return m.group(1).strip().strip("'\""), m.group(2).strip()
    return s, ""


def _parse_exhibits(v) -> list[Exhibit]:
    if isinstance(v, list):
        chunks = [str(x) for x in v]
        header = ""
    else:
        parts = [p.strip() for p in re.split(r"(?:^|(?<=[\s.]))(?=Exhibit \d+\b)", v.strip()) if p.strip()]
        header = "\n".join(p for p in parts if not p.startswith("Exhibit")).strip()
        chunks = [p for p in parts if p.startswith("Exhibit")] or [v.strip()]
    out = []
    for i, c in enumerate(chunks, 1):
        head = c.split("\n", 1)[0]
        title = re.sub(r"^Exhibit \d+\s*[-–:]?\s*", "", head)
        title = re.sub(r"^\((?:illustrative|real)[^)]*\)\s*[-–:]?\s*", "", title)
        title = re.split(r"(?::\s|\.\s)", title, maxsplit=1)[0][:120]
        out.append(Exhibit(id=f"E{i}", title=title.strip(), body=c.strip()))
    if header and out:
        out[0].body = header + "\n\n" + out[0].body  # keep the "illustrative numbers" note
    return out


def _skills(v) -> list[str]:
    if isinstance(v, list):
        return [str(s).strip() for s in v]
    return [s.strip() for s in re.split(r"[,;]", v) if s.strip()]


def _text(v) -> str:
    if isinstance(v, list):
        return "\n".join(f"- {x}" for x in v)
    return str(v or "")


def load_case(path: Path) -> Case:
    d = json.loads(path.read_text())
    cid = path.stem.split("_", 1)[0]
    clar = [Clarification(f"Q{i}", *_parse_qa(x)) for i, x in enumerate(_as_list(d["clarifying_questions"]), 1)]
    twists = [Twist(f"T{i}", *_parse_twist(x)) for i, x in enumerate(_as_list(d["follow_up_twists"]), 1)]
    company = d.get("subject_company") or d.get("asked_at", "")
    if company.lower().startswith(("none", "same")):
        company = d.get("asked_at", "")
    return Case(
        id=cid,
        title=d["pun_title"],
        case_type=d["case_type"],
        type_key=TYPE_KEYS.get(d["case_type"], "product_strategy"),
        company=company,
        difficulty=d.get("difficulty", ""),
        time_box=d.get("time_box_minutes", "25"),
        # Some casebook prompts carry the follow-up twist in brackets; the interviewer must not read it out.
        prompt=re.sub(r"\s*\((?:follow-up )?twist:[^)]*\)", "", d["exact_prompt"], flags=re.I),
        context=_text(d.get("context_as_of")),
        clarifications=clar,
        exhibits=_parse_exhibits(d["interviewer_data_reveals"]),
        twists=twists,
        framework_applied=d.get("framework_applied", ""),
        walkthrough=d.get("solution_walkthrough", []),
        answer=d.get("answer_and_recommendation", ""),
        guardrails=_text(d.get("counter_metrics_guardrails")),
        pitfalls=[str(p) for p in _as_list(d.get("common_pitfalls", []))],
        skills=_skills(d.get("skills_tested", "")),
    )


CUSTOM = Path(__file__).resolve().parents[1] / "custom_cases"   # cases added from the UI


def load_all() -> dict[str, Case]:
    cases = {}
    for folder in (CASEBOOK, CUSTOM):
        for p in sorted(folder.glob("*.json")):
            try:
                c = load_case(p)
            except Exception as exc:  # a malformed custom case shouldn't take the app down
                print(f"skipping {p.name}: {exc}")
                continue
            c.custom = folder == CUSTOM
            cases[c.id] = c
    return dict(sorted(cases.items(), key=lambda kv: (kv[0][0], int(kv[0][1:]))))


if __name__ == "__main__":
    for cid, c in load_all().items():
        bad_qa = sum(1 for q in c.clarifications if not q.a)
        bad_tw = sum(1 for t in c.twists if not t.strong_response)
        print(f"{cid:4} {c.case_type:16} Q={len(c.clarifications):2} (noA {bad_qa}) "
              f"E={len(c.exhibits):2} T={len(c.twists)} (noR {bad_tw}) P={len(c.pitfalls)} "
              f"{c.time_box_minutes}m  {c.title}")
