"""
JARVIS background listener — "Hey JARVIS", no browser tab.

    .venv/bin/python listener.py

Listens on the mic for the wake word (openWakeWord's "hey_jarvis" model, which
runs entirely on this Mac — no audio leaves the machine until you've said it).
After the wake word it records until you stop talking, sends that clip to the
JARVIS server for speech-to-text, streams the answer back and speaks it sentence
by sentence as it arrives, so you hear the first sentence while the rest is
still being written. Then it keeps listening a few seconds for a follow-up, no
wake word needed.

Keyboard shortcut instead of the wake word:  ./talk.sh   (sends SIGUSR1)
Stop it talking mid-answer:                  ./talk.sh stop   (SIGUSR2)

Needs the server (python3 server.py) running on JARVIS_PORT.
"""
import io
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave

import numpy as np
import sounddevice as sd
from openwakeword.model import Model

ROOT = os.path.dirname(os.path.abspath(__file__))
for line in open(os.path.join(ROOT, ".env"), encoding="utf-8") if os.path.exists(os.path.join(ROOT, ".env")) else []:
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

PORT = int(os.environ.get("JARVIS_PORT", "8730"))
SERVER = f"http://127.0.0.1:{PORT}"
HOST = f"localhost:{PORT}"                       # the server only answers its own Host
WAKE_THRESHOLD = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5"))
FOLLOWUP_S = float(os.environ.get("JARVIS_FOLLOWUP", "5"))     # 0 = always need the wake word
END_SILENCE_S = float(os.environ.get("JARVIS_END_SILENCE", "1.6"))   # "Hey JARVIS": pause this long = done
BARGE_THRESHOLD = float(os.environ.get("JARVIS_BARGE_THRESHOLD", "0.5"))  # "JARVIS" while it talks = stop
FALLBACK_VOICE = os.environ.get("JARVIS_FALLBACK_VOICE", "Daniel")   # macOS voice if ElevenLabs is down
ASK_LISTEN_S = float(os.environ.get("JARVIS_ASK_LISTEN", "8"))   # after JARVIS asks you something
MANUAL_MAX_S = 90          # push-to-talk (double-⌘): keep listening until you tap ⌘, up to this long
MANUAL_QUIET_S = 12        # …or until you've been silent this long (forgot to tap)
FILLERS = ["One sec.", "Checking.", "On it."]     # said when it has to look something up
_filler_audio = []
_standby_audio = []                              # pre-voiced "On standby."
MIC = os.environ.get("JARVIS_MIC", "")           # part of a mic's name; blank = system default
NOTIFY = os.environ.get("JARVIS_NOTIFY", "0") == "1"   # the pop-up shows answers now

RATE = 16000
FRAME = 1280                                     # 80 ms, what openWakeWord wants
CHIME_ON = "/System/Library/Sounds/Tink.aiff"
CHIME_OFF = "/System/Library/Sounds/Pop.aiff"
CHIME_MISS = "/System/Library/Sounds/Bottle.aiff"

frames = queue.Queue()
hotkey = threading.Event()
cancel_listen = threading.Event()             # pop-up: you started typing instead
stop_speaking = threading.Event()
player = {"p": None}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# Live state for the pop-up's voice bars: tiny UDP packets to localhost only.
import socket as _socket
_UI = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
UI_ADDR = ("127.0.0.1", int(os.environ.get("JARVIS_UI_PORT", "8731")))


FOCUS_FLAG = "/tmp/jarvis-page-focus"           # written by server.py's /api/focus
PAGE_END_SILENCE_S = float(os.environ.get("JARVIS_PAGE_END_SILENCE", "2"))


def page_focused():
    """True while the JARVIS page/app is in front (it heartbeats every 10 s)."""
    try:
        if time.time() - os.path.getmtime(FOCUS_FLAG) > 30:
            return False
        with open(FOCUS_FLAG) as f:
            return f.read().strip() == "1"
    except OSError:
        return False


def ui(state, level=None, text=None):
    """state: listening | level | heard | thinking | speaking | idle"""
    try:
        msg = {"s": state}
        if level is not None:
            msg["l"] = round(level, 3)
        if text is not None:
            msg["t"] = text[:500]
        _UI.sendto(json.dumps(msg).encode(), UI_ADDR)
    except OSError:
        pass


def chime(path):
    subprocess.Popen(["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(title, text):
    if not NOTIFY or not text:
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')[:220]
    subprocess.Popen(["osascript", "-e", f'display notification "{esc(text)}" with title "{esc(title)}"'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ─────────────────────────── server calls ───────────────────────────
def _req(path, data, ctype):
    return urllib.request.Request(SERVER + path, data=data, method="POST",
                                  headers={"Content-Type": ctype, "Host": HOST,
                                           "X-Jarvis-Source": "voice"})


def transcribe(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    with urllib.request.urlopen(_req("/api/listen", buf.getvalue(), "audio/wav"), timeout=45) as r:
        body = json.loads(r.read())
    if body.get("error"):
        raise RuntimeError(body["error"])
    return (body.get("text") or "").strip()


def tts(text):
    with urllib.request.urlopen(_req("/api/speak", json.dumps({"text": text}).encode(),
                                     "application/json"), timeout=30) as r:
        return r.read()


SENTENCE_END = re.compile(r"(.+?[.!?])(\s+|$)", re.S)


def ask_and_speak(text, oww=None):
    """Stream the answer; speak each sentence as soon as it's complete.
    While it talks, the mic still listens for "JARVIS": hearing it cuts the
    answer off. Returns (answer, preroll) — preroll is None unless you cut in,
    then it holds the audio since "JARVIS" so your next words aren't lost."""
    speakq = queue.Queue()                        # sentences waiting for a voice
    audioq = queue.Queue()                        # voiced sentences waiting to play
    stop_speaking.clear()

    def synth():                                  # voices sentence N+1 while N plays
        while True:
            item = speakq.get()
            if item is None or stop_speaking.is_set():
                audioq.put(None)
                return
            try:
                audioq.put(("mp3", tts(item), item))
            except Exception as e:                    # noqa: BLE001
                log(f"voice failed ({e}); using macOS voice")
                audioq.put(("say", item))

    def speaker():
        while True:
            item = audioq.get()
            if item is None or stop_speaking.is_set():
                return
            kind, data = item[0], item[1]
            ui("speaking")
            player["line"] = data if kind == "say" else item[2] if len(item) > 2 else ""
            if kind == "say":
                player["p"] = subprocess.Popen(["say", "-v", FALLBACK_VOICE, data])
                player["p"].wait()
                continue
            with tempfile.NamedTemporaryFile(suffix=".wav" if data[:4] == b"RIFF" else ".mp3",
                                             delete=False) as f:
                f.write(data)
            player["p"] = subprocess.Popen(["afplay", f.name])
            player["p"].wait()
            player["p"] = None
            os.unlink(f.name)

    threading.Thread(target=synth, daemon=True).start()
    th = threading.Thread(target=speaker, daemon=True)
    th.start()

    st = {"answer": "", "pending": "", "first": None, "filler": False}
    t0 = time.monotonic()
    spoken = f"[spoken — speech-to-text, may contain misheard words] {text}"
    req = _req("/api/run", json.dumps({"message": spoken}).encode(), "application/json")
    fetched = threading.Event()

    def fetch():
        # reads the whole answer even after you cut in, so the core finishes its turn cleanly
        answer, pending, first, said_filler = "", "", None, False
        try:
          with urllib.request.urlopen(req, timeout=300) as r:
            for raw in r:
                ev = json.loads(raw)
                if ev.get("t") == "delta":
                    first = first or time.monotonic() - t0
                    answer += ev["text"]; pending += ev["text"]
                    while True:
                        m = SENTENCE_END.match(pending)
                        if not m or len(m.group(1)) < 12 and m.group(2) == "":
                            break
                        speakq.put(m.group(1).strip())
                        pending = pending[m.end():]
                elif ev.get("t") == "tool":
                    if ev.get("phase") == "use":
                        log(f"  · {ev.get('name')}")
                        if not answer and not said_filler and _filler_audio:
                            # it's going to look something up: say so instantly
                            said_filler = True
                            audioq.put(("mp3", _filler_audio[int(time.time()) % len(_filler_audio)]))
                elif ev.get("t") == "error":
                    log(f"  ! {ev.get('message')}")
                    if not answer:
                        pending = "Something went wrong on my end."
        except Exception as e:                        # noqa: BLE001
            log(f"  ! server unreachable: {e}")
            pending = "I can't reach the JARVIS server. Is it running?"
        if pending.strip():
            speakq.put(pending.strip())
        speakq.put(None)
        st.update(answer=answer, pending=pending, first=first)
        fetched.set()
        log(f"JARVIS ({(first or 0):.1f}s): {answer.strip() or pending.strip()}")
        notify("JARVIS", answer.strip() or pending.strip())

    threading.Thread(target=fetch, daemon=True).start()

    # ── barge-in: listen for "JARVIS" while the answer plays ──
    import collections as _col
    pre = _col.deque(maxlen=PREROLL_FRAMES)
    drain()
    if oww is not None:
        oww.reset()
    while th.is_alive():
        try:
            f = frames.get(timeout=0.1)
        except queue.Empty:
            continue
        if oww is None:
            continue
        pre.append(f)
        score = oww.predict(f).get("hey_jarvis", 0)
        if score >= BARGE_THRESHOLD and "jarvis" not in (player.get("line") or "").lower():
            log(f"  (you said JARVIS — stopping, {score:.2f})")
            stop_speaking.set()
            if player["p"]:
                player["p"].terminate()
            th.join(timeout=2)
            oww.reset()
            return "", pre
    fetched.wait(timeout=5)
    return st["answer"].strip(), None


# ─────────────────────────── audio ───────────────────────────
def on_audio(indata, n, t, status):
    frames.put(indata[:, 0].copy())


def drain():
    while not frames.empty():
        try:
            frames.get_nowait()
        except queue.Empty:
            break


class Level:
    """Is someone talking? Uses the Silero voice-activity model (bundled with
    openWakeWord), which hears *voices*, not loudness — so a quiet pause in
    your sentence isn't "done", and a noisy room isn't "still talking"."""
    def __init__(self):
        self.floor = 200.0
        from openwakeword.vad import VAD
        self.vad = VAD()
        self.on = float(os.environ.get("JARVIS_VAD_ON", "0.5"))

    def rms(self, f):
        return float(np.sqrt(np.mean(f.astype(np.float32) ** 2)))

    def idle(self, f):                             # learn the room while idle
        self.floor = 0.97 * self.floor + 0.03 * min(self.rms(f), 4000)

    def speech(self, f):
        try:
            return float(self.vad.predict(f)) >= self.on
        except Exception:                          # noqa: BLE001  (fall back to loudness)
            return self.rms(f) > max(self.floor * 2.5, 350)


MIN_SPEECH_S = 0.8          # don't decide you're done until you've said at least this much
PREROLL_FRAMES = 19         # ~1.5 s kept from before the wake word fired


def record(level, wait_s, manual=False, preroll=None, end_silence=END_SILENCE_S):
    """Record one utterance. Returns int16 PCM, or None if nobody spoke.

    manual=True (opened with double-⌘): pauses don't end it — you do, with a
    single ⌘ tap or ✓ (both arrive as SIGUSR1). Otherwise a pause of
    end_silence seconds ends it."""
    got, started, silent, t0 = [], False, 0.0, time.monotonic()
    spoke = 0.0                                    # seconds of actual speech heard
    if preroll:                                    # "Hey JARVIS, when is…" in one breath:
        got, started = list(preroll), True         # keep what you said while it was waking
    while True:
        if cancel_listen.is_set():
            return None
        if hotkey.is_set():                       # pop-up ✓: you're done talking
            hotkey.clear()
            return np.concatenate(got) if got else None
        f = frames.get()
        loud = level.speech(f)
        db = 20 * np.log10(level.rms(f) + 1)
        ui("level", max(0.0, min(1.0, (db - 38) / 36)))     # ~quiet room → 0, loud voice → 1
        if started:
            got.append(f)
            silent = 0.0 if loud else silent + FRAME / RATE
            spoke += FRAME / RATE if loud else 0.0
            secs = len(got) * FRAME / RATE
            if manual:
                if silent >= MANUAL_QUIET_S or secs > MANUAL_MAX_S:
                    return np.concatenate(got)
            elif secs > 30:
                return np.concatenate(got)
            elif spoke >= MIN_SPEECH_S and silent >= end_silence:
                return np.concatenate(got)
            elif spoke < 0.25 and time.monotonic() - t0 > wait_s:
                return None                        # woke, but you never said anything
        elif loud:
            started = True
            got.append(f)
        elif time.monotonic() - t0 > (max(wait_s, 15) if manual else wait_s):
            return None


class MicHealth:
    """Notices a microphone that only ever delivers silence — what macOS does
    when an app hasn't been allowed to use the mic — and says so."""
    def __init__(self):
        self.t0 = time.monotonic()
        self.peak = 0
        self.warned = False
        self.last_report = time.monotonic()
        self.last_near = 0.0

    def check(self, f):
        self.peak = max(self.peak, int(np.abs(f).max()))
        now = time.monotonic()
        if not self.warned and now - self.t0 > 8 and self.peak == 0:
            self.warned = True
            log("! MICROPHONE IS SILENT — macOS is blocking it. Allow JARVIS (or Python) in "
                "System Settings → Privacy & Security → Microphone, then reopen JARVIS.")
            notify_always("JARVIS can't hear you",
                          "Allow the microphone for JARVIS in System Settings → Privacy & Security → Microphone.")
        if now - self.last_report > 600:            # every 10 min: proof of life
            log(f"  (mic ok · loudest sound in last 10 min: {self.peak})")
            self.peak, self.last_report = 0, now


health = MicHealth()


def notify_always(title, text):
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')[:220]
    subprocess.Popen(["osascript", "-e", f'display notification "{esc(text)}" with title "{esc(title)}"'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def asks_question(answer):
    """True if JARVIS's reply asks you something — a "?" in the last two sentences
    ("Want me to print it? Just say the word." counts)."""
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", answer.strip()) if x]
    return any(x.rstrip().endswith("?") for x in sentences[-2:])


STANDBY_INTENT = re.compile(
    r"\b((go(ing)?|get|be|head|time)\s+(on|to|in|into|for)\s+)?(stand\s?-?by|standby)\b"
    r"|\b(go(ing)?|head|time)\s+(to|for)\s+(bed|sleep)\b|\bgo\s+to\s+sleep\b"
    r"|\b(good\s?night|night night)\b", re.I)
DISMISS = re.compile(r"\W*(that'?s (all|it)( for now)?|never ?mind|nothing|stop|cancel|thanks?( you)?|"
                     r"i'?m good|all good|bye)\W*", re.I)


def is_standby(text):
    """"Go on standby", "can you go on standby now?", "go to bed now", "goodnight"…
    Short requests only, so a real question that mentions standby still gets answered."""
    words = re.findall(r"[A-Za-z']+", text)
    if DISMISS.fullmatch(text):
        return True
    return len(words) <= 8 and bool(STANDBY_INTENT.search(text)) and not re.search(
        r"\b(what|why|how|mean|means|when|who)\b", text, re.I)


def go_standby():
    """End the conversation: say "On standby." and flip the dashboard to STANDBY."""
    try:
        urllib.request.urlopen(_req("/api/standby", b"{}", "application/json"), timeout=3).read()
    except Exception:                             # noqa: BLE001
        pass
    if _standby_audio:
        with tempfile.NamedTemporaryFile(suffix=".wav" if _standby_audio[0][:4] == b"RIFF" else ".mp3",
                                         delete=False) as f:
            f.write(_standby_audio[0])
        subprocess.run(["afplay", f.name])
        os.unlink(f.name)
    else:
        subprocess.run(["say", "-v", FALLBACK_VOICE, "On standby."])


def pick_mic():
    if not MIC:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and MIC.lower() in d["name"].lower():
            return i
    log(f"no mic matching {MIC!r}; using the default")
    return None


def main():
    signal.signal(signal.SIGUSR1, lambda *_: hotkey.set())

    def _stop(*_):                               # SIGUSR2: stop talking / stop listening
        cancel_listen.set()
        stop_speaking.set()
        if player["p"]:
            player["p"].terminate()
    signal.signal(signal.SIGUSR2, _stop)

    oww = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    try:
        _standby_audio.append(tts("On standby."))
    except Exception:                             # noqa: BLE001
        pass
    for line in FILLERS:                          # pre-voice the fillers once, so they're instant
        try:
            _filler_audio.append(tts(line))
        except Exception:                         # noqa: BLE001  (server not up yet is fine)
            pass
    level = Level()
    import collections as _col
    preroll = _col.deque(maxlen=PREROLL_FRAMES)
    dev = pick_mic()
    name = sd.query_devices(dev if dev is not None else sd.default.device[0])["name"]
    with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                        device=dev, callback=on_audio):
        log(f'listening on "{name}" — say "Hey JARVIS", or press your shortcut')
        while True:
            try:
                f = frames.get(timeout=0.2)
            except queue.Empty:
                f = None
            woke = False
            manual = False
            if hotkey.is_set():
                hotkey.clear(); woke = True
                manual = True                         # double-⌘ / 🎙 / VOICE: you end it with ⌘
            elif f is not None:
                level.idle(f)
                preroll.append(f)
                health.check(f)
                score = oww.predict(f).get("hey_jarvis", 0)
                if score >= WAKE_THRESHOLD:
                    woke = True
                    log(f"wake word heard ({score:.2f})")
                elif score >= 0.25 and time.monotonic() - health.last_near > 2:
                    health.last_near = time.monotonic()
                    log(f"  (almost woke: {score:.2f} — needs {WAKE_THRESHOLD})")
            if not woke:
                continue

            wait = 6.0
            asked = False
            first = True
            while True:                               # conversation: wake, then follow-ups
                chime(CHIME_ON)
                cancel_listen.clear()
                ui("listening")
                hands_free = page_focused()           # on the JARVIS page: a 2 s pause = done, no ⌘ tap
                pcm = record(level, wait, manual=manual and not hands_free,
                             preroll=None if (manual or asked or not first) else preroll,
                             end_silence=PAGE_END_SILENCE_S if hands_free else END_SILENCE_S)
                first = False
                if pcm is None:
                    if cancel_listen.is_set():
                        log("  (listening cancelled)")
                    elif wait > FOLLOWUP_S or FOLLOWUP_S == 0:
                        chime(CHIME_MISS)
                    break
                chime(CHIME_OFF)
                ui("thinking")
                try:
                    text = transcribe(pcm)
                except Exception as e:                # noqa: BLE001
                    log(f"  ! couldn't transcribe: {e}")
                    # never fail silently: say the ears are down
                    subprocess.run(["say", "-v", FALLBACK_VOICE, "Sorry, I couldn't hear that. My hearing is down."])
                    break
                ui("heard", text=re.sub(r"^\W*(hey|hi|ok|okay)?\W*jarvis\W*", "", text, flags=re.I).strip())
                said_name = bool(re.match(r"^\W*(hey|hi|ok|okay)?\W*jarvis\b", text, re.I))
                text = re.sub(r"^\W*(hey|hi|ok|okay)?\W*jarvis\W*", "", text, flags=re.I).strip()
                if not text:
                    break
                if is_standby(text):
                    log(f"you: {text}  (on standby)")
                    go_standby()
                    break
                followup = wait <= FOLLOWUP_S and not asked
                asked = False
                if followup and not said_name and len(re.findall(r"[A-Za-z]+", text)) < 3:
                    # in the follow-up window, a stray "45" or "okay" from the room
                    # isn't a question — stop listening instead of answering it
                    log(f"  (ignored background: {text!r})")
                    break
                log(f"you: {text}")
                ui("thinking")
                answer, cut_in = ask_and_speak(text, oww)   # saying "JARVIS" while it talks stops it
                if cut_in is not None:
                    # you cut it off: listen to what you say next, keeping the audio
                    # since "JARVIS" so "JARVIS, what about…" isn't clipped
                    preroll.clear(); preroll.extend(cut_in)
                    wait, manual, asked, first = 6.0, False, False, True
                    continue
                drain(); oww.reset()
                if asks_question(answer):
                    # JARVIS asked you something: keep listening for your reply,
                    # no "Hey JARVIS" needed. Pauses end it; tap ⌘ / ✓ works too.
                    log(f"  (asked you something — listening {ASK_LISTEN_S:.0f}s for your answer)")
                    wait, manual, asked = ASK_LISTEN_S, False, True
                    continue
                if FOLLOWUP_S <= 0:
                    break
                wait = FOLLOWUP_S
            ui("idle")
            drain(); oww.reset(); preroll.clear()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  listener down.")
    except sd.PortAudioError as e:
        sys.exit(f"microphone error: {e}\nAllow microphone access for this app in "
                 "System Settings → Privacy & Security → Microphone.")
