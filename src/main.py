from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pipeline import run_score
else:
    from .pipeline import run_score


def main() -> None:
    """Entry point for running the configured scoring pipeline."""
    run_score()


if __name__ == "__main__":
    main()
