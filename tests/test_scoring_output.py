from __future__ import annotations

import unittest

from src.ollama_scorer import parse_score_result
from src.rows import OUTPUT_FIELDNAMES, output_row


class ScoringOutputTest(unittest.TestCase):
    def test_parse_score_result_returns_score_and_reasoning(self) -> None:
        score, reasoning = parse_score_result(
            '{"score": 47.34567, "reasoning": "The disclosure has numbers but also unsupported momentum claims."}'
        )

        self.assertEqual(score, 47.3457)
        self.assertEqual(reasoning, "The disclosure has numbers but also unsupported momentum claims.")

    def test_output_row_includes_reasoning_column(self) -> None:
        row = {
            "ticker": "AMD",
            "cik": "2488",
            "filingDate": "2018-01-30",
            "accessionNumber": "0000002488-18-000014",
            "source": "body_701_fallback",
        }

        result = output_row(row, 47.3, "Concrete figures are mixed with unsupported claims.")

        self.assertEqual(
            OUTPUT_FIELDNAMES,
            ["ticker", "cik", "filingDate", "accessionNumber", "source", "score", "reasoning"],
        )
        self.assertEqual(result["score"], "47.3000")
        self.assertEqual(result["reasoning"], "Concrete figures are mixed with unsupported claims.")


if __name__ == "__main__":
    unittest.main()
