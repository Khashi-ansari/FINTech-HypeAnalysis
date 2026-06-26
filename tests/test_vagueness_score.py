from __future__ import annotations

import unittest

from src.scoring import vagueness_score


class VaguenessScoreTest(unittest.TestCase):
    def test_all_concrete_low_score(self) -> None:
        census = {
            "sentences_total": 10,
            "sentences_concrete": 10,
            "sentences_vague": 0,
            "promotional_terms": 0,
            "buzzword_hedge_terms": 0,
            "distinct_figures": 10,
        }
        score = vagueness_score(census)
        self.assertLess(score, 10.0)

    def test_all_promo_high_score(self) -> None:
        census = {
            "sentences_total": 10,
            "sentences_concrete": 0,
            "sentences_vague": 10,
            "promotional_terms": 20,
            "buzzword_hedge_terms": 5,
            "distinct_figures": 0,
        }
        score = vagueness_score(census)
        self.assertGreater(score, 80.0)

    def test_mixed_not_multiple_of_25(self) -> None:
        census = {
            "sentences_total": 4,
            "sentences_concrete": 1,
            "sentences_vague": 3,
            "promotional_terms": 2,
            "buzzword_hedge_terms": 1,
            "distinct_figures": 1,
        }
        score = vagueness_score(census)
        self.assertNotEqual(score % 25, 0)

    def test_clamping_and_rounding(self) -> None:
        census = {
            "sentences_total": 1,
            "sentences_concrete": 0,
            "sentences_vague": 1,
            "promotional_terms": 100,
            "buzzword_hedge_terms": 0,
            "distinct_figures": 0,
        }
        score = vagueness_score(census)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)
        self.assertEqual(round(score, 1), score)
