from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

try:
    from . import config as cfg
    from .monitoring import (
        LOGGER,
        clear_monitor_current_row,
        clear_terminal_status,
        update_monitor_current_row,
        write_terminal_status,
    )
    from .rows import output_row
except ImportError:  # Allows running via python src/main.py
    import config as cfg
    from monitoring import (
        LOGGER,
        clear_monitor_current_row,
        clear_terminal_status,
        update_monitor_current_row,
        write_terminal_status,
    )
    from rows import output_row


def parse_score_result(content: str) -> tuple[float, str]:
    """Parse and validate the score result returned by the Ollama model."""
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    expected_keys = {"score", "reasoning"}
    actual_keys = set(data)
    if actual_keys != expected_keys:
        raise ValueError(f"Expected keys {sorted(expected_keys)}, got {sorted(actual_keys)}")

    score = float(data["score"])
    if not 0.0 <= score <= 100.0:
        raise ValueError(f"Score outside 0-100 range: {score}")

    reasoning = data["reasoning"]
    if not isinstance(reasoning, str):
        raise TypeError(f"Reasoning must be a string, got {type(reasoning).__name__}")

    reasoning = " ".join(reasoning.strip().split())
    if not reasoning:
        raise ValueError("Reasoning must not be empty")

    return round(score, 4), reasoning


def ollama_score(text: str, prompt: str, previous_error: str | None = None) -> tuple[float, str]:
    """Send one filing text to Ollama and return its hype/vagueness score result."""
    retry_instruction = ""
    if previous_error:
        retry_instruction = (
            "Your previous answer was rejected by the parser for this reason:\n"
            f"{previous_error}\n\n"
            "Correct the problem. Return a score from 0.0 to 100.0 and one reasoning sentence "
            "as exactly one JSON object.\n\n"
        )

    payload = {
        "model": cfg.MODEL,
        "stream": False,
        "format": {
            "type": "object",
            "properties": {
                "score": {"type": "number", "minimum": 0, "maximum": 100},
                "reasoning": {"type": "string"},
            },
            "required": ["score", "reasoning"],
            "additionalProperties": False,
        },
        "think": False,
        "messages": [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": (
                    "/no_think\n"
                    f"{retry_instruction}"
                    "Score this 8-K Item 2.02 text for hype/vagueness. "
                    "Return exactly one JSON object matching this schema: "
                    '{"score": number, "reasoning": string}. '
                    "The reasoning must be exactly one concise sentence. "
                    "Do not include markdown, comments, or extra keys.\n\n"
                    f"TEXT:\n{text.strip()}"
                ),
            },
        ],
        "options": {
            "temperature": 0,
            "top_k": 1,
            "top_p": 0.1,
            "seed": 42,
            "num_predict": 128,
        },
    }

    request = urllib.request.Request(
        f"{cfg.OLLAMA_URL.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=cfg.REQUEST_TIMEOUT_SECONDS) as response:
        response_data = json.loads(response.read().decode("utf-8"))

    content = response_data.get("message", {}).get("content", "")
    if not content:
        raise ValueError(f"Ollama returned no message content: {response_data}")

    return parse_score_result(content)


def score_with_retries(row: dict[str, str], prompt: str, row_number: int) -> dict[str, str]:
    """Score one input row, retrying transient model or parsing failures."""
    last_error: Exception | None = None
    previous_error: str | None = None
    text_chars = len(row["item_202_text"])
    active_id = str(row_number)

    try:
        for attempt in range(cfg.RETRIES + 1):
            try:
                update_monitor_current_row(active_id, row, row_number, text_chars, attempt + 1)
                if cfg.TERMINAL_STATUS_EVERY > 0 and row_number % cfg.TERMINAL_STATUS_EVERY == 0:
                    write_terminal_status(
                        f"currently scoring row={row_number} ticker={row['ticker']} "
                        f"filingDate={row['filingDate']} accession={row['accessionNumber']} "
                        f"chars={text_chars} attempt={attempt + 1}/{cfg.RETRIES + 1}"
                    )
                score, reasoning = ollama_score(row["item_202_text"], prompt, previous_error)
                return output_row(row, score, reasoning)
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
                last_error = exc
                previous_error = str(exc)
                if attempt >= cfg.RETRIES:
                    break
                clear_terminal_status()
                LOGGER.warning(
                    "retrying row ticker=%s accession=%s next_attempt=%s/%s feedback_error=%s",
                    row["ticker"],
                    row["accessionNumber"],
                    attempt + 2,
                    cfg.RETRIES + 1,
                    previous_error,
                )
                time.sleep(min(2**attempt, 8))

        raise RuntimeError(f"failed after {cfg.RETRIES + 1} attempts: {last_error}")
    finally:
        clear_monitor_current_row(active_id)
