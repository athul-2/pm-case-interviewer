"""Minimal Ollama client (stdlib only)."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Iterator

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
if "://" not in OLLAMA:   # Ollama's own convention allows "127.0.0.1:11434" or "0.0.0.0"
    OLLAMA = "http://" + (OLLAMA.replace("0.0.0.0", "127.0.0.1") if ":" in OLLAMA else OLLAMA.replace("0.0.0.0", "127.0.0.1") + ":11434")
# qwen3:8b (thinking off) since 2026-11-16: qwen2.5:7b could not judge whether a casebook item answers
# the question (it opened the release note for "any updates to the tracking tool?"). Same model as the
# grader, so only one LLM is ever loaded. INTERVIEW_MODEL=qwen2.5:7b goes back.
INTERVIEW_MODEL = os.environ.get("INTERVIEW_MODEL", "qwen3:8b")
GRADER_MODEL = os.environ.get("GRADER_MODEL", "qwen3:8b")


def _post(path: str, body: dict, stream: bool = False):
    req = urllib.request.Request(
        OLLAMA + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    return urllib.request.urlopen(req, timeout=600)


def chat(messages: list[dict], model: str | None = None, *, fmt=None, think: bool | None = None,
         temperature: float = 0.4, num_ctx: int = 8192, keep_alive: str = "30m",
         top_logprobs: int = 0, raw: bool = False):
    """Return the reply text, or with raw=True the whole response (incl. logprobs when asked for)."""
    model = model or INTERVIEW_MODEL   # read at call time, so the app can switch local models
    body = {"model": model, "messages": messages, "stream": False, "keep_alive": keep_alive,
            "options": {"temperature": temperature, "num_ctx": num_ctx}}
    if top_logprobs:
        body["logprobs"] = True
        body["top_logprobs"] = top_logprobs
    if fmt is not None:
        body["format"] = fmt
    if think is None and model.startswith("qwen3"):
        think = False  # qwen3 thinks by default; only the grader wants it
    if think is not None:
        body["think"] = think
    with _post("/api/chat", body) as r:
        resp = json.loads(r.read())
    return resp if raw else resp["message"]["content"]


def parse_json(out: str) -> dict:
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        start, end = out.find("{"), out.rfind("}")
        try:
            return json.loads(out[start:end + 1]) if start >= 0 else {}
        except json.JSONDecodeError:
            return {}


def chat_json(messages: list[dict], schema: dict, **kw) -> dict:
    return parse_json(chat(messages, fmt=schema, **kw))


def stream(messages: list[dict], model: str | None = None, *, temperature: float = 0.6,
           num_ctx: int = 8192) -> Iterator[str]:
    model = model or INTERVIEW_MODEL
    body = {"model": model, "messages": messages, "stream": True, "keep_alive": "30m",
            "options": {"temperature": temperature, "num_ctx": num_ctx}}
    if model.startswith("qwen3"):
        body["think"] = False
    with _post("/api/chat", body) as r:
        for line in r:
            if not line.strip():
                continue
            chunk = json.loads(line)
            piece = chunk.get("message", {}).get("content", "")
            if piece:
                yield piece
            if chunk.get("done"):
                break


EMBED_MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")


def embed(texts: list[str]) -> list[list[float]]:
    with _post("/api/embed", {"model": EMBED_MODEL, "input": texts, "keep_alive": "30m"}) as r:
        return json.loads(r.read())["embeddings"]


def unload(model: str) -> None:
    """Free RAM before loading the other model (16 GB machine)."""
    try:
        with _post("/api/generate", {"model": model, "keep_alive": 0}):
            pass
    except Exception:
        pass
