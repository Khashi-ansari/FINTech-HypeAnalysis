from __future__ import annotations

import unittest
from unittest.mock import patch

from src import main as main_module


class MainRunModeTest(unittest.TestCase):
    def test_main_runs_score_pipeline(self) -> None:
        with (
            patch.object(main_module.sys, "argv", ["main.py"]),
            patch.object(main_module, "run_score") as run_score,
        ):
            main_module.main()

        run_score.assert_called_once_with()

    def test_main_rejects_arguments(self) -> None:
        with (
            patch.object(main_module.sys, "argv", ["main.py", "score"]),
            patch.object(main_module, "run_score") as run_score,
            self.assertRaisesRegex(SystemExit, "python src/main.py"),
        ):
            main_module.main()

        run_score.assert_not_called()


if __name__ == "__main__":
    unittest.main()
