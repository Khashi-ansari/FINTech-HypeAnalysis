from __future__ import annotations

METADATA_FIELDNAMES = ["ticker", "cik", "filingDate", "accessionNumber", "source"]
OUTPUT_FIELDNAMES = [*METADATA_FIELDNAMES, "score", "reasoning"]
ERROR_FIELDNAMES = [*METADATA_FIELDNAMES, "error"]


def row_key(row: dict[str, str]) -> str:
    """Build the stable resume key from the filing metadata columns."""
    return "\x1f".join(row[field] for field in METADATA_FIELDNAMES)


def metadata(row: dict[str, str]) -> dict[str, str]:
    """Extract the metadata fields that are copied from input rows to output rows."""
    return {field: row[field] for field in METADATA_FIELDNAMES}


def output_row(row: dict[str, str], score: float, reasoning: str) -> dict[str, str]:
    """Build one score output row including the model's reasoning sentence."""
    return {**metadata(row), "score": f"{score:.4f}", "reasoning": reasoning}
