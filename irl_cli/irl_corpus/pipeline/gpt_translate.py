"""GPT-based translation of transcript text.

This is a standalone step (unlike ``pipeline.transcribe``, whose legacy ASR
service happens to bundle translation into the same call): it takes whatever
text is already in an EAF's 'transcript' tier — regardless of which ASR
engine produced it — and translates it via the OpenAI Chat Completions API.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-5-nano"
DEFAULT_BATCH_SIZE = 40

# Repo root is 4 levels up from irl_cli/irl_corpus/pipeline/gpt_translate.py
_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_PROMPT_PATH = _REPO_ROOT / "config" / "translate_system_prompt.txt"
_LOG_PATH = _REPO_ROOT / "logs" / "gpt_translate.jsonl"

_logger = logging.getLogger(__name__)


def _load_system_prompt(*, target_lang: str, source_lang: str, count: int) -> str:
    """Render the translation system prompt from its config template.

    Args:
        target_lang: Target language, e.g. ``"en"`` or ``"English"``.
        source_lang: Optional source-language hint, or ``""`` to omit it.
        count: Number of numbered lines in this batch, embedded in the
            prompt so the model knows exactly how many translations to
            return.

    Returns:
        The rendered system prompt.

    Raises:
        FileNotFoundError: If the prompt template is missing from ``config/``.
    """
    if not _PROMPT_PATH.exists():
        raise FileNotFoundError(
            f"Translation prompt template not found at {_PROMPT_PATH}. "
            "Check your installation."
        )
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    source_hint = f" The source language is {source_lang}." if source_lang else ""
    return template.format(
        target_lang=target_lang, source_hint=source_hint, count=count
    )


def _log_mismatch(  # pylint: disable=too-many-arguments
    *,
    texts: list[str],
    target_lang: str,
    model: str,
    raw_response: str,
    expected: int,
    got: object,
) -> None:
    """Append one JSON line describing a failed batch to the debug log.

    Best-effort: logging failures are swallowed (with a warning) so they
    never mask the actual translation error.

    Args:
        texts: The batch of source strings that was sent.
        target_lang: Target language for the batch.
        model: Chat Completions model id used.
        raw_response: The raw (unparsed-as-translations) model content.
        expected: Expected number of translations (``len(texts)``).
        got: Number of translations actually returned, or ``"invalid"``.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_lang": target_lang,
        "model": model,
        "expected": expected,
        "got": got,
        "texts": texts,
        "raw_response": raw_response,
    }
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        _logger.warning("Failed to write gpt_translate log entry", exc_info=True)


def _post_chat(messages: list[dict], api_key: str, model: str) -> dict:
    """POST a Chat Completions request and return the parsed JSON response.

    Args:
        messages: Chat messages (system/user) to send.
        api_key: OpenAI API key, sent via the ``Authorization`` header.
        model: Chat Completions model id, e.g. ``"gpt-5-nano"``.

    Returns:
        The parsed JSON response body from the API.

    Raises:
        urllib.error.HTTPError: If the API returns a non-2xx response. The
            error's ``reason`` is extended with the response body (truncated
            to 500 chars) to aid debugging.
    """
    # No "temperature" override here: gpt-5-family models (e.g. gpt-5-nano)
    # reject any value other than the default (1), so it's left unset for
    # compatibility across model families.
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
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


def _translate_batch(
    texts: list[str],
    api_key: str,
    *,
    target_lang: str,
    source_lang: str,
    model: str,
) -> list[str]:
    """Translate one batch of texts, preserving order and count.

    Args:
        texts: Source strings to translate, in order. Must be non-empty.
        api_key: OpenAI API key.
        target_lang: Target language, e.g. ``"en"`` or ``"English"``.
        source_lang: Optional source-language hint, or ``""`` to omit it.
        model: Chat Completions model id.

    Returns:
        A list of translated strings, same length and order as ``texts``.

    Raises:
        ValueError: If the model's JSON response doesn't contain exactly
            ``len(texts)`` translations. The full batch and raw response are
            appended to the debug log first (see ``_LOG_PATH``).
    """
    numbered = "\n".join(f"{i}: {text}" for i, text in enumerate(texts))
    system_prompt = _load_system_prompt(
        target_lang=target_lang, source_lang=source_lang, count=len(texts)
    )

    response = _post_chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": numbered},
        ],
        api_key,
        model,
    )
    content = response["choices"][0]["message"]["content"]
    data = json.loads(content)
    translations = data.get("translations")

    if not isinstance(translations, list) or len(translations) != len(texts):
        got = len(translations) if isinstance(translations, list) else "invalid"
        _log_mismatch(
            texts=texts,
            target_lang=target_lang,
            model=model,
            raw_response=content,
            expected=len(texts),
            got=got,
        )
        raise ValueError(
            f"Translation response had {got} entries, expected {len(texts)}. "
            f"Full batch and raw response logged to {_LOG_PATH}."
        )

    return [str(t) for t in translations]


def translate_texts(  # pylint: disable=too-many-arguments
    texts: list[str],
    api_key: str,
    *,
    target_lang: str,
    source_lang: str = "",
    model: str = DEFAULT_MODEL,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[str]:
    """Translate a list of transcript strings into ``target_lang``.

    Splits ``texts`` into chunks of ``batch_size`` so long transcripts don't
    risk hitting response-size/token limits in a single call; each chunk is
    a separate request.

    Args:
        texts: Source strings to translate, in order.
        api_key: OpenAI API key (``OPENAI_API_KEY``).
        target_lang: Target language, e.g. ``"en"`` or ``"English"``.
        source_lang: Optional source-language hint, or ``""`` to omit it.
        model: Chat Completions model id.
        batch_size: Maximum number of lines translated per API call.

    Returns:
        A list of translated strings, same length and order as ``texts``.
    """
    if not texts:
        return []

    translations: list[str] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        translations.extend(
            _translate_batch(
                batch,
                api_key,
                target_lang=target_lang,
                source_lang=source_lang,
                model=model,
            )
        )
    return translations
