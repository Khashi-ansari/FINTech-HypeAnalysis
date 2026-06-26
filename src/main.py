from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pipeline import run_score
else:
    from .pipeline import run_score


def main() -> None:
    """Run the scoring pipeline."""
    if len(sys.argv) > 1:
        raise SystemExit("Use: python src/main.py")

    run_score()


if __name__ == "__main__":
    main()
