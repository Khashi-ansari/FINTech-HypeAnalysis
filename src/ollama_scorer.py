from __future__ import annotations

import hashlib
import json
import re
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
    from .rows import output_row, row_key
    from .scoring import COUNT_FIELDNAMES, normalized_census, score_reasoning, vagueness_score
except ImportError:  # Allows running via python src/main.py
    import config as cfg
    from monitoring import (
        LOGGER,
        clear_monitor_current_row,
        clear_terminal_status,
        update_monitor_current_row,
        write_terminal_status,
    )
    from rows import output_row, row_key
    from scoring import COUNT_FIELDNAMES, normalized_census, score_reasoning, vagueness_score


def coerce_text(value: object) -> str:
    return "" if value is None else str(value)


MAX_ERROR_PREVIEW_CHARS = 1200
NON_TERMINAL_ABBREVIATIONS = {"U.S.", "U.K.", "Mr.", "Ms.", "Dr.", "Prof.", "Inc.", "Corp.", "Co.", "Ltd.", "LLC."}


def preview_text(value: object, max_chars: int = MAX_ERROR_PREVIEW_CHARS) -> str:
    """Return a compact, log-safe preview of model output or parser feedback."""
    text = coerce_text(value).replace("\r\n", "\n").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


class ModelResponseError(ValueError):
    """Parser failure with the model response attached for diagnostics."""

    def __init__(self, stage: str, parser_error: Exception, response_content: str) -> None:
        self.stage = stage
        self.parser_error = parser_error
        self.response_preview = preview_text(response_content)
        super().__init__(f"{stage} model response invalid: {parser_error}")


class ScoringFailure(RuntimeError):
    """Final row failure after all retries, preserving the actionable root cause."""

    def __init__(self, attempts: int, last_error: Exception | None) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"failed after {attempts} attempts: {last_error}")


def root_diagnostic_error(error: Exception) -> Exception:
    """Unwrap retry failures to the error that should be written to diagnostics."""
    if isinstance(error, ScoringFailure) and error.last_error is not None:
        return root_diagnostic_error(error.last_error)
    return error


def prompt_target_for_stage(stage: str) -> str:
    """Return the prompt file most likely responsible for this model-output issue."""
    if stage == "validation":
        return str(cfg.VALIDATION_PROMPT_FILE)
    if stage == "scoring":
        return str(cfg.PROMPT_FILE)
    return ""


def error_diagnostic_info(error: Exception) -> dict[str, str]:
    """Build structured fields for CSV logging."""
    root_error = root_diagnostic_error(error)
    stage = getattr(root_error, "stage", "runtime")
    return {
        "error_stage": stage,
        "error_type": type(root_error).__name__,
        "error_message": preview_text(root_error),
        "prompt_target": prompt_target_for_stage(stage),
        "model_output_preview": getattr(root_error, "response_preview", ""),
    }


def error_diagnostic_fields(error: Exception) -> dict[str, str]:
    """Return CSV-ready diagnostic fields for a failed row."""
    return error_diagnostic_info(error)


def first_complete_sentence(value: str) -> str | None:
    """Return the first sentence without treating common abbreviations as sentence endings."""
    for match in re.finditer(r"[.!?](?=\s|$)", value):
        candidate = value[: match.end()].strip()
        last_token = candidate.rsplit(" ", 1)[-1]
        if last_token in NON_TERMINAL_ABBREVIATIONS:
            continue
        return candidate
    return None


def normalize_sentence(value: object, field_name: str) -> str:
    """Validate and normalize one sentence-like string returned by the model."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")

    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")

    first_sentence = first_complete_sentence(normalized)
    if first_sentence:
        normalized = first_sentence
    elif normalized[-1] not in ".!?":
        normalized += "."

    return normalized


def validate_census_fields(data: dict[str, object]) -> dict[str, int]:
    """Validate and return the six non-negative integer count fields."""
    census: dict[str, int] = {}
    for field_name in COUNT_FIELDNAMES:
        value = data.get(field_name)
        if type(value) is not int:
            raise TypeError(f"{field_name} must be an integer, got {type(value).__name__}")
        if value < 0:
            raise ValueError(f"{field_name} must be non-negative, got {value}")
        census[field_name] = value
    return census


def parse_census_result(content: str) -> dict[str, int]:
    """Parse and validate the six-count census returned by the Ollama model."""
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    expected_keys = set(COUNT_FIELDNAMES)
    actual_keys = set(data)
    if actual_keys != expected_keys:
        raise ValueError(f"Expected keys {sorted(expected_keys)}, got {sorted(actual_keys)}")

    return validate_census_fields(data)


def parse_validation_result(content: str) -> tuple[bool, dict[str, int], str]:
    """Parse and validate the count-census validation result returned by Ollama."""
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    expected_keys = {*COUNT_FIELDNAMES, "valid", "validation_reasoning"}
    actual_keys = set(data)
    if actual_keys != expected_keys:
        raise ValueError(f"Expected keys {sorted(expected_keys)}, got {sorted(actual_keys)}")

    valid = data["valid"]
    if type(valid) is not bool:
        raise TypeError(f"valid must be a boolean, got {type(valid).__name__}")

    census = validate_census_fields(data)
    validation_reasoning = normalize_sentence(data["validation_reasoning"], "Validation reasoning")
    return valid, census, validation_reasoning


def should_validate(row: dict[str, str], validation_pct: float) -> bool:
    """Return whether this row belongs to the deterministic validation sample."""
    if not 0.0 <= validation_pct <= 1.0:
        raise ValueError(f"VALIDATION_PCT must be between 0.0 and 1.0, got {validation_pct}")
    if validation_pct <= 0.0:
        return False
    if validation_pct >= 1.0:
        return True

    digest = hashlib.sha256(row_key(row).encode("utf-8")).hexdigest()
    bucket = int(digest[:16], 16) / 0xFFFFFFFFFFFFFFFF
    return bucket < validation_pct


def census_schema_properties() -> dict[str, dict[str, object]]:
    """Return the JSON schema properties for the six count fields."""
    return {field_name: {"type": "integer", "minimum": 0} for field_name in COUNT_FIELDNAMES}


def ollama_score(text: str, prompt: str, previous_error: str = "") -> dict[str, int]:
    """Send one filing text to Ollama and return count census fields."""
    retry_instruction = ""
    if previous_error:
        retry_instruction = (
            "Your previous answer was rejected by the parser for this reason:\n"
            f"{previous_error}\n\n"
            "Correct the problem. Return exactly one JSON object with the six non-negative integer count fields.\n\n"
        )

    payload = {
        "model": cfg.MODEL,
        "stream": False,
        "format": {
            "type": "object",
            "properties": census_schema_properties(),
            "required": list(COUNT_FIELDNAMES),
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
                    "Count this 8-K Item 2.02 text for hype/vagueness evidence. "
                    "Return exactly one JSON object matching this schema: "
                    '{"sentences_total": integer, "sentences_concrete": integer, "sentences_vague": integer, '
                    '"promotional_terms": integer, "buzzword_hedge_terms": integer, "distinct_figures": integer}. '
                    "Do not include a score, reasoning, markdown, comments, or extra keys.\n\n"
                    f"TEXT:\n{text.strip()}"
                ),
            },
        ],
        # Determinism is safe here because the model emits count fields only;
        # Python computes the continuous final score from those counts.
        "options": {
            "temperature": 0,
            "top_k": 1,
            "top_p": 0.1,
            "seed": 42,
            "num_predict": 96,
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
        raise ModelResponseError(
            "scoring",
            ValueError("Ollama returned no message content"),
            json.dumps(response_data, ensure_ascii=True),
        )

    try:
        return parse_census_result(content)
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
        raise ModelResponseError("scoring", exc, content) from exc


def ollama_validate_score(
    text: str,
    census: dict[str, int],
    validation_prompt: str,
    previous_error: str = "",
) -> tuple[bool, dict[str, int], str]:
    """Validate one count census and return accepted or corrected counts."""
    retry_instruction = ""
    if previous_error:
        retry_instruction = (
            "Your previous validation answer was rejected by the parser for this reason:\n"
            f"{previous_error}\n\n"
            "Correct the problem. Return exactly one JSON object with valid, validation_reasoning, "
            "and the six non-negative integer count fields.\n\n"
        )

    census_result = normalized_census(census)
    payload = {
        "model": cfg.MODEL,
        "stream": False,
        "format": {
            "type": "object",
            "properties": {
                "valid": {"type": "boolean"},
                **census_schema_properties(),
                "validation_reasoning": {"type": "string"},
            },
            "required": ["valid", *COUNT_FIELDNAMES, "validation_reasoning"],
            "additionalProperties": False,
        },
        "think": False,
        "messages": [
            {"role": "system", "content": validation_prompt},
            {
                "role": "user",
                "content": (
                    "/no_think\n"
                    f"{retry_instruction}"
                    "Validate whether this proposed hype/vagueness count census is reasonable. "
                    "Return exactly one JSON object matching this schema: "
                    '{"valid": boolean, "sentences_total": integer, "sentences_concrete": integer, '
                    '"sentences_vague": integer, "promotional_terms": integer, '
                    '"buzzword_hedge_terms": integer, "distinct_figures": integer, '
                    '"validation_reasoning": string}. '
                    "If the proposed census is reasonable, set valid to true and re-emit the same counts. "
                    "If not, set valid to false and return corrected counts. "
                    "Do not include score, markdown, comments, or extra keys.\n\n"
                    f"PROPOSED_CENSUS:\n{json.dumps(census_result, ensure_ascii=True)}\n\n"
                    f"TEXT:\n{text.strip()}"
                ),
            },
        ],
        "options": {
            "temperature": 0,
            "top_k": 1,
            "top_p": 0.1,
            "seed": 43,
            "num_predict": 192,
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
        raise ModelResponseError(
            "validation",
            ValueError("Ollama returned no validation message content"),
            json.dumps(response_data, ensure_ascii=True),
        )

    try:
        return parse_validation_result(content)
    except (ValueError, TypeError, json.JSONDecodeError, KeyError) as exc:
        raise ModelResponseError("validation", exc, content) from exc


def score_with_retries(
    row: dict[str, str],
    prompt: str,
    validation_prompt: str,
    row_number: int,
) -> dict[str, str]:
    """Score one input row, retrying transient model or parsing failures."""
    last_error: Exception | None = None
    previous_score_error = ""
    previous_validation_error = ""
    text_chars = len(row["item_202_text"])
    active_id = str(row_number)
    validate_score = bool(validation_prompt) and should_validate(row, cfg.VALIDATION_PCT)

    try:
        for attempt in range(cfg.RETRIES + 1):
            try:
                update_monitor_current_row(active_id, row, row_number, text_chars, attempt + 1, "scoring")
                if cfg.TERMINAL_STATUS_EVERY > 0 and row_number % cfg.TERMINAL_STATUS_EVERY == 0:
                    write_terminal_status(
                        f"currently scoring row={row_number} ticker={row['ticker']} "
                        f"filingDate={row['filingDate']} accession={row['accessionNumber']} "
                        f"chars={text_chars} attempt={attempt + 1}/{cfg.RETRIES + 1}"
                    )

                try:
                    census = ollama_score(row["item_202_text"], prompt, previous_score_error)
                    score = vagueness_score(census)
                    reasoning = score_reasoning(census)
                except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
                    previous_score_error = str(exc)
                    raise

                if validate_score:
                    update_monitor_current_row(active_id, row, row_number, text_chars, attempt + 1, "validating")
                    if cfg.TERMINAL_STATUS_EVERY > 0 and row_number % cfg.TERMINAL_STATUS_EVERY == 0:
                        write_terminal_status(
                            f"currently validating row={row_number} ticker={row['ticker']} "
                            f"filingDate={row['filingDate']} accession={row['accessionNumber']} "
                            f"attempt={attempt + 1}/{cfg.RETRIES + 1}"
                        )

                    try:
                        valid, final_census, validation_reasoning = ollama_validate_score(
                            row["item_202_text"],
                            census,
                            validation_prompt,
                            previous_validation_error,
                        )
                        final_census = normalized_census(final_census)
                        original_census = normalized_census(census)
                        corrected = final_census != original_census
                        if not valid and not corrected:
                            raise ValueError("validation marked census invalid but returned unchanged counts")
                        final_score = vagueness_score(final_census)
                        final_reasoning = score_reasoning(final_census)
                    except (
                        urllib.error.URLError,
                        TimeoutError,
                        ValueError,
                        json.JSONDecodeError,
                        KeyError,
                        TypeError,
                    ) as exc:
                        previous_validation_error = str(exc)
                        raise

                    return output_row(
                        row,
                        final_score,
                        final_reasoning,
                        final_census,
                        validation_status="corrected" if corrected else "accepted",
                        validation_reasoning=validation_reasoning,
                        original_score=score if corrected else None,
                        original_reasoning=reasoning if corrected else "",
                    )

                return output_row(row, score, reasoning, census)
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
                last_error = exc
                if attempt >= cfg.RETRIES:
                    break
                clear_terminal_status()
                LOGGER.warning(
                    "retrying row ticker=%s accession=%s next_attempt=%s/%s feedback_error=%s",
                    row["ticker"],
                    row["accessionNumber"],
                    attempt + 2,
                    cfg.RETRIES + 1,
                    exc,
                )
                time.sleep(min(2**attempt, 8))

        raise ScoringFailure(cfg.RETRIES + 1, last_error) from last_error
    finally:
        clear_monitor_current_row(active_id)
