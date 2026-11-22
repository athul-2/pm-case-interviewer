"""Model choices for the live interviewer, API keys, and the monthly API budget.

- MODELS: every interviewer option (local Ollama pipeline, Claude, Gemini) with prices per 1M tokens.
- settings.json (case_interviewer/): the chosen interviewer model and the monthly budget in USD.
- Keys: ANTHROPIC_API_KEY / GEMINI_API_KEY from the environment, else ~/.config/case_interviewer/keys.json
  (written by the web app with 0600 permissions). Keys are never sent anywhere but their own provider.
- usage.jsonl (case_interviewer/): one line per API call with tokens and cost. The month's spend is summed
  from it, and every API call is checked against the budget before it is sent (charge_guard), using a
  worst-case estimate, so the cap can't be crossed by one call.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "settings.json"
USAGE = ROOT / "usage.jsonl"
KEYS = Path.home() / ".config" / "case_interviewer" / "keys.json"

DEFAULT_BUDGET = 10.0
_lock = threading.Lock()

# Prices in USD per 1M tokens, checked 2026-11-16 (Anthropic: platform pricing; Google: AI Studio pricing).
# in / out / cache read / cache write. Thinking tokens bill as output on both. Where a provider doesn't
# publish a cache-write price, the input price is used.
MODELS: dict[str, dict] = {
    "local:qwen3:8b": {
        "provider": "ollama", "model": "qwen3:8b", "group": "Local", "label": "Qwen3 8B",
        "note": "Free and offline. Scripted pipeline: the model only picks and words casebook lines.",
    },
    "local:qwen2.5:7b": {
        "provider": "ollama", "model": "qwen2.5:7b", "group": "Local", "label": "Qwen2.5 7B",
        "note": "Free and offline. The older local model; weaker at matching questions to the case.",
    },
    "claude-opus-5-5": {
        "provider": "anthropic", "model": "claude-opus-5-5", "group": "Claude", "label": "Claude Opus 5.5",
        "price": (4.00, 20.00, 0.20, 5.00), "per_interview": 0.40,
        "note": "Best judgement and the most natural interviewer. Runs at low effort to stay quick.",
    },
    "claude-sonnet-5-5": {
        "provider": "anthropic", "model": "claude-sonnet-5-5", "group": "Claude", "label": "Claude Sonnet 5.5",
        "price": (2.00, 10.00, 0.10, 2.50), "per_interview": 0.20,
        "note": "Faster and half the price of Opus; still follows the conversation well.",
    },
    "claude-haiku-5-5": {
        "provider": "anthropic", "model": "claude-haiku-5-5", "group": "Claude", "label": "Claude Haiku 5.5",
        "price": (0.10, 0.50, 0.01, 0.125), "per_interview": 0.02,
        "note": "Very cheap and fast. Lighter judgement than Sonnet or Opus.",
    },
    "gemini-2.5-flash": {
        "provider": "gemini", "model": "gemini-2.5-flash", "group": "Gemini", "label": "Gemini 2.5 Flash",
        "price": (0.30, 2.50, 0.03, 0.30), "per_interview": 0.04,
        "note": "Deprecated by Google: from 1 Dec 2026 requests route to Gemini 3.5 Flash-Lite (same price).",
    },
    "gemini-3.5-flash-lite": {
        "provider": "gemini", "model": "gemini-3.5-flash-lite", "group": "Gemini", "label": "Gemini 3.5 Flash-Lite",
        "price": (0.30, 2.50, 0.03, 0.30), "per_interview": 0.04,
        "note": "Google's current lightweight model; where 2.5 Flash goes after 1 Dec 2026.",
    },
    "gemini-2.5-flash-lite": {
        "provider": "gemini", "model": "gemini-2.5-flash-lite", "group": "Gemini", "label": "Gemini 2.5 Flash-Lite",
        "price": (0.10, 0.40, 0.01, 0.10), "per_interview": 0.01,
        "note": "The cheapest option. Lightest judgement of the API models.",
    },
    # OpenAI-compatible chat APIs, called with the standard library (agent.py _run_openai). Checked 2026-11-17
    # against each provider's docs.
    "deepseek-v4.1-flash": {
        "provider": "deepseek", "model": "deepseek-flash", "group": "DeepSeek", "label": "DeepSeek V4.1 Flash",
        "base_url": "https://api.deepseek.com", "extra": {"thinking": {"type": "disabled"}},
        # off-peak; peak (01:00-04:00 and 06:00-10:00 UTC, Mon-Fri) is double, see price_peak
        "price": (0.15, 0.60, 0.003, 0.15), "price_peak": (0.30, 1.20, 0.006, 0.30), "per_interview": 0.03,
        "note": "Very cheap, fast replies with thinking off. Prices double at peak: 6:30-9:30 am and 11:30 am-3:30 pm IST on weekdays.",
    },
    "qwen3.8-flash": {
        "provider": "qwen", "model": "qwen3.8-flash", "group": "Qwen", "label": "Qwen3.8 Flash",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "extra": {"enable_thinking": False},
        "price": (0.15, 0.47, 0.016, 0.15), "per_interview": 0.02,
        "note": "Tuned for low-latency chat; thinking off. Alibaba Model Studio, international region.",
    },
    "minimax-m3.1-flash": {
        "provider": "minimax", "model": "MiniMax-M3.1-Flash", "group": "MiniMax", "label": "MiniMax M3.1 Flash",
        "base_url": "https://api.minimax.io/v1", "extra": {"reasoning_effort": "low", "reasoning_split": True},
        "price": (0.15, 0.60, 0.015, 0.15), "per_interview": 0.03,
        "note": "Good multi-turn flow. Always thinks a little (lowest effort), so replies are slightly slower.",
    },
}

# OpenRouter: one key for many models. Slugs and list prices from openrouter.ai/api/v1/models, 2026-11-17.
# OpenRouter reports the real charge in usage.cost, which is what the ledger records; these prices are used
# only for the before-the-call worst case. Reasoning goes through OpenRouter's unified `reasoning` parameter.
_OR = "https://openrouter.ai/api/v1"
for _slug, _label, _price, _per, _effort, _note in [
    ("deepseek/deepseek-v4.1-flash", "DeepSeek V4.1 Flash", (0.30, 1.20, 0.006, 0.30), 0.04, "none",
     "Very cheap and quick, thinking off."),
    ("qwen/qwen3.8-flash", "Qwen3.8 Flash", (0.15, 0.47, 0.016, 0.15), 0.02, "none",
     "Tuned for low-latency chat, thinking off."),
    ("minimax/minimax-m3.1-flash", "MiniMax M3.1 Flash", (0.15, 0.60, 0.015, 0.15), 0.03, "low",
     "Good multi-turn flow; always thinks a little."),
    ("google/gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", (0.30, 2.50, 0.03, 0.30), 0.04, "low",
     "Google's current lightweight model."),
    ("google/gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", (0.10, 0.40, 0.01, 0.10), 0.01, None,
     "The cheapest option; doesn't think by default."),
    ("anthropic/claude-haiku-5.5", "Claude Haiku 5.5", (0.10, 0.50, 0.01, 0.125), 0.02, "low",
     "Very cheap and fast Claude."),
    ("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5", (2.00, 10.00, 0.10, 2.50), 0.20, "low",
     "Strong judgement; follows the conversation well."),
]:
    MODELS["or:" + _slug] = {
        "provider": "openrouter", "model": _slug, "group": "OpenRouter", "label": _label + " (OpenRouter)",
        "base_url": _OR, "extra": ({"reasoning": {"effort": _effort}} if _effort else {}),
        "price": _price, "per_interview": _per, "reported_cost": True,
        "note": _note + " Billed through your OpenRouter credits.",
    }

_env_local = os.environ.get("INTERVIEW_MODEL", "").strip()
if _env_local and f"local:{_env_local}" not in MODELS:
    MODELS[f"local:{_env_local}"] = {
        "provider": "ollama", "model": _env_local, "group": "Local", "label": f"{_env_local} (local)",
        "note": "Free and offline. Your own Ollama model, set with INTERVIEW_MODEL.",
    }
DEFAULT_MODEL = f"local:{_env_local}" if _env_local else "local:qwen3:8b"

# Grader choices. The grader needs per-token probabilities (logprobs) for its expected 1-4 scores, so only
# models verified to return them are offered. OpenRouter routes DeepSeek to a provider that supports logprobs
# when the request sets provider.require_parameters (checked 2026-11-21).
GRADERS: dict[str, dict] = {
    "local:qwen3:8b": {"label": f"{os.environ.get('GRADER_MODEL', 'qwen3:8b')} (local)", "note": "Free and offline. Set GRADER_MODEL to use another Ollama model. About 5-9 minutes per report on a 16 GB laptop; faster with more memory or a GPU.",
                       "per_report": 0.0},
    "or:deepseek/deepseek-v4.1-flash": {"label": "DeepSeek V4.1 Flash (OpenRouter)", "per_report": 0.05,
                                        "note": "About a minute per report, a few cents each. Frees your machine's memory."},
}
DEFAULT_GRADER = "local:qwen3:8b"
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
           "qwen": "DASHSCOPE_API_KEY", "minimax": "MINIMAX_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
PROVIDER_LABEL = {"anthropic": "Anthropic (Claude)", "gemini": "Google (Gemini)", "deepseek": "DeepSeek",
                  "qwen": "Alibaba Model Studio (Qwen)", "minimax": "MiniMax", "openrouter": "OpenRouter"}
BASE_URL_ENV = {"qwen": "DASHSCOPE_BASE_URL"}   # Model Studio gives each workspace its own endpoint


def is_peak(model_id: str, t: float | None = None) -> bool:
    if "price_peak" not in MODELS[model_id]:
        return False
    g = time.gmtime(t or time.time())
    return g.tm_wday < 5 and (1 <= g.tm_hour < 4 or 6 <= g.tm_hour < 10)


class BudgetExceeded(RuntimeError):
    pass


# ---------- settings ----------

def settings() -> dict:
    try:
        s = json.loads(SETTINGS.read_text())
    except Exception:
        s = {}
    if s.get("interviewer_model") not in MODELS:
        s["interviewer_model"] = DEFAULT_MODEL
    if s.get("grader_model") not in GRADERS:
        s["grader_model"] = DEFAULT_GRADER
    try:
        s["monthly_budget"] = max(0.0, float(s.get("monthly_budget", DEFAULT_BUDGET)))
    except (TypeError, ValueError):
        s["monthly_budget"] = DEFAULT_BUDGET
    return s


def save_settings(**kw) -> dict:
    s = settings()
    if kw.get("interviewer_model") is not None:
        if kw["interviewer_model"] not in MODELS:
            raise ValueError("Unknown model")
        s["interviewer_model"] = kw["interviewer_model"]
    if kw.get("grader_model") is not None:
        if kw["grader_model"] not in GRADERS:
            raise ValueError("Unknown grader")
        s["grader_model"] = kw["grader_model"]
    if "monthly_budget" in kw and kw["monthly_budget"] is not None:
        s["monthly_budget"] = max(0.0, min(1000.0, float(kw["monthly_budget"])))
    SETTINGS.write_text(json.dumps(s, indent=1))
    return s


# ---------- keys ----------

def _stored_keys() -> dict:
    try:
        return json.loads(KEYS.read_text())
    except Exception:
        return {}


def api_key(provider: str) -> str | None:
    env = KEY_ENV.get(provider)
    return (os.environ.get(env) if env else None) or _stored_keys().get(provider) or None


def key_source(provider: str) -> str | None:
    env = KEY_ENV.get(provider)
    if env and os.environ.get(env):
        return "environment"
    return "saved" if _stored_keys().get(provider) else None


def save_key(provider: str, key: str | None) -> None:
    if provider not in KEY_ENV:
        raise ValueError("Unknown provider")
    keys = _stored_keys()
    if key:
        keys[provider] = key.strip()
    else:
        keys.pop(provider, None)
    KEYS.parent.mkdir(parents=True, exist_ok=True)
    KEYS.write_text(json.dumps(keys))
    os.chmod(KEYS, 0o600)


def base_url(model_id: str) -> str:
    m = MODELS[model_id]
    env = BASE_URL_ENV.get(m["provider"])
    return ((os.environ.get(env) if env else None) or _stored_keys().get(m["provider"] + "_base_url")
            or m.get("base_url", "")).rstrip("/")


def save_base_url(provider: str, url: str | None) -> None:
    if provider not in BASE_URL_ENV:
        raise ValueError("This provider has a fixed endpoint")
    url = (url or "").strip()
    if url and not url.startswith("https://"):
        raise ValueError("The endpoint must start with https://")
    keys = _stored_keys()
    if url:
        keys[provider + "_base_url"] = url.rstrip("/")
    else:
        keys.pop(provider + "_base_url", None)
    KEYS.parent.mkdir(parents=True, exist_ok=True)
    KEYS.write_text(json.dumps(keys))
    os.chmod(KEYS, 0o600)


KEY_PREFIX = {"openrouter": "sk-or-", "anthropic": "sk-ant-"}


def check_key(provider: str, key: str) -> str | None:
    """Return a problem with this key, or None. Format check for every provider with a known prefix; for
    OpenRouter also a live check against its free GET /api/v1/key endpoint (no credits used)."""
    pre = KEY_PREFIX.get(provider)
    if pre and not key.startswith(pre):
        return (f"That doesn't look like an API key from {PROVIDER_LABEL[provider]}: those start with \"{pre}\". "
                "Copy the key itself (shown once when you create it), not its name.")
    if provider == "openrouter":
        import urllib.error
        import urllib.request
        req = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=15):
                return None
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return "OpenRouter rejected this key. Check it on openrouter.ai/settings/keys, or create a new one."
            return None   # OpenRouter having a bad moment is not the key's fault
        except Exception:
            return None   # offline: save it anyway
    return None


def available(model_id: str) -> bool:
    m = MODELS[model_id]
    return m["provider"] == "ollama" or bool(api_key(m["provider"]))


# ---------- spend ----------

def _month() -> str:
    return time.strftime("%Y-%m")


def month_spend(month: str | None = None) -> float:
    month = month or _month()
    total = 0.0
    try:
        with USAGE.open() as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("month") == month:
                    total += float(r.get("cost", 0))
    except FileNotFoundError:
        pass
    return round(total, 6)


def cost(model_id: str, inp: int = 0, out: int = 0, cache_read: int = 0, cache_write: int = 0,
         peak: bool | None = None) -> float:
    m = MODELS[model_id]
    if peak is None:
        peak = is_peak(model_id)
    p = m.get("price_peak") if peak and m.get("price_peak") else m.get("price")
    if not p:
        return 0.0
    pin, pout, pread, pwrite = p
    return (inp * pin + out * pout + cache_read * pread + cache_write * pwrite) / 1e6


def charge_guard(model_id: str, prompt_chars: int, max_out: int) -> None:
    """Refuse a call whose worst case (whole prompt uncached, max output) would cross the budget."""
    if not MODELS[model_id].get("price"):
        return
    worst = cost(model_id, inp=prompt_chars // 3 + 200, out=max_out, peak=True)   # always the dearer rate
    budget = settings()["monthly_budget"]
    if month_spend() + worst > budget:
        raise BudgetExceeded(f"This month's API budget (${budget:.2f}) is used up.")


def can_grade(grader_id: str) -> tuple[bool, str]:
    """Before an API grading run: key present, and room for a report (with a 3x margin)."""
    if not MODELS[grader_id].get("price"):
        return True, ""
    m = MODELS[grader_id]
    if not api_key(m["provider"]):
        return False, f"No {PROVIDER_LABEL.get(m['provider'])} API key is set."
    need = GRADERS[grader_id]["per_report"] * 3
    if month_spend() + need > settings()["monthly_budget"]:
        return False, "This month's API budget can't cover a report; it will be graded locally instead."
    return True, ""


def can_start(model_id: str) -> tuple[bool, str]:
    """Before an interview: is there room for a typical interview on this model?"""
    m = MODELS[model_id]
    if not m.get("price"):
        return True, ""
    if not api_key(m["provider"]):
        return False, f"No {PROVIDER_LABEL.get(m['provider'], m['group'])} API key is set. Add one in Models."
    budget, spent = settings()["monthly_budget"], month_spend()
    need = m.get("per_interview", 0.5) * (3.0 if m.get("price_peak") else 1.5)
    if spent + need > budget:
        return False, (f"This month's API spend is ${spent:.2f} of ${budget:.2f}, which leaves too little for "
                       f"an interview on {m['label']} (about ${m['per_interview']:.2f} each).")
    return True, ""


def record(model_id: str, *, inp: int = 0, out: int = 0, cache_read: int = 0, cache_write: int = 0,
           billed_model: str | None = None, session: str = "", actual_cost: float | None = None) -> float:
    """Log one call. actual_cost (OpenRouter's usage.cost, in USD) replaces the price-table estimate when
    the provider reports it; otherwise cost comes from the price table."""
    priced = billed_model if billed_model in MODELS else model_id
    c = cost(priced, inp, out, cache_read, cache_write)
    if actual_cost is not None:
        try:
            c = float(actual_cost) if float(actual_cost) >= 0 else c
        except (TypeError, ValueError):
            pass
    row = {"t": round(time.time(), 1), "month": _month(), "model": priced, "in": inp, "out": out,
           "cache_read": cache_read, "cache_write": cache_write, "cost": round(c, 6), "session": session}
    with _lock, USAGE.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return c


def summary() -> dict:
    s = settings()
    spent = month_spend()
    by_model: dict[str, float] = {}
    try:
        with USAGE.open() as f:
            for line in f:
                r = json.loads(line)
                if r.get("month") == _month():
                    by_model[r["model"]] = by_model.get(r["model"], 0) + r.get("cost", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {
        "interviewer_model": s["interviewer_model"], "monthly_budget": s["monthly_budget"],
        "month": _month(), "spent": round(spent, 4), "by_model": {k: round(v, 4) for k, v in by_model.items()},
        "keys": {p: key_source(p) for p in KEY_ENV},
        "providers": [{"id": p, "label": PROVIDER_LABEL[p], "key": key_source(p),
                       "key_problem": (bool(api_key(p)) and bool(KEY_PREFIX.get(p))
                                       and not api_key(p).startswith(KEY_PREFIX[p])),
                       "base_url": (base_url(next(k for k, m in MODELS.items() if m["provider"] == p))
                                    if p in BASE_URL_ENV else None)} for p in KEY_ENV],
        "models": [{"id": k, **{x: v for x, v in m.items() if x != "price"},
                    "price": m.get("price"), "available": available(k), "peak_now": is_peak(k)}
                   for k, m in MODELS.items()],
        "grader_model": s["grader_model"],
        "graders": [{"id": k, **g, "available": available(k)} for k, g in GRADERS.items()],
    }
