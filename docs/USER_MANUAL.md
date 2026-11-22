# User manual

This guide takes you from a fresh machine to your first graded case. It assumes nothing about your computer: the app was built and tested on an Apple M4 laptop with 16 GB of RAM, and the sections below say what to change if yours is different.

- [What you need](#what-you-need)
- [Install](#install)
- [Choosing models for your machine](#choosing-models-for-your-machine)
- [Voice (optional)](#voice-optional)
- [API interviewers and the budget (optional)](#api-interviewers-and-the-budget-optional)
- [Running an interview](#running-an-interview)
- [Reading your report](#reading-your-report)
- [Tracking progress](#tracking-progress)
- [Adding your own cases](#adding-your-own-cases)
- [Command line](#command-line)
- [Settings reference](#settings-reference)
- [Troubleshooting](#troubleshooting)

## What you need

| | Minimum | Recommended |
| --- | --- | --- |
| Operating system | macOS, Windows 10/11 or Linux | Any of these |
| Memory | 8 GB (with a small model, or an API interviewer) | 16 GB or more |
| Disk | About 4 GB for the models | About 8 GB with voice |
| Python | 3.10 | 3.11-3.13 |
| Ollama | A recent release | Tested on 0.40 |
| Browser | Any modern browser | |

Everything runs on your machine. Only the optional API interviewers send anything over the network.

## Install

**1. Install Ollama** from [ollama.com/download](https://ollama.com/download) and start it. On macOS and Windows it runs in the menu bar or system tray. On Linux run `ollama serve`.

**2. Get the code and the Python packages.**

```bash
git clone https://github.com/athul-2/pm-case-interviewer.git
cd pm-case-interviewer
python3 -m pip install -r requirements.txt
```

On Windows, use `py` instead of `python3` if `python3` isn't found. Using a virtual environment (`python3 -m venv .venv`, then activate it) keeps these packages separate from the rest of your system.

**3. Download the models.** The defaults suit a 16 GB machine. On a smaller machine, see the next section first.

```bash
ollama pull qwen3:8b
ollama pull nomic-embed-text
```

**4. Start the app.**

```bash
python3 -m interviewer.server
```

Open http://127.0.0.1:8770. The app runs until you stop the server with Ctrl+C. Closing the browser tab doesn't stop it.

**macOS shortcut (optional):** `bash launcher/build_app.sh` puts a **Case Interviewer** app on your Desktop that starts Ollama and the server, then opens the page.

## Choosing models for your machine

The app uses three local models: one interviews you, one grades you, and a small embedding model matches your questions to the case data. By default the interviewer and grader are the same model, so only one is loaded at a time.

| Your machine | Interviewer and grader | Expect |
| --- | --- | --- |
| 8 GB RAM | `qwen3:4b`, or an API interviewer with the local grader | Works, but the interviewer matches questions less reliably. Close other apps |
| 16 GB RAM, Apple Silicon or a recent laptop | `qwen3:8b` (default) | 1-6 s per reply, 5-9 min per report |
| 32 GB+ RAM, or a GPU with 12 GB+ | `qwen3:14b` | Faster and somewhat sharper |
| Any machine, no local models | An API interviewer plus the DeepSeek grader | About a cent per case; needs internet |

Only `qwen3:8b` and `qwen2.5:7b` have been tested end to end. Other sizes of Qwen3 should behave similarly but haven't been checked, so treat their grades with more caution. Stick to the Qwen3 family if you can: the app asks the model not to "think", and other model families may handle that setting differently.

To use another model, pull it and set it when you start the server:

```bash
ollama pull qwen3:4b
INTERVIEW_MODEL=qwen3:4b GRADER_MODEL=qwen3:4b python3 -m interviewer.server
```

On Windows PowerShell:

```powershell
$env:INTERVIEW_MODEL="qwen3:4b"; $env:GRADER_MODEL="qwen3:4b"; py -m interviewer.server
```

The model you name appears on the **Models** screen as a local option.

**Memory tips**

- Interviewing and grading never run at the same time. Grading waits until no interview is live, then frees the interviewer's memory.
- A browser with many tabs can push a 16 GB machine into swap, which roughly halves local speed. Close what you don't need before practising.
- If Ollama runs on another machine, point the app at it: `OLLAMA_HOST=http://that-machine:11434`.

## Voice (optional)

Voice lets you speak your answers and hear the interviewer. It uses the microphone and speakers of the computer running the server, not the browser's.

```bash
python3 -m pip install -r requirements-voice.txt
```

That installs the right speech-to-text engine for your machine:

| Your machine | Speech to text | Default model | Change it with |
| --- | --- | --- | --- |
| Apple Silicon Mac | mlx-whisper | `whisper-large-v3-turbo` (about 1.6 GB, downloaded on first use) | `STT_MODEL=mlx-community/whisper-small-mlx` to save memory |
| Everything else | faster-whisper | `small` | `STT_MODEL=large-v3-turbo` on a GPU, `base.en` on a slow CPU |

**Speech out** uses Kokoro. Download `kokoro-v1.0.onnx` and `voices-v1.0.bin` from the [kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases) (`model-files-v1.0`) and put them in `~/.cache/case_interviewer/`. On Windows that's `C:\Users\<you>\.cache\case_interviewer\`. Without these files the app falls back to the system voice (`say` on macOS, `espeak` on Linux if installed), and otherwise shows text only.

- `TTS_VOICE` changes the voice (default `af_heart`; others include `bf_emma` and `am_michael`). `TTS_SPEED` changes the speed.
- The first time you talk, your operating system asks for microphone access for your terminal or Python. Allow it.
- Starting to talk cuts the interviewer off, so its voice never ends up in your recording.

## API interviewers and the budget (optional)

The local interviewer is free but follows conversation less fluidly. An API model sounds more like a person. Open **Models** to choose one.

| Option | Approximate cost per interview | Key |
| --- | --- | --- |
| Local Qwen (default) | Free | None |
| DeepSeek V4.1 Flash, Qwen3.8 Flash, MiniMax M3.1 Flash | $0.02-0.03 | `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `MINIMAX_API_KEY` |
| Gemini 2.5 / 3.5 Flash-Lite | $0.01-0.04 | `GEMINI_API_KEY` |
| Claude Haiku / Sonnet / Opus 5.5 | $0.02 / $0.20 / $0.40 | `ANTHROPIC_API_KEY` |
| Seven of the above through OpenRouter | Same as the provider | `OPENROUTER_API_KEY` |

The costs are estimates; check them against your own use. DeepSeek through OpenRouter is the most tested API path.

- **Keys:** paste them under **API keys**, or set the environment variables above. Pasted keys are stored only on your computer, in `~/.config/case_interviewer/keys.json`, readable by your user alone, and sent only to their own provider.
- **Budget:** set **Monthly budget** and click **Save cap** (default $10). Before every API call the app checks this month's spend plus a worst-case cost for the call. If the call could cross the cap, the interview stops with a line saying so and is still graded locally. A case won't start without room for 1.5 times a typical interview.
- **Backstop:** also set a spending limit in each provider's console.
- **Grader:** the grader stays local by default. **DeepSeek V4.1 Flash (OpenRouter)** is offered as a faster grader: about a minute per report, a few cents each. It is stricter than the local grader on middling answers.
- Claude needs `pip install anthropic`, and Gemini `pip install google-genai`. Both are in `requirements.txt`. The others need nothing extra.

## Running an interview

1. **Set your level.** **Hire bar** in the sidebar: APM, PM or Senior PM. It moves the verdict cut-offs by 0.25 either way.
2. **Pick a case.** Open **Cases**. Filter by type, search, or show only cases you haven't tried. Click a case to see its prompt and your past sessions, then **Practise**.
3. **Or build a practice set.** Tick several cases, optionally turn on **Shuffle** and voice, and start. **Continue set** picks up where you stopped; **Discard set** clears it.
4. **Listen to the prompt, then restate it.** Ask "am I right?" and the interviewer confirms, or corrects the detail you got wrong.
5. **Talk.** Type and press **Send**. With voice on, click **Talk**, speak, then **Stop and send**.
6. **Ask clarifying questions as you would in a real interview.** You get the smallest true answer to exactly what you asked. To go deeper, ask the next question.
7. **Ask for data by name.** For example, "Can I see the add-to-cart rate by pincode tier?" The table appears beside the chat. A vague "show me the data" gets "Which cut?"
8. **Stuck?** Click **Ask for a hint**, or say "I'm stuck". Help goes nudge, then redirect, then rescue, and each one lowers your grade.
9. **Recommend.** When you give your final answer, the interviewer puts a follow-up twist to you, takes your response, and closes.
10. **End.** **End and grade** queues the report. **End without grading** keeps the transcript only.

**What the interviewer won't do:** praise you, tell you whether you're on track, offer data you didn't ask for, do your arithmetic, or say what a number means. In product design cases it asks for your own guess before confirming who the users are or what their pain points are. That's all deliberate: it's what a good interviewer does.

**The timer** counts against the case's time box (usually 30-45 minutes). Near the end, the interviewer asks you to move towards a recommendation.

## Reading your report

Grading runs in the background once no interview is live. The top bar shows the queue. It takes about 5-9 minutes per report with the local grader on a 16 GB laptop, faster on bigger machines, and about a minute with the DeepSeek grader.

| Part of the report | What it tells you |
| --- | --- |
| Verdict on the hire bar | Strong hire, Hire, Lean hire, Lean no hire or No hire, at your level |
| What capped it | Gates you hit (for example, diving into data before structuring) and hint penalties |
| Fix these first | Three fixes from your weakest dimensions, each with a "say this instead" line |
| Against the casebook answer | How close your diagnosis or recommendation came to the model answer |
| Dimensions | A 1-4 score per rubric dimension, with the quotes from your own turns it rests on |
| Transcript and exhibits | The whole conversation and every table you unlocked |

**Verdict cut-offs at the PM bar:**

| Verdict | Weighted score |
| --- | --- |
| Strong hire | 3.5 or more, and no dimension below 3 |
| Hire | 3.0 or more |
| Lean hire | 2.5 or more |
| Lean no hire | 2.0 or more |
| No hire | Below 2.0 |

APM is 0.25 lower and senior PM 0.25 higher.

**What hints cost:**

- A nudge takes 0.25 off that dimension.
- A redirect caps that dimension at 2, and at PM and senior PM caps the verdict at Lean hire. At APM the first redirect is free.
- A rescue caps the dimension at 2 and the verdict at Lean hire.

**Gates** are must-haves for each case type: for example, checking data integrity in a root cause case. Missing two caps the verdict at Lean no hire, and three or more means No hire. A gate isn't applied if the dimension it caps scored 3 or more on verified quotes.

From a report you can **Re-grade** it at another level or **Delete session**.

**How far to trust the grade:** the grader reliably separates strong, middling and weak answers. It has not been calibrated against human interviewers, so use it to compare your own sessions over time, not as a prediction of a real interview.

## Tracking progress

- **Home** shows each case type's average as a dot on the hire-bar ladder, your latest session, what to practise next, and your weakest dimensions.
- **History** lists every report.
- Your sessions are saved as JSON files in `sessions/`. They're yours: back them up, or delete them to start fresh.

## Adding your own cases

Open **Add a case** and fill in:

- the prompt;
- prepared answers to likely clarifying questions (**Add a question**);
- exhibits, the data tables you'd hand over (**Add an exhibit**);
- follow-up twists (**Add a twist**);
- the model answer, common pitfalls and guardrail metrics. Only the grader sees these.

New cases are saved to `custom_cases/` with the next free ID for their type (for example A11). The bundled casebook is never changed. You can delete cases you added.

## Command line

The whole app also runs in a terminal.

| Command | What it does |
| --- | --- |
| `python3 -m interviewer.cli` | Lists the cases |
| `python3 -m interviewer.cli A1` | Runs case A1, graded at the end |
| `python3 -m interviewer.cli A1 --level senior --voice` | At senior PM level, by voice: Enter to talk, Enter to stop |
| `/hint`, `/time`, `/exhibits`, `/end` | Commands during a case |
| `python3 -m interviewer.cli --grade sessions/<file>.json` | Re-grades a saved session |
| `python3 -m interviewer.rescore sessions/*.json` | Recomputes verdicts without the model, after changing weights |
| `python3 -m interviewer.simulate A1 [--weak]` | Runs a scripted candidate, for testing; marked as a test run |
| `python3 -m interviewer.test_agent` | Offline checks of the API interviewer and budget; no network, no cost |

## Settings reference

All of these are optional environment variables.

| Variable | Default | What it changes |
| --- | --- | --- |
| `INTERVIEW_MODEL` | `qwen3:8b` | Local interviewer model |
| `GRADER_MODEL` | `qwen3:8b` | Local grader model |
| `EMBED_MODEL` | `nomic-embed-text` | Question-matching model |
| `OLLAMA_HOST` | `http://localhost:11434` | Where Ollama runs |
| `GRADER_THINK` | off | `1` lets the grader reason first: possibly better, about 3x slower |
| `STT_MODEL` | see [Voice](#voice-optional) | Speech-to-text model |
| `TTS_VOICE`, `TTS_SPEED` | `af_heart`, `1.0` | Speaking voice and speed |
| `TTS=say` | off | Use the system voice instead of Kokoro |
| `PORT` | `8770` | Web app port |
| `CASEBOOK_DIR` | `casebook/results` | A different folder of case files |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, `MINIMAX_API_KEY`, `OPENROUTER_API_KEY` | none | API keys, taking priority over keys saved in the app |

## Troubleshooting

| Problem | Fix |
| --- | --- |
| "Ollama isn't running" | Start the Ollama app, or run `ollama serve` |
| A model is reported missing | Run `ollama pull <name>` for the model the app names |
| Replies take 10 s or more | Close memory-heavy apps; use a smaller model (see [Choosing models](#choosing-models-for-your-machine)). The first reply after a model loads is always slower |
| Grading takes very long | Normal on CPU-only machines. Use the DeepSeek grader, or a smaller `GRADER_MODEL` |
| Grading never starts | It waits while an interview is live. An interview idle for 15 minutes stops blocking it |
| A grading job vanished after a restart | The queue is kept in memory. Open the report and click **Re-grade** |
| "Setup problem: … rejected the API key" | Paste the key again on **Models**. OpenRouter keys start with `sk-or-` |
| "Out of credits", or a case won't start (402) | Top up the provider, or raise the cap on **Models** |
| No sound | Check the Kokoro files are in `~/.cache/case_interviewer/`, or install `espeak` on Linux |
| The mic picks up nothing | Allow microphone access for your terminal or Python in your system settings, then restart the server |
| Port 8770 is taken | `PORT=8780 python3 -m interviewer.server` |
| `python3` not found on Windows | Use `py` |
