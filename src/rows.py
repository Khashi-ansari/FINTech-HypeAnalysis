from __future__ import annotations

try:
    from .scoring import COUNT_FIELDNAMES, normalized_census
except ImportError:  # Allows running via python src/main.py
    from scoring import COUNT_FIELDNAMES, normalized_census

METADATA_FIELDNAMES = ["ticker", "cik", "filingDate", "accessionNumber", "source"]
VALIDATION_FIELDNAMES = ["validation_status", "validation_reasoning", "original_score", "original_reasoning"]
OUTPUT_FIELDNAMES = [*METADATA_FIELDNAMES, "score", "reasoning", *VALIDATION_FIELDNAMES, *COUNT_FIELDNAMES]
ERROR_DIAGNOSTIC_FIELDNAMES = [
    "error_stage",
    "error_type",
    "error_message",
    "prompt_target",
    "model_output_preview",
]
ERROR_FIELDNAMES = [*METADATA_FIELDNAMES, *ERROR_DIAGNOSTIC_FIELDNAMES, "error"]


def row_key(row: dict[str, str]) -> str:
    """Build the stable resume key from the filing metadata columns."""
    return "\x1f".join(row[field] for field in METADATA_FIELDNAMES)


def metadata(row: dict[str, str]) -> dict[str, str]:
    """Extract the metadata fields that are copied from input rows to output rows."""
    return {field: row[field] for field in METADATA_FIELDNAMES}


def output_row(
    row: dict[str, str],
    score: float,
    reasoning: str | None,
    census: dict[str, int],
    validation_status: str = "not_selected",
    validation_reasoning: str | None = "",
    original_score: float | None = None,
    original_reasoning: str | None = "",
) -> dict[str, str]:
    """Build one score output row from metadata, score, validation fields, and counts."""
    count_fields = {field: str(value) for field, value in normalized_census(census).items()}
    return {
        **metadata(row),
        "score": f"{score:.4f}",
        "reasoning": reasoning or "",
        "validation_status": validation_status,
        "validation_reasoning": validation_reasoning or "",
        "original_score": "" if original_score is None else f"{original_score:.4f}",
        "original_reasoning": original_reasoning or "",
        **count_fields,
    }
