# Decision log

The main design decisions behind the app, newest first: what was decided, why, and what was tried or rejected. Most came from watching a real practice session go wrong and fixing the cause.

| Date | Decision | Why | Tried or rejected |
| --- | --- | --- | --- |
| 2026-11-22 | The API interviewer stops volunteering facts: bundled casebook answers are cut to the sub-question asked, conclusions are dropped, hints and table replies can't carry new numbers, and a vague "give me that data" gets "Which cut?" | In an RCA session it read out "no app release" and "no UPI outage" unasked, said where the drop was, and put up the key table on a vague request | A rule against inferred facts was rejected: a plausible fact in the expected direction is fine |
| 2026-11-22 | Twist text is stripped from case prompts | One case read its follow-up twist aloud in the opening | |
| 2026-11-22 | Product design: users, segments and pain points belong to the candidate. Prepared answers about them are confirmed only after the candidate guesses, in both interviewers | Asked about demographics, it handed over the whole segment list | Confirming by restating the list was rejected: unnamed segments slipped out |
| 2026-11-22 | The interviewer never advertises what else is in its notes | It offered "I've got which order types complain most, if that's useful" | |
| 2026-11-21 | DeepSeek V4.1 Flash through OpenRouter is offered as a grader | 42-62 s per report against 5-9 min locally, under a cent each, and strong and weak answers stay apart | It's stricter than local Qwen in the middle bands, so band edges need rechecking after human calibration |
| 2026-11-21 | Pacing and help rework: one layer at a time, never compute or interpret, a per-turn note with the clock and the next help level, and a review pass that trims over-sharing | A session over-shared, did the candidate's arithmetic and logged no hints | Letting the model log help freely was rejected: it logged 8 false hints |
| 2026-11-17 | Answer in words; tables go up only when the candidate asks to see data | The first API session put tables up instead of answering, and narrated them | |
| 2026-11-17 | DeepSeek, Qwen, MiniMax and OpenRouter share one OpenAI-compatible connector, with no SDK | Cheap, capable models through one code path | |
| 2026-11-17 | The interviewer model is selectable, including Claude and Gemini, under a hard monthly cap checked before every call | Even the best local model couldn't follow a real conversation and leaked a root cause | Uncapped API use was rejected |
| 2026-11-16 | The local interviewer moves to qwen3:8b with thinking off, and a yes/no check per candidate item | qwen2.5:7b opened the wrong tables. qwen3 with yes/no checks scored precision 1.00 and recall 0.82 on a labelled set | Multi-select with qwen3: better recall but 8-19 s a turn |
| 2026-11-16 | One model-written reply per turn | Tried for a more natural voice | Dropped: it coached, asked questions back, and took up to 22 s |
| 2026-11-16 | Local replies are grounded in casebook sentences: the model picks the sentences that answer the question, and Python checks numbers and names | Whole prepared answers leaked unasked facts | |
| 2026-11-16 | Hints aren't streamed, so they can be checked first; a name the candidate hasn't said swaps in a fixed line | The local model volunteered a real-world cause as a hint | |
| 2026-11-16 | Sessions are tagged practice or simulated, and averages hide test runs | Early sessions were all scripted test runs | |
| 2026-11-16 | Web app in plain HTML, CSS and JS on FastAPI, fonts vendored, quiet editorial style | Works offline with no build step; colour only on data | |
| 2026-11-16 | Voice through Whisper and Kokoro | Both run locally | |
| 2026-11-15 | Grader: one pass per dimension, expected score from token probabilities, verbatim quote check, positively phrased gates, all arithmetic in Python | Evaluation research: score one criterion per pass, quote before scoring, keep arithmetic out of the model | One holistic score from the model was rejected |
| 2026-11-15 | The interviewer never sees the answer key: each case is split into an interviewer pack and a grader pack | Left free, the model leaked exhibits and the answer | |
| 2026-11-15 | Local-first through Ollama | Free, private and offline | |
| 2026-11-15 | The casebook is never modified by the app; added cases go to `custom_cases/` | The case files are the source of truth | |

## Still open

- Should good-but-imperfect answers reach Hire more often? The mid-level scripted candidates never do. To be decided after hand-scoring real sessions.
- Which grader agrees best with human scores? The target is QWK of 0.6 or more per dimension, against 30-50 hand-scored transcripts.
- Some twists assume the model answer and should be made conditional.
- Voice needs more real-microphone testing.
