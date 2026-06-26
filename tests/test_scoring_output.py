from __future__ import annotations

import unittest
from unittest.mock import patch

from src import ollama_scorer as scorer_module
from src.ollama_scorer import parse_census_result, parse_validation_result, should_validate
from src.rows import ERROR_FIELDNAMES, OUTPUT_FIELDNAMES, output_row


class ScoringOutputTest(unittest.TestCase):
    def filing_row(self) -> dict[str, str]:
        return {
            "ticker": "AMD",
            "cik": "2488",
            "filingDate": "2018-01-30",
            "accessionNumber": "0000002488-18-000014",
            "source": "body_701_fallback",
            "item_202_text": "Revenue rose 8%, and management described robust momentum without more support.",
        }

    def test_parse_census_result_accepts_valid_counts(self) -> None:
        content = '{"sentences_total": 2, "sentences_concrete": 1, "sentences_vague": 1, "promotional_terms": 1, "buzzword_hedge_terms": 1, "distinct_figures": 1}'
        census = parse_census_result(content)
        self.assertEqual(census["sentences_total"], 2)
        self.assertEqual(census["distinct_figures"], 1)

    def test_parse_census_result_rejects_missing_keys(self) -> None:
        content = '{"sentences_total": 2, "sentences_concrete": 1}'
        with self.assertRaises(ValueError):
            parse_census_result(content)

    def test_parse_census_result_rejects_negative_values(self) -> None:
        content = '{"sentences_total": 2, "sentences_concrete": -1, "sentences_vague": 1, "promotional_terms": 0, "buzzword_hedge_terms": 0, "distinct_figures": 0}'
        with self.assertRaises(ValueError):
            parse_census_result(content)

    def test_output_row_includes_new_count_columns(self) -> None:
        row = self.filing_row()
        census = {
            "sentences_total": 2,
            "sentences_concrete": 1,
            "sentences_vague": 1,
            "promotional_terms": 1,
            "buzzword_hedge_terms": 0,
            "distinct_figures": 1,
        }
        result = output_row(row, 47.3, "Concrete figures are mixed with unsupported claims.", census)

        self.assertTrue(OUTPUT_FIELDNAMES[-6:] == [
            "sentences_total",
            "sentences_concrete",
            "sentences_vague",
            "promotional_terms",
            "buzzword_hedge_terms",
            "distinct_figures",
        ])

        self.assertEqual(result["score"], "47.3000")
        self.assertEqual(result["reasoning"], "Concrete figures are mixed with unsupported claims.")
        self.assertEqual(result["validation_status"], "not_selected")
        self.assertEqual(result["sentences_total"], "2")
        self.assertEqual(result["distinct_figures"], "1")

    def test_error_fields_include_diagnostic_columns(self) -> None:
        self.assertEqual(
            ERROR_FIELDNAMES,
            [
                "ticker",
                "cik",
                "filingDate",
                "accessionNumber",
                "source",
                "error_stage",
                "error_type",
                "error_message",
                "prompt_target",
                "model_output_preview",
                "error",
            ],
        )

    def test_parse_validation_result_handles_corrections(self) -> None:
        content = (
            '{"valid": false, "sentences_total": 3, "sentences_concrete": 0, '
            '"sentences_vague": 3, "promotional_terms": 2, "buzzword_hedge_terms": 1, "distinct_figures": 0, '
            '"validation_reasoning": "Counts adjusted to include an extra vague sentence."}'
        )
        valid, census, validation_reasoning = parse_validation_result(content)
        self.assertFalse(valid)
        self.assertEqual(census["sentences_total"], 3)
        self.assertEqual(validation_reasoning.endswith("."), True)

    def test_should_validate_respects_boundary_percentages(self) -> None:
        row = self.filing_row()
        self.assertFalse(should_validate(row, 0.0))
        self.assertTrue(should_validate(row, 1.0))

    def test_score_with_retries_uses_validation_correction(self) -> None:
        row = self.filing_row()
        original_census = {
            "sentences_total": 2,
            "sentences_concrete": 1,
            "sentences_vague": 1,
            "promotional_terms": 0,
            "buzzword_hedge_terms": 0,
            "distinct_figures": 1,
        }
        corrected_census = {
            "sentences_total": 2,
            "sentences_concrete": 0,
            "sentences_vague": 2,
            "promotional_terms": 2,
            "buzzword_hedge_terms": 0,
            "distinct_figures": 0,
        }

        with (
            patch.object(scorer_module.cfg, "VALIDATION_PCT", 1.0),
            patch.object(scorer_module.cfg, "TERMINAL_STATUS_EVERY", 0),
            patch.object(scorer_module, "ollama_score", return_value=original_census),
            patch.object(
                scorer_module,
                "ollama_validate_score",
                return_value=(False, corrected_census, "Counts corrected: added promotional terms."),
            ),
        ):
            result = scorer_module.score_with_retries(row, "score prompt", "validation prompt", row_number=1)

        self.assertEqual(result["validation_status"], "corrected")
        self.assertEqual(result["original_score"], "{:.4f}".format(scorer_module.vagueness_score(original_census)))
        self.assertEqual(result["original_reasoning"], scorer_module.score_reasoning(original_census))


if __name__ == "__main__":
    unittest.main()
