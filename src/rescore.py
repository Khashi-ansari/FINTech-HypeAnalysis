from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Callable

import pandas as pd

try:
    from . import config as cfg
    from .scoring import (
        COUNT_FIELDNAMES,
        estimate_kappa,
        score_breakdown,
        vagueness_score,
        vagueness_score_v2,
    )
except ImportError:
    import config as cfg
    from scoring import (
        COUNT_FIELDNAMES,
        estimate_kappa,
        score_breakdown,
        vagueness_score,
        vagueness_score_v2,
    )


DEFAULT_RESCORED_CSV = cfg.OUTPUT_CSV.with_name(f"{cfg.OUTPUT_CSV.stem}_rescored_v2{cfg.OUTPUT_CSV.suffix}")
DEFAULT_RESCORED_XLSX = DEFAULT_RESCORED_CSV.with_suffix(".xlsx")


def row_census(row: dict[str, str]) -> dict[str, int]:
    """Extract the six scoring counts from one output CSV row."""
    return {field: int(row[field]) for field in COUNT_FIELDNAMES}


def read_censuses(path: Path) -> list[dict[str, int]]:
    """Read all census rows needed to freeze one corpus-level kappa."""
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return [row_census(row) for row in csv.DictReader(source)]


def convert_csv_to_excel(csv_path: Path, xlsx_path: Path) -> None:
    """Convert a CSV file into an .xlsx workbook."""
    dataframe = pd.read_csv(Path(csv_path), dtype=str, keep_default_na=False)
    dataframe.to_excel(Path(xlsx_path), index=False, sheet_name="rescored", engine="openpyxl")


def add_score_column(
    in_path: Path,
    out_path: Path,
    name: str,
    formula: Callable[[dict[str, int]], float],
    drop_columns: tuple[str, ...] = (),
) -> None:
    """Calculate a new final score from existing census columns."""
    in_path = Path(in_path)
    out_path = Path(out_path)
    temporary = out_path.with_name(f".{out_path.name}.tmp")
    try:
        with in_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fields = reader.fieldnames or []
            if name in fields:
                raise ValueError(f"column already exists: {name}")
            output_fields = [field for field in fields if field not in drop_columns]

            with temporary.open("w", encoding="utf-8", newline="") as target:
                writer = csv.DictWriter(target, fieldnames=[*output_fields, name], extrasaction="ignore")
                writer.writeheader()
                for row in reader:
                    census = row_census(row)
                    row[name] = f"{formula(census):.4f}"
                    writer.writerow(row)
        os.replace(temporary, out_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def alternative_score(c: dict[str, int]) -> float:
    n = max(c["sentences_total"], 1)
    return 100 * c["sentences_vague"] / n


def unclipped_score(census: dict[str, int], weights: dict[str, float] | None = None) -> float:
    """Use the pipeline formula without clipping component densities or final score."""
    w = cfg.WEIGHTS if weights is None else weights
    breakdown = score_breakdown(census)
    base = 100.0 * breakdown["vague_frac"]
    base += 100.0 * breakdown["promo_density"] * w["promo"]
    base -= 100.0 * breakdown["figure_density"] * w["figure"]
    return round(base, 1)


def current_score(census: dict[str, int]) -> float:
    """Use the same formula as the initial scoring pipeline."""
    return vagueness_score(census)


def add_v2_score_column(
    in_path: Path,
    out_path: Path,
    name: str = "rescored_vagueness_v2",
) -> dict[str, float]:
    """Add a v2 score column using one kappa estimated from the full input CSV."""
    censuses = read_censuses(Path(in_path))
    if not censuses:
        raise ValueError(f"cannot rescore empty CSV: {in_path}")

    kappa = estimate_kappa(censuses)
    add_score_column(
        Path(in_path),
        Path(out_path),
        name,
        lambda census: vagueness_score_v2(census, kappa),
        drop_columns=("score",),
    )
    return kappa


if __name__ == "__main__":
    frozen_kappa = add_v2_score_column(cfg.OUTPUT_CSV, DEFAULT_RESCORED_CSV)
    convert_csv_to_excel(DEFAULT_RESCORED_CSV, DEFAULT_RESCORED_XLSX)
    print(f"wrote {DEFAULT_RESCORED_CSV}")
    print(f"wrote {DEFAULT_RESCORED_XLSX}")
    print(f"frozen kappa: {frozen_kappa}")
