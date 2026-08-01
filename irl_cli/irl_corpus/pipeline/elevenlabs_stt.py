"""ElevenLabs Scribe v2 batch speech-to-text.

Scribe v2 performs ASR, non-speech filtering, and speaker diarization in a
single call, so unlike ``pipeline.transcribe`` no chunking or separate
VAD/diarization pass is required here.
"""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
DEFAULT_MODEL_ID = "scribe_v2"


def _post_file(  # pylint: disable=too-many-arguments
    audio_path: Path,
    api_key: str,
    *,
    model_id: str,
    language_code: str,
    diarize: bool,
    num_speakers: int | None,
    tag_audio_events: bool,
    timestamps_granularity: str,
) -> dict:
    """POST an audio file to the ElevenLabs speech-to-text batch API.

    Args:
        audio_path: Path to the audio file to upload (e.g. a 16 kHz mono WAV).
        api_key: ElevenLabs API key, sent via the ``xi-api-key`` header.
        model_id: Scribe model id, e.g. ``"scribe_v2"``.
        language_code: ISO 639-1/3 language hint, or ``""`` to auto-detect.
        diarize: Whether to enable speaker diarization.
        num_speakers: Maximum number of speakers to detect, or ``None`` to
            let the API decide.
        tag_audio_events: Whether to detect non-speech audio events
            (laughter, applause, etc.) as separate word-list entries.
        timestamps_granularity: One of ``"none"``, ``"word"``, ``"character"``.

    Returns:
        The parsed JSON response body from the API.

    Raises:
        urllib.error.HTTPError: If the API returns a non-2xx response. The
            error's ``reason`` is extended with the response body (truncated
            to 500 chars) to aid debugging.
    """
    boundary = "----IrlCorpusElevenLabsBoundary"
    audio_data = audio_path.read_bytes()
    content_type = mimetypes.guess_type(audio_path.name)[0] or "audio/vnd.wave"

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    fields = [
        ("model_id", model_id),
        ("diarize", "true" if diarize else "false"),
        ("tag_audio_events", "true" if tag_audio_events else "false"),
        ("timestamps_granularity", timestamps_granularity),
    ]
    if language_code:
        fields.append(("language_code", language_code))
    if num_speakers is not None:
        fields.append(("num_speakers", str(num_speakers)))

    body = b""
    for name, value in fields:
        body += _field(name, value)

    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    body += audio_data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "xi-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:  # nosec
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise urllib.error.HTTPError(
            exc.url, exc.code, f"{exc.reason} — {detail}", exc.headers, None
        ) from None


def _group_into_segments(words: list[dict], max_gap_seconds: float) -> list[dict]:
    """Group word-level output into speaker-turn segments.

    A new segment starts on a speaker change or on a silence gap longer than
    ``max_gap_seconds``. Scribe v2 already drops non-speech audio, so gaps
    here mark natural pause boundaries rather than requiring a separate VAD
    pass over the audio.

    Args:
        words: Word-level entries from the Scribe v2 response, each with
            ``text``, ``start``, ``end``, ``type``, and ``speaker_id`` keys.
        max_gap_seconds: Silence gap (seconds) beyond which a new segment is
            started even if the speaker hasn't changed.

    Returns:
        A list of segment dicts with ``start``, ``end``, ``text``, and
        ``speaker`` keys, in chronological order. Segments with empty text
        (e.g. a run of only audio-event tokens) are dropped.
    """
    segments: list[dict] = []
    current: dict | None = None

    for word in words:
        if word.get("type") == "audio_event":
            continue

        text = word.get("text", "")
        start = float(word.get("start", 0.0))
        end = float(word.get("end", start))
        speaker = word.get("speaker_id")

        if current is not None:
            gap = start - current["end"]
        if current is None or speaker != current["speaker"] or gap > max_gap_seconds:
            current = {"start": start, "end": end, "text": text, "speaker": speaker}
            segments.append(current)
        else:
            current["text"] += text
            current["end"] = end

    for seg in segments:
        seg["text"] = seg["text"].strip()

    return [seg for seg in segments if seg["text"]]


def transcribe_wav(  # pylint: disable=too-many-arguments
    wav_path: Path,
    api_key: str,
    *,
    language_code: str = "",
    num_speakers: int | None = None,
    model_id: str = DEFAULT_MODEL_ID,
    tag_audio_events: bool = True,
    max_gap_seconds: float = 1.5,
) -> dict:
    """Transcribe and diarize a WAV file in a single Scribe v2 call.

    Args:
        wav_path: Path to the WAV file to transcribe.
        api_key: ElevenLabs API key (``ELEVENLABS_API_KEY``).
        language_code: ISO 639-1/3 language hint, or ``""`` to auto-detect.
        num_speakers: Maximum number of speakers to detect, or ``None`` to
            let the API decide.
        model_id: Scribe model id to use.
        tag_audio_events: Whether to detect non-speech audio events; these
            are excluded when segments are built.
        max_gap_seconds: Silence gap (seconds) beyond which a new segment is
            started even for the same speaker.

    Returns:
        A dict with:

        * ``segments``: speaker-turn dicts with ``start``, ``end``, ``text``,
          ``speaker`` (seconds relative to ``wav_path``).
        * ``words``: the raw word-level list from the API response.
        * ``language_code``: the detected language code.
        * ``text``: the full transcript text.
    """
    data = _post_file(
        wav_path,
        api_key,
        model_id=model_id,
        language_code=language_code,
        diarize=True,
        num_speakers=num_speakers,
        tag_audio_events=tag_audio_events,
        timestamps_granularity="word",
    )
    words = data.get("words") or []
    segments = _group_into_segments(words, max_gap_seconds)
    return {
        "segments": segments,
        "words": words,
        "language_code": data.get("language_code"),
        "text": data.get("text"),
    }
