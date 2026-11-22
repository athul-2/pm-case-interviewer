"""Text-mode interview loop (voice and UI come later on top of the same engine).

  python -m interviewer.cli            # list cases
  python -m interviewer.cli A1         # run case A1 (graded at the PM bar)
  python -m interviewer.cli A1 --level apm|pm|senior
  python -m interviewer.cli A1 --voice      # speak your answers (Enter to talk, Enter to stop)
  python -m interviewer.cli --grade sessions/<file>.json [--grader or:deepseek/deepseek-v4.1-flash]   # re-grade

Commands during the interview: /hint  /time  /exhibits  /end   (typed, also in voice mode)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import evaluator
from .casepack import load_all
from .engine import Interview

DIM, BOLD, CYAN, YEL, RED, RST = "\033[2m", "\033[1m", "\033[36m", "\033[33m", "\033[31m", "\033[0m"


def print_report(r: dict) -> None:
    c = r["coaching"]
    print(f"\n{BOLD}=== Feedback: {r['case_id']} ({r.get('level', 'pm').upper()} bar) ==={RST}")
    print(f"{BOLD}Verdict:{RST} {r['verdict']}  |  weighted {r['weighted_mean']}/4  |  "
          f"answer vs casebook {r['answer_quality']}/4")
    print(c.get("one_line_summary", ""))
    print(f"{DIM}{'; '.join(r['rollup']['reasons'])}{RST}")
    print(f"{DIM}{json.dumps(r['stats'])}  graded in {r['timings']['total']}s{RST}\n")
    print(f"{BOLD}Dimensions{RST}{' (trade-off weights)' if r.get('tradeoff_profile') else ''}")
    for d in r["dimensions"]:
        print(f"  {d['score']:.1f}/4  {d['name'][:44]:44} w{d['weight']:<4g} {d['reason']}")
        for q in d["quotes"][:1]:
            print(f"        {DIM}\"{q[:150]}\"{RST}")
        for cap in d["caps"]:
            print(f"        {YEL}{cap}{RST}")
    fired = [g for g in r["gates"] if g["triggered"]]
    if fired:
        print(f"\n{BOLD}Gates triggered{RST}")
        for g in fired:
            print(f"  {YEL}! {g['id']}{RST} (max {g['cap']}): {g['reason']}")
    disputed = [g for g in r["gates"] if g.get("disputed")]
    if disputed:
        print(f"\n{DIM}Disputed gates (not applied: the dimension pass found quoted evidence against them){RST}")
        for g in disputed:
            print(f"  {DIM}? {g['id']}: {g['reason']}{RST}")
    bad = [a for a in r.get("arithmetic", []) if a["error"]]
    if bad:
        print(f"\n{BOLD}Arithmetic{RST}")
        for a in bad:
            print(f"  {a['text']}  -> off by {a['ratio']}x{' (corrected)' if a['corrected'] else ''}")
    ct = r["content"]
    print(f"\n{BOLD}Against the casebook answer{RST}  ({ct.get('answer_comment','')})")
    for p in ct.get("key_points_hit", []):
        print(f"  + {p}")
    for p in ct.get("key_points_missed", []):
        print(f"  - {p}")
    if ct.get("pitfalls_fallen_into"):
        print(f"  Pitfalls: {'; '.join(ct['pitfalls_fallen_into'])}")
    if ct.get("twist_score"):
        print(f"  Twist: {ct['twist_score']}/4 - {ct.get('twist_comment','')}")
    if c.get("strengths"):
        print(f"\n{BOLD}Strengths{RST}")
        for s in c["strengths"]:
            print(f"  * {s}")
    print(f"\n{BOLD}Fix these{RST}")
    for i, f in enumerate(c.get("fixes", []), 1):
        print(f"  {i}. {f['issue']} - {f['why_it_matters']}\n     {CYAN}Say: \"{f['say_this_instead']}\"{RST}")
    print(f"\n{BOLD}Practise next:{RST} {c.get('practice_next')}\n")


def grade_file(path: Path, level: str | None = None, grader: str | None = None) -> dict:
    session = json.loads(path.read_text())
    case = load_all()[session["case_id"]]
    report = evaluator.evaluate(case, session, progress=lambda m: print(f"{DIM}{m}{RST}"), level=level, grader=grader)
    session["report"] = report
    path.write_text(json.dumps(session, indent=1))
    print_report(report)
    return report


class Voice:
    """Push-to-talk input and spoken interviewer lines (see voice.py)."""

    def __init__(self, case):
        from . import voice
        print(f"{DIM}Loading speech models...{RST}")
        self.rec = voice.Recorder()
        self.stt = voice.Transcriber(voice.case_prompt(case))
        self.stt.warm_up()
        self.tts = voice.Speaker()
        print(f"{DIM}Voice: {voice.STT_MODEL.split('/')[-1]} + {self.tts.backend}. "
              f"Press Enter to talk, Enter again to stop. Typing still works.{RST}")

    def listen(self) -> str:
        typed = input(f"\n{CYAN}You{RST} {DIM}[Enter = talk]{RST} ").strip()
        self.tts.stop()                      # barge-in: talking or typing cuts the interviewer off
        if typed:
            return typed
        self.rec.start()
        input(f"{RED}● Recording... Enter to stop{RST} ")
        audio = self.rec.stop()
        print(f"{DIM}transcribing {len(audio) / 16000:.0f}s...{RST}", end="\r", flush=True)
        text = self.stt(audio)
        print(f"{CYAN}You:{RST} {text or DIM + '(nothing heard, try again)' + RST}" + " " * 10)
        return text


def run(case_id: str, level: str = "pm", use_voice: bool = False) -> None:
    cases = load_all()
    case = cases[case_id.upper()]
    vo = Voice(case) if use_voice else None
    iv = Interview(case, level)
    print(f"{BOLD}{case.id} {case.title}{RST} - {case.case_type}, {case.difficulty.split('.')[0]}, "
          f"~{case.time_box_minutes} min\n{DIM}/hint /time /exhibits /end{RST}\n")
    opening = iv.open().text
    print(f"{YEL}Interviewer:{RST} {opening}")
    if vo:
        vo.tts.say(opening)
    while not iv.done:
        try:
            said = vo.listen() if vo else input(f"\n{CYAN}You:{RST} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not said:
            continue
        if said == "/end":
            break
        if said == "/time":
            print(f"{DIM}{iv.elapsed_min:.1f} of {case.time_box_minutes} min{RST}")
            continue
        if said == "/exhibits":
            for e in case.exhibits:
                if e.id in iv.revealed_e:
                    print(f"{DIM}{e.body}{RST}\n")
            continue
        events = iv.hint() if said == "/hint" else iv.respond(said)
        started = False
        for ev in events:
            if ev.kind == "exhibit":
                print(f"\n{DIM}┌─ {ev.exhibit_id} ─────────────\n{ev.text}\n└──────────────{RST}")
            elif ev.kind == "say":
                if not started:
                    print(f"\n{YEL}Interviewer:{RST} ", end="")
                    started = True
                print(ev.text, end="", flush=True)
                if vo:
                    vo.tts.feed(ev.text)
        print()
        if vo:
            vo.tts.flush()
    path = iv.save()
    print(f"\n{DIM}Saved {path}{RST}")
    if vo:
        vo.tts.wait()
        vo.stt.unload()                      # make room for the grader
    if len([t for t in iv.turns if t.speaker == "Candidate"]) >= 2:
        report = grade_file(path)
        if vo and report:
            vo.tts.say(f"Verdict: {report['verdict']}. {report['coaching'].get('one_line_summary', '')}")
            vo.tts.wait()


def main(argv: list[str]) -> None:
    level = None
    use_voice = "--voice" in argv
    argv = [a for a in argv if a != "--voice"]
    if "--level" in argv:
        i = argv.index("--level")
        level = argv[i + 1].lower()
        argv = argv[:i] + argv[i + 2:]
    grader = None
    if "--grader" in argv:
        i = argv.index("--grader")
        grader = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if len(argv) >= 2 and argv[0] == "--grade":
        grade_file(Path(argv[1]), level, grader)
    elif argv:
        run(argv[0], level or "pm", use_voice)
    else:
        for c in load_all().values():
            print(f"{c.id:4} {c.case_type:16} {c.time_box_minutes:>2}m  {c.title}  ({c.company})")


if __name__ == "__main__":
    main(sys.argv[1:])
