from __future__ import annotations

import inspect
import unittest

from src.scoring import WEIGHTS, estimate_kappa, vagueness_score, vagueness_score_v2


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


class VaguenessScoreV2Test(unittest.TestCase):
    def test_signature_requires_global_kappa_before_weights(self) -> None:
        parameters = list(inspect.signature(vagueness_score_v2).parameters)

        self.assertEqual(parameters, ["census", "kappa", "weights"])

    def test_weights_are_convex(self) -> None:
        self.assertEqual(sum(WEIGHTS.values()), 1.0)

    def test_quantified_filing_scores_low_and_promotional_filing_scores_high(self) -> None:
        kappa = {"promotional": 0.05, "hedging": 0.05, "figure": 8.0}
        concrete = {
            "sentences_total": 20,
            "sentences_concrete": 18,
            "sentences_vague": 2,
            "promotional_terms": 1,
            "buzzword_hedge_terms": 0,
            "distinct_figures": 25,
        }
        promo = {
            "sentences_total": 20,
            "sentences_concrete": 3,
            "sentences_vague": 17,
            "promotional_terms": 12,
            "buzzword_hedge_terms": 9,
            "distinct_figures": 1,
        }

        self.assertEqual(vagueness_score_v2(concrete, kappa), 18.6)
        self.assertLess(vagueness_score_v2(concrete, kappa), 25.0)
        self.assertEqual(vagueness_score_v2(promo, kappa), 87.8)
        self.assertGreater(vagueness_score_v2(promo, kappa), 80.0)

    def test_final_score_is_bounded_without_final_clipping(self) -> None:
        kappa = {"promotional": 0.05, "hedging": 0.05, "figure": 8.0}
        census = {
            "sentences_total": 1,
            "sentences_concrete": 0,
            "sentences_vague": 1,
            "promotional_terms": 1_000_000,
            "buzzword_hedge_terms": 1_000_000,
            "distinct_figures": 0,
        }

        self.assertEqual(vagueness_score_v2(census, kappa), 100.0)

    def test_zero_denominator_guards_handle_misconfigured_kappa(self) -> None:
        kappa = {"promotional": 0.0, "hedging": 0.0, "figure": 0.0}
        census = {
            "sentences_total": 0,
            "sentences_concrete": 0,
            "sentences_vague": 0,
            "promotional_terms": 0,
            "buzzword_hedge_terms": 0,
            "distinct_figures": 0,
        }

        self.assertEqual(vagueness_score_v2(census, kappa), 20.0)

    def test_estimate_kappa_uses_corpus_medians(self) -> None:
        censuses = [
            {
                "sentences_total": 10,
                "sentences_concrete": 8,
                "sentences_vague": 2,
                "promotional_terms": 1,
                "buzzword_hedge_terms": 0,
                "distinct_figures": 2,
            },
            {
                "sentences_total": 20,
                "sentences_concrete": 10,
                "sentences_vague": 10,
                "promotional_terms": 4,
                "buzzword_hedge_terms": 2,
                "distinct_figures": 8,
            },
            {
                "sentences_total": 0,
                "sentences_concrete": 0,
                "sentences_vague": 0,
                "promotional_terms": 3,
                "buzzword_hedge_terms": 1,
                "distinct_figures": 25,
            },
        ]

        self.assertEqual(
            estimate_kappa(censuses),
            {"promotional": 0.2, "hedging": 0.1, "figure": 8},
        )
