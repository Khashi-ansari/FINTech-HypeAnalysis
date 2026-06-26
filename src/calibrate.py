from __future__ import annotations

import csv
from pathlib import Path

try:
    from . import config as cfg
except ImportError:  # Allows running via python src/calibrate.py
    import config as cfg


BIN_WIDTH = 5
BIN_COUNT = 100 // BIN_WIDTH


def read_scores(path: Path) -> list[float]:
    """Read score values from an output CSV."""
    scores: list[float] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            value = row.get("score", "").strip()
            if not value:
                continue
            scores.append(float(value))
    return scores


def score_histogram(scores: list[float]) -> list[int]:
    """Return counts in 5-point bins from 0-5 through 95-100."""
    bins = [0] * BIN_COUNT
    for score in scores:
        clamped = max(0.0, min(100.0, score))
        index = min(int(clamped // BIN_WIDTH), BIN_COUNT - 1)
        bins[index] += 1
    return bins


def empty_regions(bins: list[int]) -> list[tuple[int, int]]:
    """Return consecutive empty score ranges as lower/upper integer bounds."""
    regions: list[tuple[int, int]] = []
    start: int | None = None
    for index, count in enumerate(bins):
        if count == 0 and start is None:
            start = index
        elif count > 0 and start is not None:
            regions.append((start * BIN_WIDTH, index * BIN_WIDTH))
            start = None
    if start is not None:
        regions.append((start * BIN_WIDTH, 100))
    return regions


def print_diagnostic(path: Path = cfg.OUTPUT_CSV) -> None:
    """Print a stdlib-only calibration diagnostic for the live output CSV."""
    if not path.exists():
        raise FileNotFoundError(f"output CSV not found: {path}")

    scores = read_scores(path)
    print(f"score calibration diagnostic: path={path} rows={len(scores)}")
    if not scores:
        print("no score values found")
        return

    bins = score_histogram(scores)
    for index, count in enumerate(bins):
        lower = index * BIN_WIDTH
        upper = lower + BIN_WIDTH
        if index == BIN_COUNT - 1:
            label = f"{lower:03d}-100"
        else:
            label = f"{lower:03d}-{upper:03d}"
        print(f"{label}: {count}")

    regions = empty_regions(bins)
    if regions:
        for lower, upper in regions:
            print(f"empty region: {lower}-{upper}")
    else:
        print("empty region: none")


if __name__ == "__main__":
    print_diagnostic()
