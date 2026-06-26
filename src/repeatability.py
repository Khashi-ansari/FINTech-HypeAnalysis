from __future__ import annotations

import csv

try:
    from . import config as cfg
    from .ollama_scorer import ollama_score
    from .pipeline import load_prompt
    from .scoring import vagueness_score
except ImportError:  # Allows running via python src/main.py
    import config as cfg
    from ollama_scorer import ollama_score
    from pipeline import load_prompt
    from scoring import vagueness_score


def read_item_202_text(row_number: int) -> tuple[dict[str, str], str]:
    """Read one item_202_text value from the configured input CSV."""
    if row_number < 1:
        raise ValueError("--repeatability-row must be 1 or greater")

    with cfg.INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for current_row_number, row in enumerate(reader, start=1):
            if current_row_number == row_number:
                text = row.get("item_202_text", "").strip()
                if not text:
                    raise ValueError(f"row {row_number} has an empty item_202_text value")
                return row, text

    raise ValueError(f"input CSV has no row {row_number}: {cfg.INPUT_CSV}")


def run_repeatability_test(row_number: int, runs: int) -> list[str]:
    """Score one Item 2.02 text repeatedly and require identical rounded scores."""
    if runs < 2:
        raise ValueError("--repeatability-runs must be 2 or greater")

    row, text = read_item_202_text(row_number)
    prompt = load_prompt()
    scores: list[str] = []

    print(
        "repeatability test row={row_number} ticker={ticker} filingDate={filingDate} accession={accession}".format(
            row_number=row_number,
            ticker=row.get("ticker", ""),
            filingDate=row.get("filingDate", ""),
            accession=row.get("accessionNumber", ""),
        )
    )

    for run_number in range(1, runs + 1):
        census = ollama_score(text, prompt)
        raw_score = vagueness_score(census)
        score = f"{raw_score:.4f}"
        scores.append(score)
        print(f"repeatability run {run_number}/{runs} score={score}")

    if len(set(scores)) != 1:
        raise AssertionError(f"repeatability test failed: same item_202_text returned scores {scores}")

    print(f"repeatability test passed: score={scores[0]} runs={runs}")
    return scores
