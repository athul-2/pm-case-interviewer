#!/bin/bash
# Starts the Case Interviewer (Ollama + the web server) if it isn't running, then opens it.
# Run by "Case Interviewer.app" (macOS); also fine to run by hand on macOS or Linux.
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$(command -v python3)}"
URL="http://127.0.0.1:8770"
LOG="$DIR/launcher/server.log"

up() { curl -s -m 2 "$URL/api/health" >/dev/null; }
alert() { command -v osascript >/dev/null && osascript -e "display alert \"Case Interviewer\" message \"$1\" as critical" >/dev/null || echo "Case Interviewer: $1" >&2; }
opener() { command -v open >/dev/null && open "$1" || xdg-open "$1" >/dev/null 2>&1; }

if ! up; then
  # Ollama first: the server needs it for every turn.
  if ! curl -s -m 2 http://localhost:11434/api/tags >/dev/null; then
    open -a Ollama 2>/dev/null || (nohup ollama serve >/dev/null 2>&1 &)
    for _ in $(seq 1 30); do curl -s -m 2 http://localhost:11434/api/tags >/dev/null && break; sleep 1; done
  fi
  cd "$DIR" || { alert "Can't find $DIR"; exit 1; }
  echo "--- $(date) starting" >> "$LOG"
  nohup "$PY" -m interviewer.server >> "$LOG" 2>&1 &
  for _ in $(seq 1 60); do up && break; sleep 1; done
  if ! up; then
    alert "The server didn't start within a minute. See launcher/server.log."
    exit 1
  fi
fi
opener "$URL"
