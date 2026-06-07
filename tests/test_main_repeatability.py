from __future__ import annotations

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from src import repeatability as repeatability_module


class RepeatabilityTest(unittest.TestCase):
    def write_input_csv(self, directory: str) -> Path:
        """Create a one-row input CSV with the item_202_text column."""
        input_csv = Path(directory) / "item202_clean.csv"
        with input_csv.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "ticker",
                    "cik",
                    "filingDate",
                    "accessionNumber",
                    "source",
                    "item_202_text",
                    "char_count",
                ],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "ticker": "AMD",
                    "cik": "2488",
                    "filingDate": "2018-01-30",
                    "accessionNumber": "0000002488-18-000014",
                    "source": "body_701_fallback",
                    "item_202_text": "Same Item 2.02 text used for every repeatability run.",
                    "char_count": "58",
                }
            )
        return input_csv

    def test_same_item_202_text_scores_consistently(self) -> None:
        """The repeatability test passes when every parse returns the same score."""
        with tempfile.TemporaryDirectory() as directory:
            input_csv = self.write_input_csv(directory)
            seen_texts: list[str] = []

            def fake_score(text: str, prompt: str) -> tuple[float, str]:
                seen_texts.append(text)
                self.assertEqual(prompt, "test prompt")
                return 4.25, "The disclosure contains limited promotional language."

            with (
                patch.object(repeatability_module.cfg, "INPUT_CSV", input_csv),
                patch.object(repeatability_module, "load_prompt", return_value="test prompt"),
                patch.object(repeatability_module, "ollama_score", side_effect=fake_score),
                redirect_stdout(io.StringIO()),
            ):
                scores = repeatability_module.run_repeatability_test(row_number=1, runs=3)

        self.assertEqual(scores, ["4.2500", "4.2500", "4.2500"])
        self.assertEqual(
            seen_texts,
            ["Same Item 2.02 text used for every repeatability run."] * 3,
        )

    def test_same_item_202_text_fails_on_score_drift(self) -> None:
        """The repeatability test fails when repeated parses produce different scores."""
        with tempfile.TemporaryDirectory() as directory:
            input_csv = self.write_input_csv(directory)

            with (
                patch.object(repeatability_module.cfg, "INPUT_CSV", input_csv),
                patch.object(repeatability_module, "load_prompt", return_value="test prompt"),
                patch.object(
                    repeatability_module,
                    "ollama_score",
                    side_effect=[
                        (4.25, "The disclosure contains limited promotional language."),
                        (4.25, "The disclosure contains limited promotional language."),
                        (4.5, "The disclosure contains moderate promotional language."),
                    ],
                ),
                redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(AssertionError, "same item_202_text returned scores"),
            ):
                repeatability_module.run_repeatability_test(row_number=1, runs=3)


if __name__ == "__main__":
    unittest.main()
