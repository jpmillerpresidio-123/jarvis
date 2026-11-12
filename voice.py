"""
Text to speech.

ElevenLabs when a key is present, browser speechSynthesis otherwise.

The key stays here, server-side. The browser asks /api/speak for audio and gets
mp3 bytes back — it never sees the key, so nothing leaks into devtools, the
page source, or a screen recording.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

# ── ElevenLabs health ──────────────────────────────────────
# When ElevenLabs is out of credits (or the key stops working), every request
# fails. Without this, JARVIS kept trying, waited on each failure, and — worst —
# couldn't HEAR anything, so "go to bed" was silently dropped. Now a failure
# benches ElevenLabs for a while: hearing switches to on-device Whisper and the
# voice to macOS speech, instantly, and you get one notification saying why.
_down = {"until": 0.0, "why": "", "told": 0.0}


def _mark_down(err):
    msg = str(err)
    if "quota_exceeded" in msg or "credits" in msg:
        why, secs = "Your ElevenLabs credits ran out", 3600
    elif msg.startswith(("401", "403")) or "unauthorized" in msg:
        why, secs = "ElevenLabs rejected the API key", 3600
    else:
        why, secs = "ElevenLabs isn't responding", 120
    _down.update(until=time.time() + secs, why=why)
    if secs >= 3600 and time.time() - _down["told"] > 6 * 3600:
        _down["told"] = time.time()
        subprocess.Popen(["osascript", "-e",
                          f'display notification "{why}. JARVIS is using its backup voice and on-device '
                          f'hearing until it\'s fixed." with title "JARVIS"'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def eleven_ok():
    return available() and time.time() >= _down["until"]


def health():
    return dict(voice="kokoro" if TTS != "elevenlabs" else "elevenlabs", kokoro=kokoro_ok(),
                elevenlabs=eleven_ok(), why="" if eleven_ok() else _down["why"],
                hearing=STT, local_stt=_local["model"] is not None)


# ── on-device speech-to-text (backup ears) ─────────────────
# faster-whisper "small.en" on the Mac's GPU: about a second per sentence, no
# account, nothing leaves the Mac. Loaded in the background at start-up.
LOCAL_MODEL = os.environ.get("JARVIS_LOCAL_STT", "small.en")
_local = {"model": None, "err": None}
_local_lock = threading.Lock()


def _local_model():
    with _local_lock:
        if _local["model"] is None:
            from faster_whisper import WhisperModel
            try:
                _local["model"] = WhisperModel(LOCAL_MODEL, device="mps", compute_type="float16")
            except Exception:                          # noqa: BLE001  (no GPU backend: CPU, smaller model)
                _local["model"] = WhisperModel("base.en", device="cpu", compute_type="int8")
        return _local["model"]


def warm_local():
    def go():
        try:
            m = _local_model()
            import numpy as np
            m.transcribe(np.zeros(16000, dtype="float32"), language="en", beam_size=1)
        except Exception as e:                         # noqa: BLE001
            _local["err"] = str(e)[:200]
    threading.Thread(target=go, daemon=True).start()


def transcribe_local(audio, ext="wav"):
    m = _local_model()
    with tempfile.NamedTemporaryFile(suffix="." + ext) as f:
        f.write(audio); f.flush()
        segs, _info = m.transcribe(f.name, language="en", beam_size=1, without_timestamps=True,
                                   condition_on_previous_text=False, vad_filter=True,
                                   initial_prompt=os.environ.get("JARVIS_STT_HINTS", "JARVIS, standby."))
        return _clean(" ".join(s.text for s in segs))

# Rachel — calm, British-adjacent. Override with ELEVENLABS_VOICE_ID.
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
# Tell speech-to-text which language you speak (skips auto-detect, which can
# garble short phrases). Blank = auto-detect.
_LANG = os.environ.get("JARVIS_STT_LANG", "en").strip()


def available():
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def voice_id():
    return os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE)


# ── which voice / which ears ───────────────────────────────
# JARVIS_TTS: kokoro (default: free, on-device, unlimited) or elevenlabs (paid credits).
#   Either way the other one is the backup, then the macOS voice.
# JARVIS_STT: local (default: on-device Whisper, free) or elevenlabs.
TTS = os.environ.get("JARVIS_TTS", "kokoro").strip().lower()
STT = os.environ.get("JARVIS_STT", "local").strip().lower()
KOKORO = f"http://127.0.0.1:{os.environ.get('JARVIS_KOKORO_PORT', '8732')}"


def kokoro_ok():
    try:
        with urllib.request.urlopen(KOKORO + "/health", timeout=1) as r:
            return r.status == 200
    except Exception:                                  # noqa: BLE001
        return False


def speak_kokoro(text):
    req = urllib.request.Request(KOKORO + "/speak", data=json.dumps({"text": text}).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def speak_any(text):
    """(audio bytes, mime type), trying the chosen voice first and the other as backup.
    Raises if both fail — callers then use the macOS voice."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    order = ["elevenlabs", "kokoro"] if TTS == "elevenlabs" else ["kokoro", "elevenlabs"]
    err = None
    for eng in order:
        try:
            if eng == "kokoro":
                return speak_kokoro(text), "audio/wav"
            if eleven_ok():
                return speak(text), "audio/mpeg"
        except Exception as e:                         # noqa: BLE001
            err = e
    raise RuntimeError(f"no voice available ({err})")


def speak(text):
    """ElevenLabs only. Returns mp3 bytes, or raises. Most callers want speak_any()."""
    if not available():
        raise RuntimeError("no ELEVENLABS_API_KEY")
    if not eleven_ok():
        raise RuntimeError(f"elevenlabs benched: {_down['why']}")
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")

    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id()}",
        data=json.dumps({
            "text": text[:2500],
            "model_id": MODEL,
            "voice_settings": {
                "stability": 0.42,          # a little variation; 0.5+ goes flat
                "similarity_boost": 0.85,
                "style": 0.25,
                "use_speaker_boost": True,
            },
        }).encode(),
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "audio/mpeg",
            "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        # Surface the real reason (quota, bad key, bad voice id) instead of a
        # bare status code — the UI uses this to decide whether to fall back.
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:                              # noqa: BLE001
            body = ""
        _mark_down(f"{e.code} {body}")
        raise RuntimeError(f"{e.code} {body}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _mark_down(e)
        raise


def _clean(text):
    """Drop sound tags like "[background noise]" or "(music)". A turn that was
    only noise comes back empty, so the HUD re-listens instead of sending it."""
    return re.sub(r"\[[^\]]*\]|\([^)]*\)", "", text).strip()


def transcribe(audio, mime="audio/webm"):
    """ElevenLabs Scribe when it's working; on-device Whisper when it isn't."""
    if not audio:
        raise ValueError("empty audio")
    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4",
           "audio/mpeg": "mp3", "audio/wav": "wav"}.get(mime.split(";")[0], "webm")
    if STT == "elevenlabs" and eleven_ok():
        try:
            return transcribe_eleven(audio, mime)
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:                          # noqa: BLE001
                body = ""
            _mark_down(f"{e.code} {body}")
        except Exception as e:                         # noqa: BLE001
            _mark_down(e)
    return transcribe_local(audio, ext)


def transcribe_eleven(audio, mime="audio/webm"):
    """Speech to text via ElevenLabs Scribe. Returns the transcript, or raises.

    Used instead of the browser's Web Speech API because that one only exists in
    Chrome and is a silently-failing stub in Brave — and because it ships your
    audio to Google. This keeps listening on the same provider as speaking.
    """
    if not available():
        raise RuntimeError("no ELEVENLABS_API_KEY")
    if not audio:
        raise ValueError("empty audio")

    ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "mp4",
           "audio/mpeg": "mp3", "audio/wav": "wav"}.get(mime.split(";")[0], "webm")
    boundary = "----jarvis" + os.urandom(8).hex()
    b = boundary.encode()

    body = b"".join([
        b"--", b, b"\r\n",
        b'Content-Disposition: form-data; name="model_id"\r\n\r\nscribe_v1\r\n',
        b"--", b, b"\r\n",
        b'Content-Disposition: form-data; name="tag_audio_events"\r\n\r\nfalse\r\n',
        *([b"--", b, b"\r\n",
           f'Content-Disposition: form-data; name="language_code"\r\n\r\n{_LANG}\r\n'.encode()]
          if _LANG else []),
        b"--", b, b"\r\n",
        f'Content-Disposition: form-data; name="file"; filename="turn.{ext}"\r\n'.encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        audio, b"\r\n",
        b"--", b, b"--\r\n",
    ])

    req = urllib.request.Request(
        "https://api.elevenlabs.io/v1/speech-to-text",
        data=body, method="POST",
        headers={
            "content-type": f"multipart/form-data; boundary={boundary}",
            "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
        },
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        return _clean(json.loads(r.read()).get("text") or "")


def voices():
    """List the account's voices, so you can find an id to put in .env."""
    if not available():
        return []
    req = urllib.request.Request(
        "https://api.elevenlabs.io/v2/voices",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read())
    except Exception:                                  # noqa: BLE001
        return []
    return [dict(id=v.get("voice_id"), name=v.get("name"),
                 labels=v.get("labels", {}))
            for v in body.get("voices", [])]
