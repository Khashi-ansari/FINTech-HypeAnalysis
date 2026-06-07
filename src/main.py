from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pipeline import run_score
    from repeatability import run_repeatability_test
else:
    from .pipeline import run_score
    from .repeatability import run_repeatability_test


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the pipeline entry point."""
    parser = argparse.ArgumentParser(description="Run or test the Item 2.02 hype/vagueness scorer.")
    parser.add_argument(
        "--repeatability-test",
        action="store_true",
        help="Score the same item_202_text multiple times and fail if the rounded scores differ.",
    )
    parser.add_argument(
        "--repeatability-runs",
        type=int,
        default=3,
        help="Number of repeated scoring calls for --repeatability-test.",
    )
    parser.add_argument(
        "--repeatability-row",
        type=int,
        default=1,
        help="1-based input CSV row number to use for --repeatability-test.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Entry point for running the configured scoring pipeline."""
    args = parse_args(argv)

    if args.repeatability_test:
        try:
            run_repeatability_test(args.repeatability_row, args.repeatability_runs)
        except (AssertionError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        return

    run_score()


if __name__ == "__main__":
    main()
