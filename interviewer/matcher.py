"""Match what the candidate asked to the case's clarifying questions and exhibits, using embeddings.

Deterministic and ~100 ms, unlike asking the 7B model to pick from a list.
"""
from __future__ import annotations

import math
import re

from . import llm

ASK_PATTERNS = re.compile(
    r"\?|^(can|could|may) (i|we|you)\b|\b(i'?d|i would) (like|love) to (see|know|look)|\b(show|share|give) me\b|"
    r"\bdo (we|you) have\b|\bis there (any )?data\b|\btell me\b|"
    r"\b(i (just )?want(ed)? to|i'?d like to) (confirm|check|understand|know|clarify)\b|\bconfirm(ing)? (that|whether|if)\b",
    re.I,
)


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) + 1e-9)


def exhibit_summary(e, n: int = 220) -> str:
    """Title + the start of the exhibit's own content (drops the 'Exhibit N -' prefix and book-wide notes)."""
    body = e.body
    m = re.search(r"Exhibit \d+", body)
    if m:
        body = body[m.start():]
    body = re.sub(r"^Exhibit \d+\s*[-–:]?\s*(\((?:illustrative|real)[^)]*\)\s*[-–:]?\s*)?", "", body)
    body = re.sub(r"\s+", " ", body.replace("|", " "))
    return body[:n]


def extract_asks(utterance: str) -> list[str]:
    """Sentences in the candidate's turn that ask the interviewer for something."""
    parts = re.split(r"(?<=[?.!])\s+|\n+", utterance.strip())
    asks = [p.strip() for p in parts if p.strip() and ASK_PATTERNS.search(p.strip())]
    return asks[:6]


class Matcher:
    def __init__(self, clarifications, exhibits, q_threshold: float = 0.70, e_threshold: float = 0.66):
        self.items = [(c.id, c.q) for c in clarifications]
        self.items += [(e.id, exhibit_summary(e)) for e in exhibits]
        self.q_thr, self.e_thr = q_threshold, e_threshold
        self.vecs = llm.embed([f"search_document: {t}" for _, t in self.items]) if self.items else []

    def shortlist(self, utterance: str, exclude: set[str], k: int = 4, floor: float = 0.55):
        """[(ask_sentence, [(item_id, score), ...])] - top-k candidates per ask, for the LLM to confirm."""
        asks = extract_asks(utterance)
        if not asks or not self.items:
            return []
        avecs = llm.embed([f"search_query: {a}" for a in asks])
        out = []
        for a, av in zip(asks, avecs):
            scored = sorted(((_cos(av, iv), iid) for (iid, _), iv in zip(self.items, self.vecs)
                             if iid not in exclude), reverse=True)
            ids = [(iid, round(s, 3)) for s, iid in scored[:k] if s >= floor]
            if ids:
                out.append((a, ids))
        return out

    def text(self, iid: str) -> str:
        return next(t for i, t in self.items if i == iid)

    def match(self, utterance: str, exclude: set[str]) -> list[tuple[str, float, str]]:
        """Returns [(item_id, score, ask_sentence)] above threshold, best per item."""
        asks = extract_asks(utterance)
        if not asks or not self.items:
            return []
        avecs = llm.embed([f"search_query: {a}" for a in asks])
        best: dict[str, tuple[float, str]] = {}
        for a, av in zip(asks, avecs):
            scored = sorted(((_cos(av, iv), iid) for (iid, _), iv in zip(self.items, self.vecs)), reverse=True)
            for s, iid in scored[:2]:
                thr = self.q_thr if iid.startswith("Q") else self.e_thr
                if s >= thr and iid not in exclude and s > best.get(iid, (0, ""))[0]:
                    best[iid] = (s, a)
        return sorted(((i, round(s, 3), a) for i, (s, a) in best.items()), key=lambda x: -x[1])

    def debug(self, utterance: str):
        asks = extract_asks(utterance)
        avecs = llm.embed([f"search_query: {a}" for a in asks]) if asks else []
        for a, av in zip(asks, avecs):
            scored = sorted(((_cos(av, iv), iid) for (iid, _), iv in zip(self.items, self.vecs)), reverse=True)
            print(f"  {a[:70]!r} -> {[(i, round(s, 2)) for s, i in scored[:3]]}")
