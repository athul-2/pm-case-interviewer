"""Voice I/O for the interview: push-to-talk speech to text, and spoken interviewer lines.

  Speech to text: mlx-whisper on Apple Silicon (default mlx-community/whisper-large-v3-turbo; set STT_MODEL
                  to change, e.g. mlx-community/whisper-small-mlx to save memory). Elsewhere faster-whisper
                  (default "small"; STT_MODEL=large-v3-turbo on a GPU, base.en on a slow CPU). The case's own vocabulary
                  (company, products, lakh/crore) is passed as a prompt so names come out right.
  Text to speech: Kokoro-82M through kokoro-onnx (model files in ~/.cache/case_interviewer), falling back to
                  macOS `say` or espeak, else text only. Set TTS_VOICE (default af_heart), TTS_SPEED (default 1.0), TTS=say to force say.

Lines are spoken sentence by sentence as the engine streams them; the next sentence is synthesised while
the current one plays. Starting to record stops playback (barge-in), so the mic never hears the speaker.
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

SR = 16000
MODELS = Path.home() / ".cache" / "case_interviewer"
STT_MODEL = os.environ.get("STT_MODEL", "")
SAY_CMD = shutil.which("say") or shutil.which("espeak-ng") or shutil.which("espeak")
VOICE = os.environ.get("TTS_VOICE", "af_heart")
SPEED = float(os.environ.get("TTS_SPEED", "1.0"))

# Whisper's usual inventions on near-silence.
HALLUCINATIONS = {"thank you", "thanks for watching", "thank you for watching", "you", "bye", "okay", "so"}


# ---------- speech to text ----------

class Recorder:
    """Push-to-talk: start() opens the mic, stop() returns 16 kHz mono float32 audio."""

    def __init__(self):
        import sounddevice as sd
        self._sd = sd
        self._chunks: list[np.ndarray] = []
        self._stream = None

    @property
    def active(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        self.stop()                            # a second start (double click) must not leak a live mic stream
        chunks: list[np.ndarray] = []          # per-recording list, so a stale callback can't write into it
        self._chunks = chunks
        self._stream = self._sd.InputStream(samplerate=SR, channels=1, dtype="float32",
                                            callback=lambda data, *_: chunks.append(data.copy()))
        self._stream.start()

    def stop(self) -> np.ndarray:
        stream, self._stream = self._stream, None
        if stream:
            stream.stop()
            stream.close()
        chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, np.float32)


def case_prompt(case) -> str:
    """Vocabulary for Whisper's initial prompt: proper nouns from the case plus Indian number words."""
    text = " ".join([case.title, case.company, case.prompt] + [e.title for e in case.exhibits])
    lower = set(re.findall(r"\b[a-z]+\b", text))
    names = [n for n in dict.fromkeys(re.findall(r"\b[A-Z][A-Za-z0-9]+(?:\s[A-Z][A-Za-z0-9]+)*", text))
             if not (" " not in n and n.lower() in lower) and n not in {"I", "How", "What", "Why", "The", "A"}]
    return ("PM case interview. " + ", ".join(names[:40]) + ". Numbers like 50 lakh, 2 crore, Rs 99, 20%.")[:600]


class Transcriber:
    def __init__(self, prompt: str = ""):
        self.prompt = prompt
        self._mw = self._fw = None
        try:                                    # Apple Silicon
            import mlx_whisper
            self._mw = mlx_whisper
            self.model = STT_MODEL or "mlx-community/whisper-large-v3-turbo"
        except ImportError:                     # everything else (CPU or CUDA)
            from faster_whisper import WhisperModel
            self.model = STT_MODEL or "small"
            self._fw = WhisperModel(self.model, device="auto", compute_type="auto")

    def warm_up(self) -> None:
        """Load the model in the background so the first answer isn't slow."""
        threading.Thread(target=lambda: self(np.zeros(SR, np.float32), force=True), daemon=True).start()

    def __call__(self, audio: np.ndarray, force: bool = False) -> str:
        secs = len(audio) / SR
        if not force and (secs < 0.5 or float(np.sqrt(np.mean(audio ** 2))) < 0.003):
            return ""
        if self._mw:
            out = self._mw.transcribe(audio, path_or_hf_repo=self.model, language="en", temperature=0.0,
                                      condition_on_previous_text=False, initial_prompt=self.prompt or None,
                                      verbose=None)
            raw = out.get("text", "")
        else:
            segs, _ = self._fw.transcribe(audio, language="en", temperature=0.0, condition_on_previous_text=False,
                                          initial_prompt=self.prompt or None, vad_filter=True)
            raw = " ".join(seg.text for seg in segs)
        text = re.sub(r"\s+", " ", raw).strip()
        if force:
            return text
        if re.sub(r"[^a-z ]", "", text.lower()).strip() in HALLUCINATIONS and secs < 3:
            return ""
        return text

    def unload(self) -> None:
        """Free Whisper's memory before grading loads the bigger model."""
        try:
            from mlx_whisper.transcribe import ModelHolder
            import mlx.core as mx
            ModelHolder.model = None
            ModelHolder.model_path = None
            mx.clear_cache()
        except Exception:
            pass


# ---------- text to speech ----------

def speakable(text: str) -> str:
    """Make written case text sound right when spoken."""
    t = re.sub(r"[*_`#>|]", " ", text)
    t = re.sub(r"\bRs\.?\s?(?=\d)", "rupees ", t)
    t = re.sub(r"₹\s?(?=\d)", "rupees ", t)
    t = re.sub(r"(\d)\s?%", r"\1 percent", t)
    t = re.sub(r"(\w)\s?[x×]\s?(?=\d)", r"\1 times ", t)
    t = re.sub(r"\s=\s", " equals ", t)
    t = re.sub(r"\bw/w\b|\bWoW\b", "week over week", t)
    t = re.sub(r"\bE(\d+)\b", r"exhibit \1", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


SENT_END = re.compile(r"(?<=[.?!])\s+(?=[A-Z0-9\"'(])")


class Speaker:
    """feed() streamed text, flush() at the end of a line; stop() cuts playback; wait() blocks until done."""

    def __init__(self):
        self._buf = ""
        self._texts: queue.Queue = queue.Queue()
        self._audio: queue.Queue = queue.Queue()
        self._gen = 0                 # bumped by stop(); stale work is dropped
        self._busy = 0
        self._lock = threading.Lock()
        self._kokoro = None
        self._sd = None
        self._say_proc = None
        if os.environ.get("TTS", "").lower() != "say":
            try:
                from kokoro_onnx import Kokoro
                import sounddevice as sd
                self._kokoro = Kokoro(str(MODELS / "kokoro-v1.0.onnx"), str(MODELS / "voices-v1.0.bin"))
                self._sd = sd
            except Exception as e:     # missing model files or package: fall back to the system voice
                print(f"(Kokoro unavailable, using {SAY_CMD or 'text only'}: {e})")
        self.backend = "kokoro" if self._kokoro else ("say" if SAY_CMD else "none")
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    # public
    def feed(self, chunk: str) -> None:
        self._buf += chunk
        parts = SENT_END.split(self._buf)
        for s in parts[:-1]:
            self._enqueue(s)
        self._buf = parts[-1]

    def flush(self) -> None:
        if self._buf.strip():
            self._enqueue(self._buf)
        self._buf = ""

    def say(self, text: str) -> None:
        self.feed(text)
        self.flush()

    def stop(self) -> None:
        with self._lock:
            self._gen += 1
            self._busy = 0
        self._buf = ""
        for q in (self._texts, self._audio):
            while not q.empty():
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        if self._sd:
            self._sd.stop()
        if self._say_proc and self._say_proc.poll() is None:
            self._say_proc.terminate()

    def wait(self) -> None:
        while self._busy > 0:
            time.sleep(0.05)

    @property
    def speaking(self) -> bool:
        return self._busy > 0

    # internals
    def _enqueue(self, text: str) -> None:
        text = speakable(text)
        if not re.search(r"[A-Za-z0-9]", text):
            return
        with self._lock:
            self._busy += 1
            self._texts.put((self._gen, text))

    def _done_one(self, gen: int) -> None:
        with self._lock:
            if gen == self._gen and self._busy > 0:
                self._busy -= 1

    def _synth_loop(self) -> None:
        while True:
            gen, text = self._texts.get()
            if gen != self._gen:
                continue
            if self._kokoro:
                try:
                    samples, sr = self._kokoro.create(text, voice=VOICE, speed=SPEED, lang="en-us")
                except Exception:
                    samples, sr = None, 0
                self._audio.put((gen, text, samples, sr))
            else:
                self._audio.put((gen, text, None, 0))

    def _play_loop(self) -> None:
        while True:
            gen, text, samples, sr = self._audio.get()
            if gen == self._gen:
                if samples is not None and len(samples):
                    self._sd.play(samples, sr)
                    self._sd.wait()
                elif SAY_CMD:
                    self._say_proc = subprocess.Popen([SAY_CMD, text])
                    self._say_proc.wait()
            self._done_one(gen)
