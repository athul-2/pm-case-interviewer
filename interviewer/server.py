"""Local web app: case library, practice sets, live interviews (text or voice), grading queue, reports.

  python -m interviewer.server            # http://127.0.0.1:8770

Everything stays on this machine. Grading runs one session at a time in a background worker, and waits
while an interview is in progress so the grader (qwen3:8b) and interviewer (qwen2.5:7b) are never loaded
together on 16 GB.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import evaluator, llm, models
from .agent import make_interview
from .casepack import CUSTOM, TYPE_KEYS, load_all
from .engine import SESSIONS, Interview

WEB = Path(__file__).resolve().parent / "web"
TYPE_LETTER = {"rca": "A", "product_design": "B", "product_strategy": "C",
               "situational": "D", "metrics": "E", "guesstimate": "F"}
TYPE_NAME = {v: k for k, v in TYPE_KEYS.items()}
LEVELS = ["apm", "pm", "senior"]

app = FastAPI(title="Case Interviewer")

# ---------------- state ----------------

_cases_lock = threading.Lock()
_cases: dict = {}


def cases() -> dict:
    with _cases_lock:
        if not _cases:
            _cases.update(load_all())
        return _cases


def reload_cases() -> None:
    with _cases_lock:
        _cases.clear()
        _cases.update(load_all())


INTERVIEWS: dict[str, dict] = {}   # iid -> {"iv": Interview, "lock": Lock, "voice": bool}
JOBS: dict[str, dict] = {}         # session id -> {"status", "messages", "error", "level", "queued_at"}
GRADE_Q: "queue.Queue[str]" = queue.Queue()


IDLE_SECONDS = 15 * 60   # an interview left open without activity stops blocking the grader


def _active_interviews() -> int:
    now = time.time()
    return sum(1 for s in INTERVIEWS.values()
               if not s.get("closed") and now - s.get("touched", now) < IDLE_SECONDS)


def _grade_worker() -> None:
    while True:
        sid = GRADE_Q.get()
        job = JOBS[sid]
        while _active_interviews():          # don't load the grader next to the interviewer
            job["status"] = "waiting"
            time.sleep(2)
        job["status"] = "grading"
        job["started_at"] = time.time()
        try:
            path = SESSIONS / f"{sid}.json"
            session = json.loads(path.read_text())
            case = cases()[session["case_id"]]
            report = evaluator.evaluate(case, session, level=job.get("level"),
                                        progress=lambda m: job["messages"].append(m))
            session["report"] = report
            path.write_text(json.dumps(session, indent=1))
            job["status"] = "done"
        except Exception as exc:
            job["status"] = "error"
            job["error"] = f"{exc}"
            traceback.print_exc()
        job["finished_at"] = time.time()


threading.Thread(target=_grade_worker, daemon=True).start()

# ---------------- voice (lazy; uses this computer's mic and speakers) ----------------

_voice = {"rec": None, "stt": None, "tts": None, "error": None}
_voice_lock = threading.Lock()


def voice_ready(case=None) -> dict:
    from . import voice
    with _voice_lock:
        if _voice["rec"] is None:
            try:
                _voice["rec"] = voice.Recorder()
                _voice["stt"] = voice.Transcriber("")
                _voice["stt"].warm_up()
                _voice["tts"] = voice.Speaker()
                _voice["error"] = None
            except Exception as exc:
                _voice["error"] = str(exc)
                _voice["rec"] = None
        if case is not None and _voice["stt"] is not None:
            _voice["stt"].prompt = voice.case_prompt(case)
    return _voice


# ---------------- session helpers ----------------

def _session_files() -> list[Path]:
    return sorted(SESSIONS.glob("*.json"), reverse=True) if SESSIONS.exists() else []


def _source(d: dict) -> str:
    # Sessions saved before the 'source' field were all simulate.py test runs.
    return d.get("source") or "simulated"


def _summary(path: Path, d: dict | None = None) -> dict:
    d = d or json.loads(path.read_text())
    r = d.get("report") or {}
    sid = path.stem
    job = JOBS.get(sid)
    status = "graded" if r else (job["status"] if job else "ungraded")
    return {
        "id": sid, "case_id": d.get("case_id"), "title": d.get("title"), "case_type": d.get("case_type"),
        "started": d.get("started"), "minutes": d.get("minutes"), "level": d.get("level", "pm"),
        "source": _source(d), "status": status,
        "verdict": r.get("verdict"), "score": r.get("weighted_mean"),
        "answer_quality": r.get("answer_quality"),
        "turns": sum(1 for t in d.get("turns", []) if t.get("speaker") == "Candidate"),
    }


def _all_sessions(include_sim: bool) -> list[tuple[dict, dict]]:
    out = []
    for p in _session_files():
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(d, dict):  # e.g. grader_compare.json, not a session
            continue
        if not include_sim and _source(d) != "practice":
            continue
        out.append((_summary(p, d), d))
    return out


# ---------------- API: cases ----------------

def _short_company(s: str) -> str:
    return re.split(r"\s*[(;]|\.\s", s or "", maxsplit=1)[0].strip().rstrip(".")


def _short_difficulty(s: str) -> str:
    m = re.match(r"\s*(Easy|Medium|Hard)(?:[-/ ]+(Easy|Medium|Hard))?", s or "", re.I)
    return re.sub(r"[-/ ]+", "-", m.group(0).strip()).title() if m else (s or "").split(" ")[0]

@app.get("/api/cases")
def list_cases(include_sim: bool = False):
    stats: dict[str, list] = {}
    for s, _ in _all_sessions(include_sim):
        stats.setdefault(s["case_id"], []).append(s)
    out = []
    for c in cases().values():
        ss = stats.get(c.id, [])
        graded = [s for s in ss if s["score"] is not None]
        out.append({
            "id": c.id, "title": c.title, "case_type": c.case_type, "type_key": c.type_key,
            "company": _short_company(c.company), "difficulty": _short_difficulty(c.difficulty),
            "time_box": c.time_box_minutes, "prompt": c.prompt, "custom": c.custom,
            "attempts": len(ss),
            "best": max((s["score"] for s in graded), default=None),
            "last": graded[0]["score"] if graded else None,
            "last_verdict": graded[0]["verdict"] if graded else None,
        })
    return out


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, include_sim: bool = False):
    c = cases().get(case_id.upper())
    if not c:
        raise HTTPException(404, "No such case")
    sessions = [s for s, _ in _all_sessions(include_sim) if s["case_id"] == c.id]
    return {"id": c.id, "title": c.title, "case_type": c.case_type, "type_key": c.type_key,
            "company": _short_company(c.company), "difficulty": _short_difficulty(c.difficulty),
            "difficulty_note": c.difficulty, "time_box": c.time_box_minutes,
            "prompt": c.prompt, "custom": c.custom,
            "n_clarifications": len(c.clarifications), "n_exhibits": len(c.exhibits),
            "n_twists": len(c.twists), "sessions": sessions}


class NewCase(BaseModel):
    title: str
    type_key: str
    company: str = ""
    prompt: str
    difficulty: str = "Medium"
    time_box: int = 25
    context: str = ""
    clarifications: list[dict] = []    # [{q, a}]
    exhibits: list[str] = []           # each one exhibit's text
    twists: list[dict] = []            # [{twist, response}]
    answer: str = ""
    pitfalls: list[str] = []
    guardrails: str = ""


@app.post("/api/cases")
def add_case(nc: NewCase):
    if nc.type_key not in TYPE_LETTER:
        raise HTTPException(400, f"type_key must be one of {list(TYPE_LETTER)}")
    if not nc.prompt.strip() or not nc.title.strip():
        raise HTTPException(400, "Title and prompt are required")
    clar = [{"q": x.get("q", "").strip(), "a": x.get("a", "").strip()} for x in nc.clarifications
            if x.get("q", "").strip() and x.get("a", "").strip()]
    if not clar:
        raise HTTPException(400, "Add at least one clarifying question with its answer, so the "
                                 "interviewer has something to hand over")
    twists = [{"twist": x.get("twist", "").strip(), "response": x.get("response", "").strip()}
              for x in nc.twists if x.get("twist", "").strip()]
    letter = TYPE_LETTER[nc.type_key]
    used = [int(cid[1:]) for cid in cases() if cid[0] == letter and cid[1:].isdigit()]
    cid = f"{letter}{max(used, default=0) + 1}"
    exhibits = [f"Exhibit {i} - {e.strip()}" for i, e in enumerate((e for e in nc.exhibits if e.strip()), 1)]
    data = {
        "pun_title": nc.title.strip(), "case_type": TYPE_NAME[nc.type_key],
        "asked_at": nc.company or "By me", "subject_company": nc.company,
        "round_and_level": "", "difficulty": nc.difficulty, "market_and_industry": "",
        "time_box_minutes": str(nc.time_box), "exact_prompt": nc.prompt.strip(),
        "source_and_verification": "added from the Case Interviewer app", "context_as_of": nc.context,
        "regulatory_context": "", "public_data_anchors": "", "framework_applied": "",
        "clarifying_questions": clar, "interviewer_data_reveals": exhibits or "No exhibits.",
        "solution_walkthrough": [], "answer_and_recommendation": nc.answer,
        "counter_metrics_guardrails": nc.guardrails, "follow_up_twists": twists,
        "common_pitfalls": [p for p in nc.pitfalls if p.strip()], "skills_tested": [],
        "linked_teardown": "None", "uncertain": [],
    }
    CUSTOM.mkdir(exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", nc.title).strip("_")[:40] or "Case"
    (CUSTOM / f"{cid}_{slug}.json").write_text(json.dumps(data, indent=1))
    reload_cases()
    if cid not in cases():
        raise HTTPException(500, "Saved, but the case failed to load; check the file in custom_cases/")
    return {"id": cid}


@app.delete("/api/cases/{case_id}")
def delete_case(case_id: str):
    c = cases().get(case_id.upper())
    if not c or not c.custom:
        raise HTTPException(400, "Only cases you added can be deleted")
    for p in CUSTOM.glob(f"{c.id}_*.json"):
        p.unlink()
    reload_cases()
    return {"ok": True}


# ---------------- API: performance by case type ----------------

@app.get("/api/types")
def type_stats(include_sim: bool = False):
    by_type: dict[str, dict] = {}
    for c in cases().values():
        t = by_type.setdefault(c.type_key, {"type_key": c.type_key, "name": c.case_type, "cases": 0,
                                            "sessions": [], "dims": {}})
        t["cases"] += 1
    for s, d in _all_sessions(include_sim):
        c = cases().get(s["case_id"])
        if not c or s["score"] is None:
            continue
        t = by_type[c.type_key]
        t["sessions"].append({"id": s["id"], "case_id": s["case_id"], "started": s["started"],
                              "score": s["score"], "verdict": s["verdict"]})
        for dim in (d.get("report") or {}).get("dimensions", []):
            agg = t["dims"].setdefault(dim["name"], [])
            agg.append(dim["score"])
    out = []
    for key in TYPE_LETTER:
        t = by_type.get(key)
        if not t:
            continue
        ss = sorted(t["sessions"], key=lambda s: s["started"] or 0)
        scores = [s["score"] for s in ss]
        dims = sorted(({"name": n, "avg": round(sum(v) / len(v), 2), "n": len(v)} for n, v in t["dims"].items()),
                      key=lambda x: x["avg"])
        verdicts: dict[str, int] = {}
        for s in ss:
            verdicts[s["verdict"]] = verdicts.get(s["verdict"], 0) + 1
        out.append({
            "type_key": key, "name": t["name"], "cases": t["cases"], "graded": len(ss),
            "avg": round(sum(scores) / len(scores), 2) if scores else None,
            "recent_avg": round(sum(scores[-3:]) / len(scores[-3:]), 2) if scores else None,
            "trend": [{"started": s["started"], "score": s["score"], "case_id": s["case_id"]} for s in ss],
            "verdicts": verdicts, "weakest": dims[:2], "strongest": dims[-2:][::-1] if len(dims) > 2 else [],
        })
    return out


# ---------------- API: sessions and reports ----------------

@app.get("/api/sessions")
def list_sessions(case_id: str | None = None, type_key: str | None = None, include_sim: bool = False):
    out = []
    for s, _ in _all_sessions(include_sim):
        if case_id and s["case_id"] != case_id.upper():
            continue
        if type_key:
            c = cases().get(s["case_id"])
            if not c or c.type_key != type_key:
                continue
        out.append(s)
    return out


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    path = SESSIONS / f"{Path(sid).name}.json"
    if not path.exists():
        raise HTTPException(404, "No such session")
    d = json.loads(path.read_text())
    d["id"] = path.stem
    d["source"] = _source(d)
    job = JOBS.get(path.stem)
    d["job"] = job
    c = cases().get(d.get("case_id"))
    if c:
        d["exhibits"] = [{"id": e.id, "title": e.title, "body": e.body} for e in c.exhibits
                         if e.id in d.get("revealed_exhibits", [])]
        d["case_prompt"] = c.prompt
    return d


class GradeReq(BaseModel):
    level: str | None = None


@app.post("/api/sessions/{sid}/grade")
def regrade(sid: str, req: GradeReq):
    path = SESSIONS / f"{Path(sid).name}.json"
    if not path.exists():
        raise HTTPException(404, "No such session")
    if JOBS.get(path.stem, {}).get("status") in ("queued", "waiting", "grading"):
        return {"status": JOBS[path.stem]["status"]}
    _enqueue(path.stem, req.level)
    return {"status": "queued"}


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str):
    path = SESSIONS / f"{Path(sid).name}.json"
    if not path.exists():
        raise HTTPException(404, "No such session")
    if JOBS.get(path.stem, {}).get("status") in ("waiting", "grading"):
        raise HTTPException(409, "That session is being graded")
    path.unlink()
    return {"ok": True}


def _enqueue(sid: str, level: str | None) -> None:
    JOBS[sid] = {"status": "queued", "messages": [], "error": None, "level": level, "queued_at": time.time()}
    GRADE_Q.put(sid)


@app.get("/api/jobs")
def jobs():
    return [{"id": k, **{kk: vv for kk, vv in v.items()}} for k, v in JOBS.items()
            if v["status"] in ("queued", "waiting", "grading") or time.time() - v.get("finished_at", 0) < 600]


# ---------------- API: live interviews ----------------

class StartReq(BaseModel):
    case_id: str
    level: str = "pm"
    voice: bool = False


@app.post("/api/interviews")
def start_interview(req: StartReq):
    c = cases().get(req.case_id.upper())
    if not c:
        raise HTTPException(404, "No such case")
    if req.level not in LEVELS:
        raise HTTPException(400, f"level must be one of {LEVELS}")
    for s in INTERVIEWS.values():          # only one live interview at a time
        s["closed"] = True
    voice_error = None
    if req.voice:
        v = voice_ready(c)
        voice_error = v["error"]
    model_id = models.settings()["interviewer_model"]
    ok, why = models.can_start(model_id)
    if not ok:
        raise HTTPException(402, why)
    iv = make_interview(c, req.level, model_id)
    iv.interviewer_model = model_id
    opening = iv.open().text
    iid = uuid.uuid4().hex[:10]
    INTERVIEWS[iid] = {"iv": iv, "lock": threading.Lock(), "voice": req.voice and not voice_error,
                       "touched": time.time()}
    if INTERVIEWS[iid]["voice"]:
        _voice["tts"].say(opening)
    return {"iid": iid, "opening": opening, "voice": INTERVIEWS[iid]["voice"], "voice_error": voice_error,
            "model": {"id": model_id, "label": models.MODELS[model_id]["label"]},
            "case": {"id": c.id, "title": c.title, "case_type": c.case_type, "company": _short_company(c.company),
                     "time_box": c.time_box_minutes, "prompt": c.prompt}}


def _live(iid: str) -> dict:
    s = INTERVIEWS.get(iid)
    if not s or s.get("closed"):
        raise HTTPException(404, "This interview has ended")
    s["touched"] = time.time()
    return s


class SayReq(BaseModel):
    text: str


def _stream(s: dict, events):
    tts = _voice["tts"] if s["voice"] else None

    def gen():
        with s["lock"]:
            try:
                for ev in events:
                    if ev.kind == "say" and tts:
                        tts.feed(ev.text)
                    yield json.dumps({"kind": ev.kind, "text": ev.text, "exhibit_id": ev.exhibit_id,
                                      "title": next((e.title for e in s["iv"].case.exhibits
                                                     if e.id == ev.exhibit_id), "")}) + "\n"
                if tts:
                    tts.flush()
            except Exception as exc:
                traceback.print_exc()
                yield json.dumps({"kind": "error", "text": str(exc)}) + "\n"
            yield json.dumps({"kind": "status", "done": s["iv"].done,
                              "elapsed": round(s["iv"].elapsed_min, 1)}) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/interviews/{iid}/say")
def say(iid: str, req: SayReq):
    s = _live(iid)
    if not req.text.strip():
        raise HTTPException(400, "Say something first")
    if s["voice"]:
        _voice["tts"].stop()
    return _stream(s, s["iv"].respond(req.text.strip()))


@app.post("/api/interviews/{iid}/hint")
def hint(iid: str):
    s = _live(iid)
    if s["voice"]:
        _voice["tts"].stop()
    return _stream(s, s["iv"].hint())


@app.post("/api/interviews/{iid}/listen/start")
def listen_start(iid: str):
    s = _live(iid)
    if not s["voice"]:
        raise HTTPException(400, "Voice is off for this interview")
    _voice["tts"].stop()                    # barge-in
    _voice["rec"].start()
    return {"ok": True}


@app.post("/api/interviews/{iid}/listen/stop")
def listen_stop(iid: str):
    s = _live(iid)
    if not s["voice"]:
        raise HTTPException(400, "Voice is off for this interview")
    audio = _voice["rec"].stop()
    text = _voice["stt"](audio)
    return {"text": text, "seconds": round(len(audio) / 16000, 1)}


class EndReq(BaseModel):
    grade: bool = True


@app.post("/api/interviews/{iid}/end")
def end_interview(iid: str, req: EndReq):
    s = INTERVIEWS.get(iid)
    if not s:
        raise HTTPException(404, "No such interview")
    with s["lock"]:
        iv = s["iv"]
        iv.end()
        s["closed"] = True
        if s["voice"]:
            _voice["tts"].stop()
            try:
                _voice["stt"].unload()      # make room for the grader
            except Exception:
                pass
        path = iv.save()
    INTERVIEWS.pop(iid, None)
    cand = sum(1 for t in iv.turns if t.speaker == "Candidate")
    graded = req.grade and cand >= 2
    if graded:
        _enqueue(path.stem, iv.level)
    return {"session_id": path.stem, "queued": graded,
            "reason": None if graded else ("Not graded: fewer than 2 answers" if req.grade else "Grading skipped")}


class ModelReq(BaseModel):
    interviewer_model: str | None = None
    grader_model: str | None = None
    monthly_budget: float | None = None


class KeyReq(BaseModel):
    provider: str
    key: str | None = None


@app.get("/api/models")
def get_models():
    return models.summary()


@app.post("/api/models")
def set_models(req: ModelReq):
    try:
        models.save_settings(interviewer_model=req.interviewer_model, grader_model=req.grader_model,
                             monthly_budget=req.monthly_budget)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return models.summary()


@app.post("/api/keys")
def set_key(req: KeyReq):
    """Store (or with an empty key, remove) an API key locally: ~/.config/case_interviewer/keys.json, 0600."""
    key = (req.key or "").strip()
    if key:
        problem = models.check_key(req.provider, key)
        if problem:
            raise HTTPException(400, problem)
    try:
        models.save_key(req.provider, key or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return models.summary()


class EndpointReq(BaseModel):
    provider: str
    base_url: str | None = None


@app.post("/api/endpoint")
def set_endpoint(req: EndpointReq):
    """Qwen on Model Studio: an account-specific endpoint (blank restores the default)."""
    try:
        models.save_base_url(req.provider, req.base_url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return models.summary()


@app.get("/api/health")
def health():
    try:
        import urllib.request
        with urllib.request.urlopen(llm.OLLAMA + "/api/tags", timeout=2) as r:
            models = [m["name"] for m in json.loads(r.read())["models"]]
        ok = True
    except Exception:
        models, ok = [], False
    need = [llm.INTERVIEW_MODEL, llm.GRADER_MODEL, llm.EMBED_MODEL]
    missing = [m for m in need if not any(x == m or x.startswith(m + ":") for x in models)]
    return {"ollama": ok, "missing_models": missing, "voice_error": _voice["error"]}


# ---------------- static UI ----------------

@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Always revalidate the UI files, so an updated app.js/styles.css shows up on reload."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")


def main():
    import uvicorn
    port = int(os.environ.get("PORT", "8770"))
    print(f"Case Interviewer on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
