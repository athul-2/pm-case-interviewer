"""Offline checks for the API interviewer: fake clients for every provider, a temp ledger, no network, no cost.

  python3 -m interviewer.test_agent

Covers: exhibit sharing (and its "asked to see data" gate), twist, closing, cost logging (including
OpenRouter's reported cost and DeepSeek peak pricing), reasoning passed back in history, the budget hard stop,
can_start, banned-phrase removal, and the local fallback.
"""
from __future__ import annotations

import calendar
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

from . import models

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("ok   " if cond else "FAIL ") + label)
    if not cond:
        FAILS.append(label)


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    models.USAGE, models.SETTINGS, models.KEYS = tmp / "usage.jsonl", tmp / "settings.json", tmp / "keys.json"
    for p in models.KEY_ENV:
        models.save_key(p, "test-key-not-real")
    from .agent import AgentInterview, _OpenAICompat, _drop_banned, make_interview
    from .casepack import load_all
    case = load_all()["A1"]

    def fresh(mid: str) -> AgentInterview:
        iv = AgentInterview(case, "pm", mid)
        iv._complete = lambda system, user: {}   # skip the review pass (it would call the provider)
        iv.open()
        return iv

    # --- Anthropic: tool loop, twist, close, cost ---
    def usage(**kw):
        return NS(input_tokens=kw.get("i", 3000), output_tokens=kw.get("o", 60),
                  cache_read_input_tokens=kw.get("cr", 0), cache_creation_input_tokens=kw.get("cw", 0))
    script = [
        ([NS(type="text", text="Sure, I'll put it up."),
          NS(type="tool_use", id="t1", name="share_exhibit", input={"exhibit_id": "E3"})], "tool_use"),
        ([NS(type="text", text="There it is.")], "end_turn"),
        ([NS(type="tool_use", id="t2", name="present_twist", input={})], "tool_use"),
        ([NS(type="text", text="One more: what if it were only Bengaluru?")], "end_turn"),
        ([NS(type="text", text="Can you say more?")], "end_turn"),
        ([NS(type="tool_use", id="t3", name="end_interview", input={})], "tool_use"),
        ([NS(type="text", text="Thanks, that's time.")], "end_turn"),
    ]
    sent = []

    class FakeMsgs:
        def create(self, **kw):
            sent.append(kw)
            blocks, stop = script.pop(0)
            return NS(content=blocks, stop_reason=stop, usage=usage(cr=2500), model=kw["model"])

    iv = fresh("claude-sonnet-5-5")
    iv._client = NS(beta=NS(messages=FakeMsgs()))
    ev = list(iv.respond("Can I see the funnel?"))
    check([e.exhibit_id for e in ev if e.kind == "exhibit"] == ["E3"], "anthropic: funnel request shares E3")
    check(" ".join(e.text for e in ev if e.kind == "say") == "There it is.", "anthropic: only the final line is spoken")
    list(iv.respond("My recommendation: roll back the default."))
    check(iv.twist_id is not None and iv.landed, "anthropic: twist presented, case landed")
    list(iv.respond("I'd check the city-level releases"))
    ev = list(iv.respond("And roll it back there too"))
    check(iv.done and any(e.kind == "end" for e in ev), "anthropic: interview closes after the twist")
    check(sent[0].get("fallbacks") == "default" and sent[0]["output_config"] == {"effort": "low"},
          "anthropic: low effort + server-side fallback")
    check(iv.cost > 0, "anthropic: cost recorded")

    # --- share gate: a yes/no question must not put a table up ---
    script2 = [([NS(type="tool_use", id="g1", name="share_exhibit", input={"exhibit_id": "E2"})], "tool_use"),
               ([NS(type="text", text="No, same definition.")], "end_turn")]

    class FakeMsgs2:
        def create(self, **kw):
            blocks, stop = script2.pop(0)
            return NS(content=blocks, stop_reason=stop, usage=usage(), model=kw["model"])
    iv = fresh("claude-haiku-5-5")
    iv._client = NS(beta=NS(messages=FakeMsgs2()))
    ev = list(iv.respond("Has the definition changed?"))
    check(not any(e.kind == "exhibit" for e in ev), "share gate: no table for a yes/no question")

    # --- Gemini ---
    from google.genai import types

    def gresp(parts):
        return NS(candidates=[NS(content=types.Content(role="model", parts=parts))],
                  usage_metadata=NS(prompt_token_count=3000, cached_content_token_count=0,
                                    candidates_token_count=40, thoughts_token_count=0))
    gscript = [gresp([types.Part(function_call=types.FunctionCall(name="share_exhibit", args={"exhibit_id": "E4"}))]),
               gresp([types.Part(text="Here's the split.")])]
    gcfg = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            gcfg.append(config)
            return gscript.pop(0)
    iv = fresh("gemini-2.5-flash")
    iv._client = NS(models=FakeModels())
    ev = list(iv.respond("Can I see payment success split by platform and method?"))
    check([e.exhibit_id for e in ev if e.kind == "exhibit"] == ["E4"], "gemini: shares E4")
    check(gcfg[0].thinking_config.thinking_budget == 0, "gemini: thinking off on 2.5")

    # --- OpenAI-compatible: DeepSeek direct, MiniMax, OpenRouter ---
    tc = {"role": "assistant", "content": "Sure.", "reasoning_content": "they want E3", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "share_exhibit", "arguments": "{\"exhibit_id\": \"E3\"}"}}]}
    final = {"role": "assistant", "content": "<think>x</think>There it is."}
    for mid, u, want_cost in [
        ("deepseek-v4.1-flash", {"prompt_tokens": 3000, "completion_tokens": 50, "prompt_cache_hit_tokens": 2800}, None),
        ("minimax-m3.1-flash", {"prompt_tokens": 3000, "completion_tokens": 120}, None),
        ("or:qwen/qwen3.8-flash", {"prompt_tokens": 3000, "completion_tokens": 40, "cost": 0.000512}, 0.001024),
    ]:
        osent, oscript = [], [dict(tc), dict(final)]

        class Fake(_OpenAICompat):
            def post(self, url, key, body):
                osent.append(json.loads(json.dumps(body)))
                return {"choices": [{"message": oscript.pop(0)}], "usage": u}
        iv = fresh(mid)
        iv._client = Fake()
        iv._history = [{"role": "system", "content": iv.system}]
        ev = list(iv.respond("Can I see the funnel?"))
        check([e.exhibit_id for e in ev if e.kind == "exhibit"] == ["E3"]
              and " ".join(e.text for e in ev if e.kind == "say") == "There it is.", f"{mid}: tool loop, <think> stripped")
        check(any(m.get("reasoning_content") for m in osent[1]["messages"]), f"{mid}: reasoning passed back")
        if want_cost is not None:
            check(abs(iv.cost - want_cost) < 1e-9, f"{mid}: OpenRouter reported cost used")

    # --- peak pricing ---
    t_peak = calendar.timegm((2026, 11, 17, 7, 0, 0))
    t_off = calendar.timegm((2026, 11, 17, 12, 0, 0))
    t_sat = calendar.timegm((2026, 11, 21, 7, 0, 0))
    check(models.is_peak("deepseek-v4.1-flash", t_peak) and not models.is_peak("deepseek-v4.1-flash", t_off)
          and not models.is_peak("deepseek-v4.1-flash", t_sat), "deepseek: peak window (weekday 06-10 UTC, not weekends)")

    # --- budget ---
    models.save_settings(monthly_budget=0.0001)
    iv = fresh("claude-opus-5-5")
    iv._client = NS(beta=NS(messages=FakeMsgs()))
    ev = list(iv.respond("Hello"))
    check(iv.budget_stopped and any(e.kind == "end" for e in ev), "budget: hard stop before the call")
    check(not models.can_start("claude-opus-5-5")[0], "budget: can_start refuses when the cap is used")
    models.save_settings(monthly_budget=10)
    check(models.can_start("local:qwen3:8b")[0], "local models are never budget-blocked")

    # --- text clean-up and factory ---
    check(_drop_banned("Take a look and tell me what you see.") == "Okay, go on.", "banned phrase removed")
    check(_drop_banned("I'm not going to do the arithmetic for you, but walk me through it.") == "Walk me through it.",
          "refusal clause removed, help kept")
    check(models.check_key("openrouter", "abc") is not None, "key check: wrong OpenRouter prefix refused")
    check(type(make_interview(case, "pm", "local:qwen3:8b")).__name__ == "Interview", "local model -> pipeline")

    # --- interviewer conduct: commitment gate, probe and hedge steers, per-type rules ---
    from .agent import TYPE_RULES
    from .engine import Turn
    iv = fresh("deepseek-v4.1-flash")
    iv.turns.append(Turn("Candidate", "I'd look at the funnel next.", 1.0, {}))
    out = iv._execute("present_twist", {}, [])
    check(out.startswith("Not yet") and iv.twist_id is None, "twist gate: refused before a committed call")
    iv.turns.append(Turn("Candidate", "My recommendation: roll back the default.", 2.0, {}))
    check(iv._execute("present_twist", {}, []).startswith("The twist"), "twist gate: allowed after a recommendation")
    iv = fresh("deepseek-v4.1-flash")
    check("choose" in iv._turn_note("Honestly it depends, both options have merits."), "turn note: hedge steer")
    for i in range(3):
        iv.turns += [Turn("Candidate", "Thinking aloud about it.", i, {}), Turn("Interviewer", "Okay, go on.", i, {})]
    check("haven't probed" in iv._turn_note("So I think it's the payment step."), "turn note: probe steer after 3 acks")
    iv.turns.append(Turn("Interviewer", "Why the payment step over sessions?", 9, {}))
    check("haven't probed" not in iv._turn_note("Because that conversion fell most."), "turn note: no steer after a probe")
    cases = load_all()
    for k in ("rca", "product_design", "product_strategy", "guesstimate", "metrics", "situational"):
        c = next(x for x in cases.values() if x.type_key == k)
        b = AgentInterview(c, "pm", "deepseek-v4.1-flash")._brief()
        check(TYPE_RULES[k].strip()[:40] in b and "Probing: half the job" in b and "{" not in b[:200],
              f"brief: {k} rules and probe section present")

    print(f"\n{'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILED'}")


if __name__ == "__main__":
    main()
