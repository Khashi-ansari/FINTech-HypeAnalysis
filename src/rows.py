from __future__ import annotations


def row_key(row: dict[str, str]) -> str:
    """Build the stable resume key from the filing metadata columns."""
    return "\x1f".join([row["ticker"], row["cik"], row["filingDate"], row["accessionNumber"], row["source"]])


def metadata(row: dict[str, str]) -> dict[str, str]:
    """Extract the metadata fields that are copied from input rows to output rows."""
    return {
        "ticker": row["ticker"],
        "cik": row["cik"],
        "filingDate": row["filingDate"],
        "accessionNumber": row["accessionNumber"],
        "source": row["source"],
    }
