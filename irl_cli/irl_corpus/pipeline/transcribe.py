"""Transcription pipeline: split WAV into chunks, call ASR API, merge results."""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def _split_wav(wav_path: Path, chunk_seconds: int, chunks_dir: Path) -> list[Path]:
    """Split a WAV file into fixed-length chunks using ffmpeg."""
    chunks_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(chunks_dir / f"{wav_path.stem}_chunk_%03d.wav")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(wav_path),
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-c",
            "copy",
            pattern,
        ],
        check=True,
    )
    return sorted(chunks_dir.glob(f"{wav_path.stem}_chunk_*.wav"))


def _post_chunk(
    chunk_path: Path,
    api_url: str,
    *,
    lang: str,
    translate_lang: str,
    translation_model: str,
) -> dict:
    """POST a single WAV chunk to the transcription API and return parsed JSON."""
    boundary = "----IrlCorpusBoundary"
    audio_data = chunk_path.read_bytes()

    def _field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    body = b""
    for name, value in [
        ("rttm_time_offset", "0"),
        ("translation_language", translate_lang),
        ("file_path", ""),
        ("rttm_path", ""),
        ("translation_preserve_syllables", "false"),
        ("output_format", "json"),
        ("language", lang),
        ("translation_model", translation_model),
        ("rttm", ""),
    ]:
        body += _field(name, value)

    # file field
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{chunk_path.name}"\r\n'
        "Content-Type: audio/vnd.wave\r\n\r\n"
    ).encode()
    body += audio_data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        api_url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:  # nosec
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise urllib.error.HTTPError(
            exc.url, exc.code, f"{exc.reason} — {body}", exc.headers, None
        ) from None


def _offset_segments(data: dict, offset_seconds: float) -> dict:
    """Add a time offset (in seconds) to all segment/word timestamps."""

    def shift(items: list, keys: list[str]) -> list:
        return [
            {**item, **{k: item[k] + offset_seconds for k in keys if k in item}}
            for item in items
        ]

    segments = data.get("segments") or []
    shifted_segments = []
    for seg in segments:
        shifted = {**seg}
        for key in ("start", "end"):
            if key in shifted:
                shifted[key] = shifted[key] + offset_seconds
        words = shifted.get("words") or []
        shifted["words"] = shift(words, ["start", "end"])
        shifted_segments.append(shifted)

    return {
        **data,
        "segments": shifted_segments,
        "word_segments": shift(data.get("word_segments") or [], ["start", "end"]),
    }


def _merge(results: list[dict]) -> dict:
    """Merge multiple chunk results into a single dict."""
    all_segments: list = []
    all_words: list = []
    for result in results:
        all_segments.extend(result.get("segments") or [])
        all_words.extend(result.get("word_segments") or [])
    return {"segments": all_segments, "word_segments": all_words}


def transcribe_wav(
    wav_path: Path,
    api_url: str,
    *,
    lang: str = "fr",
    translate_lang: str = "",
    translation_model: str = "gpt-4.1",
    chunk_seconds: int = 240,
    work_dir: Path | None = None,
) -> dict:
    """Transcribe a WAV file by splitting into chunks and calling the ASR API.

    Returns a dict with ``segments`` and ``word_segments`` lists, with
    timestamps in seconds relative to the start of ``wav_path``.
    """
    if work_dir is None:
        work_dir = wav_path.parent / f"{wav_path.stem}_chunks"

    chunks_dir = work_dir / "chunks"
    chunks = _split_wav(wav_path, chunk_seconds, chunks_dir)

    results = []
    for chunk_path in chunks:
        # Derive chunk index from filename suffix _chunk_NNN.wav
        stem = chunk_path.stem  # e.g. segment_chunk_002
        index = int(stem.rsplit("_", 1)[-1])
        offset = index * chunk_seconds

        data = _post_chunk(
            chunk_path,
            api_url,
            lang=lang,
            translate_lang=translate_lang,
            translation_model=translation_model,
        )
        results.append(_offset_segments(data, float(offset)))

    return _merge(results)
