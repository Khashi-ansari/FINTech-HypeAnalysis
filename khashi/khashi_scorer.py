# -*- coding: utf-8 -*-
"""
Khashi HypeScore Scorer v3 — Weighted Sentence Coverage (WSC) Methodology
==========================================================================

REQUIRES (run once):
    pip install spacy --break-system-packages
    python -m spacy download en_core_web_sm

METHODOLOGY:
  Six word categories [A]-[F] are detected via spaCy PhraseMatcher using a
  tiered vocabulary (300+ phrases total). Matches are aggregated into per-category
  Weighted Sentence Coverage (WSC) scores:

      WSC_X = Σ [ MAX_tier_weight(sentence_i) × position_weight_i ] / n_sentences

  Positional weights:  first third of text = 2.0 | middle = 1.0 | last third = 0.5
  Tier weights:        Tier 1 = 3  |  Tier 2 = 2  |  Tier 3 = 1
  MAX not SUM per sentence — prevents single hype-dense paragraph from dominating.

  QO / SA       → derived from WSC values via genre-calibrated formulas (no LLM).
  CL            → sentence-level Loughran-McDonald modal word lists (guidance precision).
  SE            → deterministic Python D1-D6 signals (GAAP/non-GAAP emphasis, burial).
  HypeScore     = QO + SE + CL + SA  (0-100)
  Genre baseline: 9-13 per component = average S&P 500 8-K.

THEORETICAL GROUNDING:
  [A] Superlatives + [B] Vague Positives → QO  (Qualitative Optimism — WHAT language)
  [C] Certainty / [D] Hedges            → CL  (Certainty Language — guidance framing)
  [E] Internal / [F] External           → SA  (Self-Attribution Bias — WHY language)
  GAAP/Non-GAAP ordering                → SE  (Selective Emphasis — narrative structure)
"""

from __future__ import annotations

import csv
import json
import os
import random
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

try:
    import spacy
    from spacy.matcher import PhraseMatcher
except ImportError:
    raise SystemExit(
        "\n[ERROR] spaCy not installed.\n"
        "  pip install spacy --break-system-packages\n"
        "  python -m spacy download en_core_web_sm\n"
    )


# ─── CONFIGURATION ────────────────────────────────────────────────────────────
INPUT_CSV      = r"C:\Users\khash\OneDrive\Desktop\Agents\item202_clean.csv"
# STAGE: FULL RUN — v3 rev3 methodology is validated (3 stress-test rounds on 1000
# rows, converged: corr with v2 rose 0.932->0.940->0.949 across fixes, mean |rank
# shift| fell 85->80->75). Scoring ALL 14,351 filings now. Both hype_score (v2) and
# hype_v3 (rev3, 8-criterion) are written so the team can compare/choose in the paper.
OUTPUT_CSV     = r"C:\Users\khash\Desktop\FINTech_HypeAnalysis\khashi\outputs\khashi_hype_full_14351.csv"
ERRORS_CSV     = r"C:\Users\khash\Desktop\FINTech_HypeAnalysis\khashi\outputs\khashi_hype_full_14351_errors.csv"

SAMPLE_SIZE    = 0     # 0 = use all rows — full 14,351-row dataset
RANDOM_SEED    = 42    # kept for consistency; irrelevant once SAMPLE_SIZE=0 (no sampling)
# WORD LIMITS — two separate windows:
#   SPACY_MAX_WORDS : used for ALL Python-based scoring (A/B/C/D/E/F via WSC).
#                     Set to 0 for full text (spaCy is fast enough). Use 3000 to
#                     capture the full press release body (guidance + segments).
#   LLM_MAX_WORDS   : used only for the SE LLM call. Keep at 1000 for speed/cost.
#                     SE (selective emphasis) is detectable from the summary alone.
SPACY_MAX_WORDS = 3000     # 0 = full text. Captures guidance section + attribution.
LLM_MAX_WORDS   = 1000     # Kept small — SE is visible in the opening paragraphs.
MIN_WORDS      = 500   # Raised from 200 — wrapper filings (pure boilerplate) are 200-400 words
MODEL          = "qwen3:8b"
OLLAMA_URL     = "http://localhost:11434"
RETRIES        = 3
TIMEOUT        = 180
CONCURRENCY    = 1         # on CPU: keep at 1 (Ollama serialises anyway). Use multiple laptops for scale.
RESUME         = True    # Full run: safe to interrupt/resume across sessions without rescoring rows
PROGRESS_EVERY = 10

# WSC formula calibration (adjust if genre baseline drifts)
# QO: score = clamp(round(9 + (combined - 0.15) * QO_SLOPE), 2, 25)
#     Reference 0.15: typical 8-K opens with some superlatives; wsc_b often ~0 for
#     figure-backed filings (anchor suppression). Combined range ≈ 0–0.5.
# SA: ratio-based: wsc_e / (wsc_e + wsc_f) × 25, genre floor 9 when total < 0.02
QO_SLOPE = 25.0  # Reference 0.15; slope 25 → 0.30 combined → score 12.75≈13 (record margins)
SA_SLOPE = 15.0  # Kept for reference — SA now uses ratio formula, not delta

# ── Loughran-McDonald (2011) word lists ───────────────────────────────────────
# Standard financial NLP dictionaries (~4,000 citations).
# Used for sentence-level CL scoring — stronger academic grounding than custom VOCAB_C/D.
#
# Strong Modal: absolute commitment / certainty framing
LM_STRONG_MODAL: frozenset[str] = frozenset({
    "will", "shall", "must", "always", "never",
    "certainly", "definitely", "clearly", "undoubtedly", "unambiguously",
    "assuredly", "necessarily", "indisputably",
})

# Weak Modal: hedging / qualification framing
LM_WEAK_MODAL: frozenset[str] = frozenset({
    "may", "might", "could", "would", "possibly", "potentially",
    "perhaps", "probably", "presumably", "approximately", "roughly",
    "nearly", "often", "sometimes", "occasionally", "typically",
    "generally", "usually", "estimated", "seldom", "somewhat",
})

# Uncertainty words: explicit acknowledgement of unpredictability
LM_UNCERTAINTY: frozenset[str] = frozenset({
    "uncertain", "uncertainty", "unpredictable", "unclear", "indefinite",
    "contingent", "variable", "depends", "depend", "unforeseen",
    "volatile", "volatility", "fluctuate", "fluctuation", "subject",
})

# Guidance verbs: forward-looking verbs inherent to SEC guidance language
GUIDANCE_VERBS: frozenset[str] = frozenset({
    "expect", "expects", "expected", "anticipate", "anticipates", "anticipated",
    "forecast", "forecasts", "projected", "projects", "project",
    "estimate", "estimates", "estimated", "guide", "guided", "guidance",
    "target", "targets", "targeted", "outlook",
})
# ──────────────────────────────────────────────────────────────────────────────


# ═══════════════════════════════════════════════════════════════════════════════
# VOCABULARY DICTIONARIES
# phrase (lowercase) → tier weight (1, 2, or 3)
# ═══════════════════════════════════════════════════════════════════════════════

# ── [A] Strong Superlatives ──────────────────────────────────────────────────
# Unambiguous marketing superlatives; anchor check does NOT apply (a record is
# a record even when quantified). Exclusion list handles polysemy.

VOCAB_A: dict[str, int] = {
    # Tier 1 — absolute, unambiguous superlatives (weight 3)
    "record":                   3,  "record-breaking":          3,
    "record-setting":           3,  "all-time high":            3,
    "all-time best":            3,  "all-time record":          3,
    "best ever":                3,  "best-ever":                3,
    "unprecedented":            3,  "unparalleled":             3,
    "unmatched":                3,  "unrivaled":                3,
    "unsurpassed":              3,  "transformational":         3,
    "transformative":           3,  "revolutionary":            3,
    "groundbreaking":           3,  "pioneering":               3,
    "paradigm-shifting":        3,  "game-changing":            3,
    "world-class":              3,  "world class":              3,
    "world-leading":            3,  "world leading":            3,
    "industry-leading":         3,  "industry leading":         3,
    "market-leading":           3,  "market leading":           3,
    "best-in-class":            3,  "best in class":            3,
    "best-in-breed":            3,  "best in breed":            3,
    "top-tier":                 3,  "top tier":                 3,
    "first-of-its-kind":        3,  "first of its kind":        3,
    "landmark":                 3,  "historic milestone":       3,
    "never-before":             3,  "exceptional":              3,
    "extraordinary":            3,  "outstanding":              3,
    "phenomenal":               3,  "stellar":                  3,
    "never been stronger":      3,  "never been better":        3,
    "never been higher":        3,  "never been greater":       3,
    "best quarter ever":        3,  "best year ever":           3,
    "best in history":          3,  "highest ever":             3,
    "largest ever":             3,  "fastest ever":             3,
    "lowest ever":              3,
    # Tier 2 — strong signals (weight 2)
    "remarkable":               2,  "impressive":               2,
    "exemplary":                2,  "distinguished":            2,
    "elite":                    2,  "premium":                  2,
    "cutting-edge":             2,  "cutting edge":             2,
    "state-of-the-art":         2,  "state of the art":         2,
    "breakout":                 2,  "breakthrough":             2,
    "class-leading":            2,  "class leading":            2,
    "top-performing":           2,  "top performing":           2,
    "best-performing":          2,  "best performing":          2,
    "outperformed":             2,  "differentiated platform":  2,
    "differentiated model":     2,
    # Tier 3 — contextual signals (weight 1)
    "notable":                  1,  "significant achievement":  1,
    "strong momentum":          1,  "milestone":                1,
    "historic":                 1,
}

# Exclusion contexts: if the matched word appears adjacent to these, suppress.
# Keys are the FIRST word of the matched phrase.
A_EXCLUSIONS: dict[str, list[str]] = {
    "outstanding":  ["outstanding balance", "outstanding shares", "outstanding debt",
                     "outstanding loan", "outstanding obligation", "outstanding borrowings",
                     "outstanding principal", "outstanding amount", "outstanding notes",
                     "outstanding warrants"],
    "record":       ["track record", "on the record", "for the record", "public record",
                     "record date", "record keeping", "record of"],
    "landmark":     ["landmark court", "landmark ruling", "landmark decision", "landmark case"],
    "historic":     ["historical", "in history", "throughout history"],
    "milestone":    ["milestone payment", "milestone achievement"],  # payment = regulatory term
}


# ── [B] Unanchored Vague Positives ──────────────────────────────────────────
# Count only when NOT followed by a specific figure within 80 characters.

VOCAB_B: dict[str, int] = {
    # Tier 1 — clearly unsubstantiated (weight 2)
    "strong":               2,  "robust":               2,
    "solid":                2,  "healthy":              2,
    "positive":             2,  "favorable":            2,
    "encouraging":          2,  "pleased":              2,
    "proud":                2,  "thrilled":             2,
    "excited":              2,  "enthusiastic":         2,
    "delighted":            2,  "gratified":            2,
    "compelling":           2,  "meaningful":           2,
    "well-positioned":      2,  "well positioned":      2,
    "optimistic":           2,  "bullish":              2,
    "ahead of expectations":2,  "above expectations":   2,
    "ahead of plan":        2,  "better-than-expected": 2,
    "better than expected": 2,  "ahead of our plan":    2,
    "ahead of consensus":   2,
    # Tier 2 — moderately vague (weight 1)
    "good":                 1,  "great":                1,
    "excellent":            1,  "resilient":            1,
    "durable":              1,  "sustained":            1,
    "continued momentum":   1,  "positive momentum":    1,
    "on solid footing":     1,  "in good shape":        1,
    "performing well":      1,  "confident":            1,
    "constructive":         1,  "attractive":           1,
    "differentiated":       1,  "improving":            1,
}

# Anchor pattern — suppress [B] if this pattern appears within 80 chars AFTER the match
ANCHOR_PATTERN = re.compile(
    r'(\$\s*\d|€\s*\d|£\s*\d|¥\s*\d'
    r'|\d+\.?\d*\s*%'
    r'|\d+\.?\d*\s*(billion|million|thousand|cents|bps|basis points|x\b|times\b))',
    re.IGNORECASE,
)


# ── [C] Certainty Assertions ─────────────────────────────────────────────────
# Voluntary forward guidance stated as fact or strong commitment.

VOCAB_C: dict[str, int] = {
    # Tier 1 — concrete commitments / raised guidance (weight 3)
    "we will deliver":              3,  "we will achieve":              3,
    "we will exceed":               3,  "we will grow":                 3,
    "we will generate":             3,  "we will return":               3,
    "we will expand":               3,  "we will increase":             3,
    "we will reach":                3,  "we will hit":                  3,
    "on track to achieve":          3,  "on track to deliver":          3,
    "on track to exceed":           3,  "on track for":                 3,
    "we are on track":              3,  "we remain on track":           3,
    "reaffirming guidance":         3,  "reiterating guidance":         3,
    "reaffirm our guidance":        3,  "raising our guidance":         3,
    "raising guidance":             3,  "raising our outlook":          3,
    "increasing our guidance":      3,  "increasing our outlook":       3,
    "raising our full-year":        3,  "raising full-year":            3,
    "raising our full year":        3,  "we will hit our targets":      3,
    "will meet our targets":        3,
    "will deliver":                 3,  "will achieve":                 3,
    "full-year guidance":           3,  "full year guidance":           3,
    "updated guidance":             3,  "narrowing our guidance":       3,
    "narrowing guidance":           3,  "tightening our guidance":      3,
    "tightening guidance":          3,  "above our guidance":           3,
    "above the high end":           3,  "above the top end":            3,
    "we are raising":               3,  "we are increasing our":        3,
    "we are updating":              3,  "our guidance range":           3,
    "our full-year outlook":        3,  "our full year outlook":        3,
    "reaffirm guidance":            3,  "maintaining our guidance":     3,
    "maintaining guidance":         3,
    # Tier 2 — confident but slightly qualified (weight 2)
    "we intend to":                 2,  "we plan to":                   2,
    "we are committed to delivering":2, "committed to achieving":       2,
    "confident we will":            2,  "confident in our ability to":  2,
    "well-positioned to achieve":   2,  "positioned to deliver":        2,
    "we expect to exceed":          2,  "we expect to outperform":      2,
    "we expect to achieve":         2,  "we remain committed":          2,
    "we remain confident":          2,  "we are committed to":          2,
    "committed to our guidance":    2,  "committed to delivering":      2,
    "we project":                   2,  "we forecast":                  2,
    "targeting":                    2,  "our target":                   2,
    "our targets":                  2,  "we are targeting":             2,
    "guidance of":                  2,  "outlook of":                   2,
    "we continue to expect":        2,  "continue to expect":           2,
    "continue to target":           2,  "continue to plan":             2,
    "we are on pace":               2,  "on pace to":                   2,
    "confident in our outlook":     2,  "confident in the outlook":     2,
}


# ── [D] Epistemic Hedges ─────────────────────────────────────────────────────
# Genuine uncertainty in voluntary statements (safe harbor stripped first).

VOCAB_D: dict[str, int] = {
    # Tier 1 — explicit uncertainty (weight 2)
    "we believe":           2,  "we anticipate":        2,
    "we expect":            2,  "subject to":           2,
    "contingent upon":      2,  "contingent on":        2,
    "approximately":        2,  "roughly":              2,
    "around":               2,  "in the range of":      2,
    "in a range of":        2,  "in the vicinity of":   2,
    "could range from":     2,  "may range between":    2,
    "estimated between":    2,  "assuming":             2,
    "barring unforeseen":   2,  "if conditions permit": 2,
    "depending on":         2,  "if market conditions": 2,
    "if demand remains":    2,  "subject to market":    2,
    "subject to conditions":2,  "we hope to":           2,
    "we aim to":            2,  "we target":            2,
    "no assurance":         2,  "no guarantee":         2,
    "cannot guarantee":     2,  "no certainty":         2,
    "we cannot predict":    2,  "uncertain":            2,
    # Tier 2 — softer hedges (weight 1)
    "may impact":           1,  "might affect":         1,
    "could be impacted":    1,  "could be affected":    1,
    "potentially":          1,  "may be affected":      1,
    "could result in":      1,  "if sustained":         1,
    "if this continues":    1,  "if conditions":        1,
    "may vary":             1,  "subject to change":    1,
    "cannot be assured":    1,  "we continue to monitor":1,
    "we are monitoring":    1,  "remains to be seen":   1,
    "difficult to predict": 1,  "hard to predict":      1,
    "we will continue to monitor":1, "remains uncertain":1,
    "at this time":         1,  "at current rates":     1,
}


# ── [E] Internal Attribution ─────────────────────────────────────────────────
# Management / strategy credited for positive outcomes.

VOCAB_E: dict[str, int] = {
    # Tier 1 — direct, explicit management credit (weight 3)
    "driven by our strategy":          3,  "driven by our team":               3,
    "driven by our execution":         3,  "driven by our innovation":         3,
    "driven by our pricing":           3,  "driven by our investments":        3,
    "driven by our discipline":        3,  "driven by our operational":        3,
    "a result of our execution":       3,  "a result of our strategy":         3,
    "a result of our team":            3,  "as a result of our continued":     3,
    "as a result of our discipline":   3,  "as a result of our focus":         3,
    "as a result of our investments":  3,  "our team executed":                3,
    "our team delivered":              3,  "our team drove":                   3,
    "our execution":                   3,  "our operational excellence":       3,
    "our operational discipline":      3,  "our cost discipline":              3,
    "our pricing actions":             3,  "our pricing power":                3,
    "management's focus":              3,  "management's execution":           3,
    "our management team":             3,  "our ability to execute":           3,
    "strength of our platform":        3,  "strength of our portfolio":        3,
    "strength of our model":           3,  "our strategic investments":        3,
    "our investments in":              3,  "our innovation pipeline":          3,
    "our r&d investments":             3,  "reflecting our strategic":         3,
    "reflecting our execution":        3,  "our differentiated":               3,
    "our competitive advantages":      3,  "our transformation":               3,
    "our restructuring":               3,  "our restructuring actions":        3,
    "our efficiency actions":          3,  "our portfolio actions":            3,
    # Tier 2 — implicit internal credit (weight 2)
    "our continued focus":             2,  "our discipline":                   2,
    "our diligence":                   2,  "breadth of our portfolio":         2,
    "the quality of our":              2,  "our productivity":                 2,
    "our initiatives":                 2,  "we achieved":                      2,
    "we delivered":                    2,  "we expanded":                      2,
    "our business actions":            2,  "our cost actions":                 2,
    "we drove":                        2,  "we generated":                     2,
    "we grew":                         2,  "we captured":                      2,
    "we continued to":                 2,  "our focus on":                     2,
    "our continued investment":        2,  "our strong execution":             2,
    "our team's":                      2,  "our teams":                        2,
    "our employees":                   2,  "our associates":                   2,
    "our colleagues":                  2,  "our people":                       2,
    "our culture":                     2,  "our technology":                   2,
    "our capabilities":                2,  "our portfolio of":                 2,
    "our diversified":                 2,  "our strong":                       2,
    "strength of our business":        2,  "results reflect":                  2,
    "results reflect our":             2,  "driven by our":                    2,
    "driven by strong":                2,
    "reflecting the strength":         2,  "demonstrating our":                2,
    "underpinned by":                  2,  "supported by our":                 2,
    "benefiting from our":             2,  "fueled by our":                    2,
    "powered by our":                  2,  "enabled by our":                   2,
    # Tier 2 additions — short common phrases found in real 8-K attribution language
    # (diagnostic: FTNT uses these; long phrases above miss them)
    "execute our":                     2,  "executing our":                    2,
    "our platform":                    2,  "our products":                     2,
    "our solutions":                   2,  "our offering":                     2,
    "our offerings":                   2,  "our go-to-market":                 2,
    "invest in our":                   2,  "investing in our":                 2,
    "we continue to invest":           2,  "we execute":                       1,
    "our strategy":                    1,  "our model":                        1,
    "our innovation":                  1,  "our approach":                     1,
}


# ── [F] External Attribution ─────────────────────────────────────────────────
# Environmental / market factors blamed for negative outcomes.

VOCAB_F: dict[str, int] = {
    # Tier 1 — direct, unambiguous external blame (weight 3)
    "headwinds":                    3,  "macro headwinds":              3,
    "macroeconomic headwinds":      3,  "market headwinds":             3,
    "industry headwinds":           3,  "sector headwinds":             3,
    "fx impact":                    3,  "fx headwind":                  3,
    "foreign exchange impact":      3,  "foreign exchange headwind":    3,
    "currency impact":              3,  "currency headwind":            3,
    "currency pressure":            3,  "foreign exchange pressure":    3,
    "fx pressure":                  3,
    "supply chain disruption":      3,  "supply chain constraints":     3,
    "supply chain challenges":      3,  "supply chain pressure":        3,
    "inflationary pressure":        3,  "inflationary environment":     3,
    "cost inflation":               3,  "input cost pressure":          3,
    "commodity cost pressure":      3,  "commodity inflation":          3,
    "raw material cost":            3,  "higher commodity costs":       3,
    "commodity costs":              3,
    "higher interest rates":        3,  "interest rate pressure":       3,
    "rising rates":                 3,  "elevated rates":               3,
    "rate headwinds":               3,
    "geopolitical":                 3,  "geopolitical uncertainty":     3,
    "geopolitical tensions":        3,  "geopolitical risk":            3,
    "geopolitical turmoil":         3,
    "challenging environment":      3,  "difficult environment":        3,
    "unfavorable environment":      3,  "adverse conditions":           3,
    "difficult conditions":         3,  "unfavorable conditions":       3,
    "consumer weakness":            3,  "demand weakness":              3,
    "demand softness":              3,  "demand deterioration":         3,
    "volume softness":              3,  "volume pressure":              3,
    "lower volumes":                3,  "volume shortfall":             3,
    "volume decline":               3,
    "competitive pressure":         3,  "pricing pressure":             3,
    "competitive dynamics":         3,
    "tariff impact":                3,  "trade headwinds":              3,
    "regulatory headwinds":         3,
    "market softness":              3,  "industry softness":            3,
    "sector softness":              3,
    # Tier 2 — moderate external attribution (weight 2)
    "external factors":             2,  "external pressures":           2,
    "broader macro":                2,  "macroeconomic backdrop":       2,
    "macroeconomic environment":    2,  "economic uncertainty":         2,
    "market uncertainty":           2,  "market volatility":            2,
    "weather impact":               2,  "severe weather":               2,
    "weather-related":              2,  "seasonal impact":              2,
    "timing impact":                2,  "industry weakness":            2,
    "sector weakness":              2,  "broader weakness":             2,
    "cyclical weakness":            2,  "cyclical pressure":            2,
    "market challenges":            2,  "industry challenges":          2,
    "interest rate environment":    2,  "higher cost of capital":       2,
    "slower market growth":         2,  "market deceleration":          2,
    "softening demand":             2,  "weaker demand":                2,
}


# Negation words — only unambiguous negation tokens.
# Do NOT include "from", "less", "short", "anything" — these are too common as
# non-negation prepositions/adjectives and will suppress valid matches.
NEGATION_WORDS = {
    "not", "never", "no", "without", "neither", "nor",
    "hardly", "barely", "scarcely",
}


# ─── SAFE HARBOR STRIP ────────────────────────────────────────────────────────

SAFE_HARBOR_MARKERS = [
    "actual results may differ", "forward-looking statements",
    "safe harbor", "risk factors", "cautionary statements",
    "may differ materially", "cautionary note",
]

def remove_safe_harbor(text: str) -> str:
    """
    Strip safe harbor boilerplate.

    IMPORTANT: Never strip by paragraph — many 8-K filings have no double-
    newlines, so the entire press release would be treated as one paragraph
    and stripped if it mentions "forward-looking statements" (which most do).

    Strategy instead:
      1. Split into sentences at ". " boundaries (rough but fast).
      2. Strip only sentences that contain a safe harbor marker AND are
         shorter than 80 words (boilerplate sentences are dense legalese,
         not multi-sentence results paragraphs).
      3. Never strip more than the last 40% of sentences (the first 60%
         is almost never safe harbor).
    """
    sentences = re.split(r'(?<=[.!?])\s+', text)
    n = len(sentences)
    cutoff = max(0, int(n * 0.60))   # protect the first 60%
    result = []
    for i, sent in enumerate(sentences):
        is_boilerplate = (
            i >= cutoff
            and any(m in sent.lower() for m in SAFE_HARBOR_MARKERS)
            and len(sent.split()) < 80
        )
        if not is_boilerplate:
            result.append(sent)
    return " ".join(result) if result else text   # fallback: return original


# ─── spaCy SETUP ─────────────────────────────────────────────────────────────

def load_nlp():
    try:
        # Disable unused components for speed
        return spacy.load("en_core_web_sm", disable=["ner", "lemmatizer"])
    except OSError:
        raise SystemExit(
            "\n[ERROR] spaCy model 'en_core_web_sm' not found.\n"
            "  python -m spacy download en_core_web_sm\n"
        )


def build_matchers(nlp) -> tuple[dict, dict]:
    """
    Build one PhraseMatcher per category.
    Multi-word phrases are added first so they are preferred over single words
    when both could match (e.g., 'we expect to exceed' before 'we expect').
    Returns (matchers dict, weight_lookup dict).
    """
    vocabs = {
        "A": VOCAB_A, "B": VOCAB_B, "C": VOCAB_C,
        "D": VOCAB_D, "E": VOCAB_E, "F": VOCAB_F,
    }
    matchers: dict[str, PhraseMatcher] = {}
    weight_lookup: dict[str, dict[str, int]] = {}

    for cat, vocab in vocabs.items():
        m = PhraseMatcher(nlp.vocab, attr="LOWER")
        # Sort by phrase length descending → longest match added first
        sorted_phrases = sorted(vocab.keys(), key=lambda p: len(p.split()), reverse=True)
        for phrase in sorted_phrases:
            doc_pattern = nlp.make_doc(phrase)
            m.add(phrase, [doc_pattern])
        matchers[cat] = m
        weight_lookup[cat] = vocab

    n_phrases = sum(len(v) for v in vocabs.values())
    return matchers, weight_lookup, n_phrases


# ─── WSC COMPUTATION ──────────────────────────────────────────────────────────

def compute_wsc(doc, matchers: dict, weight_lookup: dict) -> dict[str, float]:
    """
    Compute Weighted Sentence Coverage (WSC) for all six categories [A]-[F].

    Algorithm:
      1. Divide document into thirds; assign position weights 2.0 / 1.0 / 0.5.
      2. For each sentence, find all PhraseMatcher hits.
      3. Apply negation suppression (4-token look-back window).
      4. Apply exclusion list suppression for [A].
      5. Apply anchor suppression for [B] (80-char window after match).
      6. Take MAX tier weight across all valid hits in the sentence (not SUM).
      7. Multiply by position weight; accumulate.
      8. Divide accumulated total by sentence count → WSC score.

    Returns dict: {"A": float, "B": float, ..., "F": float}
    """
    sentences = list(doc.sents)
    n = len(sentences)
    if n == 0:
        return {cat: 0.0 for cat in "ABCDEF"}

    # Build token-index → sentence-index map
    sent_of_token: dict[int, int] = {}
    for s_idx, sent in enumerate(sentences):
        for tok in sent:
            sent_of_token[tok.i] = s_idx

    # Position weight per sentence
    pos_weights = []
    for s_idx in range(n):
        rel = s_idx / n
        if rel < 0.333:
            pos_weights.append(2.0)
        elif rel < 0.667:
            pos_weights.append(1.0)
        else:
            pos_weights.append(0.5)

    # Accumulate max weight per sentence per category
    sent_max: dict[str, list[float]] = {cat: [0.0] * n for cat in "ABCDEF"}

    for cat in "ABCDEF":
        matcher = matchers[cat]
        for _, start, end in matcher(doc):
            s_idx = sent_of_token.get(start)
            if s_idx is None:
                continue

            phrase = doc[start:end].text.lower()
            weight = float(weight_lookup[cat].get(phrase, 1))

            # ── Negation suppression (4-token look-back) ──────────────────
            neg_start = max(0, start - 4)
            neg_context = doc[neg_start:start].text.lower().split()
            if any(neg in neg_context for neg in NEGATION_WORDS):
                continue

            # ── [A] Exclusion list ─────────────────────────────────────────
            if cat == "A":
                first_word = phrase.split()[0]
                excl = A_EXCLUSIONS.get(first_word, [])
                if excl:
                    ctx_start = max(0, start - 2)
                    ctx_end   = min(len(doc), end + 3)
                    ctx = doc[ctx_start:ctx_end].text.lower()
                    if any(e in ctx for e in excl):
                        continue

            # ── [B] Anchor suppression (80-char window after match) ────────
            if cat == "B":
                # Use character offsets for accuracy
                if end > 0 and end <= len(doc):
                    end_char = doc[end - 1].idx + len(doc[end - 1].text)
                    after_text = doc.text[end_char: end_char + 80]
                    if ANCHOR_PATTERN.search(after_text):
                        continue

            # Update max weight for this sentence
            if weight > sent_max[cat][s_idx]:
                sent_max[cat][s_idx] = weight

    # WSC = Σ(max_weight × position_weight) / n_sentences
    wsc: dict[str, float] = {}
    for cat in "ABCDEF":
        total = sum(sent_max[cat][i] * pos_weights[i] for i in range(n))
        wsc[cat] = total / n

    return wsc


# ─── FORMULA-BASED SCORES ────────────────────────────────────────────────────

def compute_qo(wsc_a: float, wsc_b: float) -> tuple[int, str]:
    """
    QO = point-band rubric on WSC_A (superlatives) + WSC_B (vague positives).

    Empirically calibrated on 45-row pilot (khashi_hype_scores.csv):
      wsc_a: mean=0.165, p25=0.047, median=0.152, p75=0.259, p90=0.327
      wsc_b: mean=0.153, p25=0.086, median=0.158, p75=0.203

    Band cutoffs align with observed percentiles so score spreads across 0-25
    rather than clustering near the old formula mean of ~12.

    wsc_a tiers (0-18 pts): 0→0, ≤0.05(p25)→2, ≤0.15(med)→6,
                             ≤0.25(p75)→10, ≤0.40(p90+)→14, >0.40→18
    wsc_b tiers (0-7 pts):  0→0, ≤0.08(p25)→2, ≤0.17(med)→4, >0.17→7
    """
    # wsc_a band (0–18)
    if wsc_a == 0:
        s_a = 0
    elif wsc_a <= 0.03:
        s_a = 2
    elif wsc_a <= 0.08:
        s_a = 6
    elif wsc_a <= 0.15:
        s_a = 10
    elif wsc_a <= 0.22:
        s_a = 14
    else:
        s_a = 18

    # wsc_b band (0–7)
    if wsc_b == 0:
        s_b = 0
    elif wsc_b <= 0.05:
        s_b = 2
    elif wsc_b <= 0.12:
        s_b = 4
    else:
        s_b = 7

    score = min(25, s_a + s_b)
    reasoning = (
        f"WSC[A]={wsc_a:.3f} (superlatives) → s_a={s_a}/18 "
        f"[bands: 0→0, ≤0.03→2, ≤0.08→6, ≤0.15→10, ≤0.22→14, >0.22→18]. "
        f"WSC[B]={wsc_b:.3f} (vague positives) → s_b={s_b}/7 "
        f"[bands: 0→0, ≤0.05→2, ≤0.12→4, >0.12→7]. "
        f"QO={s_a}+{s_b}={score}/25. "
        f"Recalibrated (v2): wsc_a mean=0.165 → s_a=14; wsc_b median=0.158 → s_b=7."
    )
    return score, reasoning


def compute_cl(doc) -> tuple[int, str, dict]:
    """
    CL = Certainty Language — how assertively does the filing present forward guidance.

    Sentence-level approach grounded in Loughran & McDonald (2011) modal word lists.
    Fixes the VOCAB_C/D problem: real 8-Ks must use weak modals ("we expect ... in
    the range of") for SEC safe-harbor compliance, so phrase matching was blind to
    the genuine variation in guidance precision and commitment.

    Signals per guidance sentence (guidance verb + dollar/% figure):
      1. Strong-modal balance  (LM_STRONG_MODAL vs LM_WEAK_MODAL)
      2. Precision bonus       (specific numerical range: "range of X to Y")
      3. Uncertainty penalty   (LM_UNCERTAINTY density across all sentences)

    Genre baseline 9-13 = company provides specific quarterly guidance with standard
    SEC hedge language ("we expect ... in the range of ...").

    Academic grounding: Loughran & McDonald (2011) — ~4,000 citations in financial NLP.
    """
    sents  = list(doc.sents)
    n_sent = max(len(sents), 1)

    guidance_sents      = 0
    strong_in_guidance  = 0
    weak_in_guidance    = 0
    precision_count     = 0
    uncertainty_total   = 0

    _NUM_RE   = re.compile(r'\$[\d,.]|\d+(?:\.\d+)?\s*(?:billion|million|%|bps)', re.I)
    _RANGE_RE = re.compile(r'range of|between \$?[\d,.]+ and|\$[\d,.]+ (?:to|-) \$?[\d,.]+', re.I)

    for sent in sents:
        tl    = sent.text.lower()
        words = [w.strip(".,;:!?\"'()[]") for w in tl.split()]

        uncertainty_total += sum(1 for w in words if w in LM_UNCERTAINTY)

        has_guidance = any(w in GUIDANCE_VERBS for w in words)
        has_number   = bool(_NUM_RE.search(tl))

        if has_guidance and has_number:
            guidance_sents     += 1
            strong_in_guidance += sum(1 for w in words if w in LM_STRONG_MODAL)
            weak_in_guidance   += sum(1 for w in words if w in LM_WEAK_MODAL)
            if _RANGE_RE.search(tl):
                precision_count += 1

    # ── Derive sub-signals ────────────────────────────────────────────────────
    guidance_density = guidance_sents / n_sent          # typical 8-K: 0.04-0.12
    modal_total      = strong_in_guidance + weak_in_guidance
    modal_ratio      = (strong_in_guidance / modal_total) if modal_total > 0 else 0.3
    unc_density      = uncertainty_total / n_sent        # typical 8-K: 0.02-0.08

    g = guidance_sents  # alias for readability

    # ── Diagnostic signal dict — raw continuous features for correlation/factor
    # analysis. Populated regardless of branch so downstream stats always see a
    # complete row. This does NOT change score/reasoning; additive only.
    diag = {
        "cl_guidance_sents":     g,
        "cl_guidance_density":   round(guidance_density, 4),
        "cl_strong_modal_count": strong_in_guidance,
        "cl_weak_modal_count":   weak_in_guidance,
        "cl_modal_ratio":        round(modal_ratio, 4),
        "cl_precision_count":    precision_count,
        "cl_unc_density":        round(unc_density, 4),
    }

    if g == 0:
        # No forward-looking sentences with figures → no certainty language signal
        score = 0
        reasoning = (
            f"No guidance sentences detected (guidance verb + $ / % figure). "
            f"CL=0: absence of forward guidance is a meaningful signal, not a floor. "
            f"Uncertainty density={unc_density:.3f}. "
            f"[LM word-list method; Loughran & McDonald 2011]"
        )
    else:
        # ── Sub-1: guidance count (0-12 pts) ─────────────────────────────────
        # Empirical pilot: g=0 most common, g=1-4 typical, g>8 high-guidance
        if   g == 1:     count_pts = 4
        elif g == 2:     count_pts = 6
        elif g <= 4:     count_pts = 9
        elif g <= 8:     count_pts = 11
        elif g <= 13:    count_pts = 13
        else:            count_pts = 15

        # ── Sub-2: modal quality (0-8 pts) ───────────────────────────────────
        # strong modals (will, shall) vs weak (expect, anticipate) relative to guidance sents
        if modal_total == 0:
            modal_pts = 5   # guidance with no modal words — neutral
        elif modal_ratio < 0.2:
            modal_pts = 3   # dominated by weak modals (heavy SEC hedging)
        elif modal_ratio <= 0.5:
            modal_pts = 7   # balanced (typical high-quality guidance)
        else:
            modal_pts = 10  # strong-modal dominant (highly assertive guidance)

        # ── Sub-3: numerical range bonus (0-5 pts) ───────────────────────────
        # "in the range of X to Y" or "between $X and $Y" → precise commitment
        if   precision_count == 0: range_pts = 0
        elif precision_count == 1: range_pts = 2
        elif precision_count == 2: range_pts = 3
        else:                      range_pts = 5

        # ── Sub-4: uncertainty penalty (0 to -5 pts) ─────────────────────────
        # LM uncertainty density across all sentences
        if   unc_density < 0.02:  unc_pts = 0
        elif unc_density < 0.05:  unc_pts = -1
        elif unc_density < 0.10:  unc_pts = -2
        elif unc_density < 0.15:  unc_pts = -3
        else:                     unc_pts = -5

        score = max(0, min(25, count_pts + modal_pts + range_pts + unc_pts))

        reasoning = (
            f"Guidance sentences={g}/{n_sent}. "
            f"Sub1 count={count_pts}/15 [g={g}]. "
            f"Sub2 modal={modal_pts}/10 "
            f"[strong={strong_in_guidance}, weak={weak_in_guidance}, ratio={modal_ratio:.2f}]. "
            f"Sub3 ranges={range_pts}/5 [precision_count={precision_count}]. "
            f"Sub4 uncertainty={unc_pts} [unc_density={unc_density:.3f}]. "
            f"CL={count_pts}+{modal_pts}+{range_pts}+({unc_pts})={score}/25. "
            f"[LM word-list method; Loughran & McDonald 2011]"
        )

    return score, reasoning, diag


def compute_sa(wsc_e: float, wsc_f: float) -> tuple[int, str]:
    """
    SA = absolute-density point-band rubric (Baginski et al. 2004 grounding).

    Old ratio formula clustered: ~35% of rows got SA=25 because wsc_f=0 → ratio=1.0.
    New design uses wsc_e absolute density tiers (calibrated on 45-row pilot) + a
    wsc_f modifier that rewards pure-credit filings and penalises heavy blame language.

    Empirical pilot: wsc_e mean=0.084, median=0.063, p25=0.000, p75=0.148 (29% zeros)
                     wsc_f mean=0.031, median=0.008, p75=0.049, p90=0.091 (49% zeros)

    wsc_e tiers  (0–22 pts): 0→0, ≤0.04→4, ≤0.08→8, ≤0.15→13, ≤0.25→18, >0.25→22
    wsc_f modifier (−5 to +3):
        e=0 & f=0 → 0   (no attribution signal at all)
        e=0 & f>0 → -3  (only blame, no credit)
        f=0       → +3  (pure internal credit)
        f≤0.02    → +2  (near-zero blame)
        f≤0.05    → +1  (below-median blame)
        f≤0.10    → 0   (typical blame level)
        f≤0.15    → -2  (above-median blame)
        f>0.15    → -5  (heavy blame / external attribution)
    """
    e, f = wsc_e, wsc_f

    # wsc_e tier (0-22)
    if   e == 0:      tier = 0
    elif e <= 0.02:   tier = 4
    elif e <= 0.05:   tier = 8
    elif e <= 0.10:   tier = 13
    elif e <= 0.18:   tier = 18
    else:             tier = 22

    # wsc_f modifier
    if   e == 0 and f == 0:  mod = 0
    elif e == 0 and f > 0:   mod = -3
    elif f == 0:              mod = +3
    elif f <= 0.02:           mod = +2
    elif f <= 0.05:           mod = +1
    elif f <= 0.10:           mod = 0
    elif f <= 0.15:           mod = -2
    else:                     mod = -5

    score = max(0, min(25, tier + mod))
    reasoning = (
        f"WSC[E]={e:.3f} (internal credit) → tier={tier}/22 "
        f"[bands: 0→0, ≤0.02→4, ≤0.05→8, ≤0.10→13, ≤0.18→18, >0.18→22]. "
        f"WSC[F]={f:.3f} (external blame) → modifier={mod:+d} "
        f"[f=0→+3, ≤0.02→+2, ≤0.05→+1, ≤0.10→0, ≤0.15→-2, >0.15→-5; "
        f"e=0&f>0→-3, e=0&f=0→0]. "
        f"SA={tier}+({mod})={score}/25. "
        f"Calibrated: wsc_e mean=0.084 (29% zero), wsc_f mean=0.031 (49% zero). "
        f"[Baginski et al. 2004 — causal attribution in earnings disclosures]"
    )
    return score, reasoning


def compute_hype_v3(wsc: dict, cl_diag: dict, se_signals: dict) -> tuple[int, dict, str]:
    """
    HypeScore v3 — 8-criterion, data-driven restructuring. Revision 3 (2026-07-15,
    post stress-test on TWO 1000-row v3 validation runs).

    Calibrated on the 1000-row diagnostic run via OLS regression of hype_score on
    all 16 standardized raw signals (wsc_a-f, SE's D1-D6, CL's four sub-signals).
    Criteria are ranked and weighted by that regression's standardized coefficients,
    NOT by assertion:

        wsc_e (SA)          beta=+6.70  <- strongest driver in the dataset
        wsc_a (QO)           beta=+5.01
        diag_se_d5 (SE)      beta=+3.93
        diag_cl_guidance_density (CL) beta=+3.58
        diag_se_d6 (SE)      beta=+2.74
        diag_cl_precision_count (CL)  beta=+2.69
        wsc_b (QO)           beta=+2.39
        diag_se_d1 (SE)      beta=+1.88
        wsc_f (SA)           beta=-1.38
        wsc_c, wsc_d (unused)  beta=+0.59 (p=0.02), +0.47 (NOT significant, p=0.09)
            -> excluded. Turning these on would add scored dimensions with the
               weakest, least-defensible signal of anything in the pipeline.

    REVISION 2 — what changed and why (see khashi_hype_v3_1000.csv stress test):
      1. C8 SA-ExternalBlame (wsc_f-based) is REMOVED. Measured live on the 1000-row
         run it had r=-0.059 with hype_v3 — statistically indistinguishable from
         zero, because wsc_f=0 (no blame language detected) in >50% of filings. A
         criterion that doesn't discriminate has no business in a scoring formula
         claiming ~9 data-driven components; cutting beats padding.
      2. D4 (SE "bad news burial") — dropped from Revision 1 entirely — is
         reinstated, but MERGED into the old SE-Demotion criterion (now
         SE-Concealment) rather than given its own slot. D4 and D5 correlate at
         r=-0.01 (genuinely independent signal, not redundant), and Revision 1's
         complete omission of D4 caused large, spurious-looking score drops for
         filings whose selective-emphasis behavior happened to concentrate in
         burial rather than demotion (e.g. EQT 2020-01-13: SE=25/25 in v2, driven
         mostly by D4=5, collapsed under Revision 1). A first attempt to fix this
         with a small standalone D4 residual (0-4 pts) was tested and rejected —
         it only correlated r=0.055 with the total, too weak to matter. Merging
         D4+D5 into one wider-range criterion (0-14, vs the old 0-12 D5-only) gives
         D4 real weight: r=0.31 with the total in the corrected version.

    REVISION 3 — a real bug, not just a gap (found while investigating why NFLX,
    MTD, SBUX dropped 15-19 points under Revision 2 despite writing unusually
    assertive, strong-modal-dominant guidance language):
      3. C4 CL-GuidanceStrength never scored modal assertiveness at all (only
         guidance_density + precision_count) — a real gap, since v2's own CL
         formula gave up to 10/25 points for strong-modal-dominant guidance
         ("will", "shall" vs. "may", "could" — the core Loughran-McDonald
         certainty distinction). Worse: Revision 2's C8 "hedging" modifier had
         the SIGN INVERTED — `if modal_ratio > 0.5: c8 -= 2` penalized the most
         ASSERTIVE, LEAST-hedged filings (high modal_ratio = mostly strong
         modals = more certain), exactly backwards from both the theory and v2's
         own working formula. Confirmed on NFLX 2018-01-22 (modal_ratio=1.0,
         100% strong modals) — was being pulled toward a hedging penalty despite
         having zero hedging. Fix: modal-strength bonus moved into C4 with the
         correct sign (reward strong-modal dominance), and C8 is now a pure
         uncertainty-density penalty only — no more overlap/sign conflict
         between the two.

    Eight criteria (SA/QO/SE/CL labels kept for lineage, not four equal buckets):
        C1 SA-Attribution (wsc_e)                          0-22  strongest driver
        C2 QO-Superlative (wsc_a)                          0-18  second strongest
        C3 SE-Concealment (diag_se_d4 + diag_se_d5 merged) 0-14  burial + demotion,
                                                                   r=-0.01 w/ each other
        C4 CL-GuidanceStrength (guidance_density + precision_count             0-15
                                 + modal-strength bonus, sign-corrected in rev3)
        C5 SE-QualifierAsymmetry (diag_se_d6)               0-8
        C6 QO-VaguePositive (wsc_b)                         0-7   unchanged from v2
        C7 SE-Framing (diag_se_d1+d2 merged, r=0.53 -> genuinely redundant, merged) 0-10
        C8 CL-Uncertainty (unc_density only, rev3)          -2..0  pure penalty;
                                                                     modal_ratio moved
                                                                     out to C4 (rev3)

    D3 (SE headline) standalone remains excluded — still bottom-third OLS importance
    and no case-level justification surfaced in either stress test to reinstate it.

    Global rescale (V3_SCALE): raw 8-criterion sum tops out at 77/100 in the 1000-row
    sample. Rescaled by 90/77=1.1688 so the top of the empirical distribution lands
    at ~90, matching v2's scale utilization. Same pattern as SE's v2 x1.5 fix.
    """
    e, a, b = wsc["E"], wsc["A"], wsc["B"]
    d1 = se_signals.get("d1", 0) if not se_signals.get("empty") else 0
    d2 = se_signals.get("d2", 0) if not se_signals.get("empty") else 0
    d4 = se_signals.get("d4", 0) if not se_signals.get("empty") else 0
    d5 = se_signals.get("d5", 0) if not se_signals.get("empty") else 0
    d6 = se_signals.get("d6", 0) if not se_signals.get("empty") else 0
    gd = cl_diag["cl_guidance_density"]
    pc = cl_diag["cl_precision_count"]
    strong_modal = cl_diag["cl_strong_modal_count"]
    weak_modal   = cl_diag["cl_weak_modal_count"]
    ud = cl_diag["cl_unc_density"]

    # C1 — SA-Attribution (wsc_e), max 22
    if e == 0:            c1 = 0
    elif e <= 0.0188:     c1 = 6
    elif e <= 0.0606:     c1 = 11
    elif e <= 0.1111:     c1 = 16
    elif e <= 0.1771:     c1 = 19
    else:                 c1 = 22

    # C2 — QO-Superlative (wsc_a), max 18
    if a == 0:             c2 = 0
    elif a <= 0.0405:      c2 = 5
    elif a <= 0.1135:      c2 = 9
    elif a <= 0.1852:      c2 = 13
    elif a <= 0.3209:      c2 = 16
    else:                  c2 = 18

    # C3 — SE-Concealment (diag_se_d4 native 1-5, shifted to 0-4, + diag_se_d5 native
    # 0-5; combined native range 0-9), scaled to max 14 (14/9 factor)
    concealment_raw = max(0, min(4, d4 - 1)) + max(0, min(5, d5))
    c3 = max(0, min(14, round(concealment_raw * (14 / 9))))

    # C4 — CL-GuidanceStrength: guidance_density + precision_count + modal-strength
    # bonus (rev3: modal-strength added here, with the correct sign — strong-modal-
    # dominant guidance is MORE assertive/certain, not hedged, so it adds points).
    gd_pts = 0 if gd == 0 else (2 if gd <= 0.0193 else (5 if gd <= 0.0526 else 7))
    pc_pts = 0 if pc == 0 else (2 if pc <= 2 else (4 if pc <= 5 else 5))
    modal_total = strong_modal + weak_modal
    if modal_total == 0:
        modal_pts = 0
    else:
        modal_ratio = strong_modal / modal_total
        modal_pts = 3 if modal_ratio > 0.5 else (1 if modal_ratio > 0.2 else 0)
    c4 = min(15, gd_pts + pc_pts + modal_pts)

    # C5 — SE-QualifierAsymmetry (diag_se_d6, native 1-5), max 8
    c5 = max(0, min(8, round((d6 - 1) / 4 * 8))) if d6 else 0

    # C6 — QO-VaguePositive (wsc_b), max 7 — unchanged band from v2
    if b == 0:        c6 = 0
    elif b <= 0.05:   c6 = 2
    elif b <= 0.12:   c6 = 4
    else:             c6 = 7

    # C7 — SE-Framing (diag_se_d1 + d2 merged), max 10
    c7 = min(10, d1 + d2)

    # C8 — CL-Uncertainty: pure uncertainty-density penalty (rev3: modal_ratio moved
    # to C4 above; this criterion no longer overlaps with it, no sign conflict)
    c8 = 0
    if ud > 0.0469:      c8 = -2
    elif ud > 0.0238:    c8 = -1   # median unc_density on the 1000-row rev2 run

    raw_total = c1 + c2 + c3 + c4 + c5 + c6 + c7 + c8
    V3_SCALE = 1.1688  # 90/77, calibrated on the 1000-row rev2 validation run
    hype_v3 = max(0, min(100, round(raw_total * V3_SCALE)))

    components = {
        "v3_c1_sa_attribution": c1, "v3_c2_qo_superlative": c2,
        "v3_c3_se_concealment": c3, "v3_c4_cl_guidance_strength": c4,
        "v3_c5_se_qualifier_asymmetry": c5, "v3_c6_qo_vague_positive": c6,
        "v3_c7_se_framing": c7, "v3_c8_cl_uncertainty": c8,
    }
    reasoning = (
        f"v3rev3(8-criteria, OLS-ranked + 2x stress-test corrected): "
        f"C1 SA-Attr={c1}/22 C2 QO-Super={c2}/18 C3 SE-Conceal={c3}/14 "
        f"C4 CL-Guid={c4}/15(incl. modal-strength bonus) C5 SE-Qual={c5}/8 C6 QO-Vague={c6}/7 "
        f"C7 SE-Frame={c7}/10 C8 CL-Uncertainty={c8:+d}. "
        f"raw={raw_total} x {V3_SCALE} = {hype_v3}/100. "
        f"wsc_c/wsc_d and standalone wsc_f excluded (weak/near-zero in 1000-row OLS + live stress test)."
    )
    return hype_v3, components, reasoning


# ─── SE: FEATURE-GUIDED LLM SCORING ─────────────────────────────────────────
#
# Two-stage hybrid methodology:
#   Stage 1 (Python)  — extract_se_signals()   : deterministic vocabulary
#                        extraction across 6 dimensions → structured signal dict
#   Stage 2 (LLM)     — score_se_with_signals() : qwen3:8b reads the signal
#                        summary (~200 tokens, NOT raw text) and produces
#                        se_score + written justification
#
# Academic rationale: Python signals are transparent & reproducible; LLM adds
# interpretive synthesis grounded in objective evidence (reduces confabulation).
# Input to LLM is ~200 tokens vs 1000 words → ~5–10s/row instead of ~50s.
#
# Six signal dimensions:
# D1 — Opening Position   : does non-GAAP appear before GAAP?
# D2 — Volume Density     : non-GAAP count vs GAAP count ratio
# D3 — Headline Capture   : non-GAAP language in opening 500 chars
# D4 — Bad News Burial    : negative terms concentrated in back 40%?
# D5 — GAAP Demotion      : GAAP relegated to tables / reconciliation?
# D6 — Qualifier Asymmetry: positives stated as fact, negatives externally blamed

# -- Non-GAAP signal vocabulary --
SE_NONGAAP: list[str] = [
    # Strong signals (most specific first to avoid substring double-count confusion)
    "adjusted earnings per share", "adjusted net income per share",
    "adjusted operating income", "adjusted operating margin",
    "adjusted gross margin", "adjusted revenue", "adjusted ebitda",
    "adjusted net income", "adjusted earnings", "adjusted free cash flow",
    "adjusted income", "adjusted margin", "adjusted profit",
    "adjusted eps", "non-gaap eps", "non-gaap earnings per share",
    "non-gaap net income", "non-gaap revenue", "non-gaap gross margin",
    "non-gaap operating income", "non-gaap operating margin",
    "non-gaap income", "non-gaap profit",
    "non-gaap", "non gaap",          # generic — counted last
    "pro forma", "core earnings", "core revenue", "core operating",
    "organic revenue", "organic growth", "organic sales",
    "constant currency", "constant-currency",
    "normalized earnings", "normalized revenue", "normalized ebitda",
    "underlying earnings", "underlying revenue", "underlying profit",
    "cash earnings", "cash net income",
    "adjusted",                       # broadest — catches remaining uses
    "excluding",                      # "excluding items", "excluding restructuring"
]

# -- GAAP signal vocabulary --
SE_GAAP: list[str] = [
    "gaap net income", "gaap earnings per share", "gaap eps",
    "gaap revenue", "gaap gross margin", "gaap operating income",
    "gaap earnings", "gaap income", "gaap profit",
    "reported net income", "reported revenue", "reported earnings",
    "reported eps", "reported operating income",
    "as reported",
    "gaap",                           # generic GAAP mention
    "net income",                     # default GAAP metric
    "net loss",
    "total revenue",                  # typically GAAP line item
    "diluted eps", "basic eps",       # GAAP-context EPS
]

# -- Negative result vocabulary (for burial analysis) --
SE_NEGATIVE: list[str] = [
    "below expectations", "below our expectations", "below consensus",
    "below guidance", "below the high end", "below the low end",
    "missed expectations", "missed our guidance",
    "net loss", "operating loss", "reported a loss",
    "write-down", "write-off", "impairment charge", "goodwill impairment",
    "restructuring charge", "restructuring cost",
    "margin compression", "margin pressure", "margin decline",
    "revenue decline", "revenue decreased", "revenue fell",
    "earnings decline", "earnings fell", "earnings decreased",
    "declined", "decreased", "deteriorated",
    "shortfall", "miss", "missed",
    "below prior year", "below last year", "below the prior",
    "unfavorable", "adverse impact", "negative impact",
    "volume decline", "volume pressure", "volume shortfall",
]

# -- GAAP demotion vocabulary (relegation to tables/footnotes) --
SE_DEMOTION: list[str] = [
    "reconciliation of gaap to non-gaap",
    "gaap to non-gaap reconciliation",
    "reconciliation of non-gaap",
    "a reconciliation of",
    "please refer to the reconciliation",
    "refer to the non-gaap",
    "see the non-gaap reconciliation",
    "see non-gaap reconciliation",
    "reconciliation table",
    "reconciliation is attached",
    "reconciliation is included",
    "reconciliation can be found",
    "reconciliation appears",
    "reconciliation attached",
    "financial tables below",
    "financial tables at the end",
    "tables at the end of this release",
    "supplemental financial data",
    "supplemental tables",
    "gaap results are included in the tables",
    "included in the financial tables",
    "provided in the tables",
    "in the accompanying tables",
    "non-gaap financial measures",
    "use of non-gaap financial measures",
]

# -- Positive-as-fact vocabulary (stated without hedging) --
SE_POS_FACT: list[str] = [
    "we delivered", "we achieved", "we generated", "we drove",
    "we grew", "we expanded", "we captured", "we gained",
    "we returned", "we increased", "we improved",
    "record revenue", "record earnings", "record sales", "record profit",
    "revenue grew", "sales grew", "earnings grew", "profit grew",
    "revenue increased", "earnings increased", "sales increased",
    "strong growth", "robust growth", "solid growth",
    "significant growth", "meaningful growth",
]

# -- External blame vocabulary (negatives attributed to outside forces) --
SE_EXT_BLAME: list[str] = [
    "due to macro", "due to the macro", "due to macroeconomic",
    "due to market", "due to currency", "due to fx",
    "due to foreign exchange", "due to supply chain",
    "due to inflationary", "due to higher costs",
    "due to geopolitical", "due to competitive",
    "impacted by", "adversely impacted by",
    "negatively impacted by", "affected by",
    "driven by macro", "driven by market conditions",
    "driven by currency", "driven by foreign exchange",
    "driven by headwinds", "driven by the challenging",
    "resulting from macro", "resulting from market",
    "resulting from currency", "resulting from fx",
    "attributable to macro", "attributable to market",
    "attributable to external", "attributable to currency",
    "reflecting the challenging", "reflecting challenging",
    "reflecting macroeconomic", "reflecting market",
    "reflecting currency", "reflecting competitive",
    "due to industry-wide", "due to broader",
    "due to the broader", "due to sector",
    "partially offset by", "more than offset by",
]


def extract_se_signals(text: str) -> dict:
    """
    Stage 1 of SE scoring — pure Python, runs in milliseconds.
    Extracts 6 structured signal dimensions from the filing text.
    Returns a dict passed to the LLM for interpretive synthesis (Stage 2).
    """
    tl = text.lower()
    n  = len(tl)
    if n == 0:
        return {"empty": True}

    def first_pos(terms: list[str], haystack: str) -> int:
        hits = [haystack.find(t) for t in terms if haystack.find(t) >= 0]
        return min(hits) if hits else len(haystack)

    def count_hits(terms: list[str], haystack: str) -> int:
        return sum(haystack.count(t) for t in terms)

    # ── D1: Opening Position ─────────────────────────────────────────────────
    pos_ng = first_pos(SE_NONGAAP, tl)
    pos_g  = first_pos(SE_GAAP,    tl)

    if pos_ng == n and pos_g == n:
        d1 = 2
    elif pos_ng == n:
        d1 = 0
    elif pos_g == n:
        d1 = 5
    else:
        gap_pct = (pos_g - pos_ng) / n
        if   gap_pct >  0.20: d1 = 5
        elif gap_pct >  0.08: d1 = 4
        elif gap_pct >  0.01: d1 = 3
        elif gap_pct > -0.01: d1 = 2
        else:                  d1 = 1

    # ── D2: Volume Density ───────────────────────────────────────────────────
    ng_count = count_hits(SE_NONGAAP, tl)
    g_count  = count_hits(SE_GAAP,    tl)
    denom    = ng_count + g_count
    ng_ratio = ng_count / denom if denom > 0 else 0.5

    if   ng_ratio > 0.82: d2 = 5
    elif ng_ratio > 0.68: d2 = 4
    elif ng_ratio > 0.52: d2 = 3
    elif ng_ratio > 0.38: d2 = 2
    else:                  d2 = 1

    # ── D3: Headline Capture ─────────────────────────────────────────────────
    opening = tl[:500]
    hl_specific = [
        "record adjusted", "adjusted revenue grew", "adjusted eps",
        "adjusted earnings of", "non-gaap eps", "non-gaap earnings",
        "adjusted operating income grew", "delivered adjusted",
        "strong adjusted", "robust adjusted", "solid adjusted",
        "adjusted net income grew", "adjusted ebitda grew",
    ]
    hl_specific_hits = sum(1 for t in hl_specific if t in opening)
    hl_generic_hits  = sum(1 for t in SE_NONGAAP[:10] if t in opening)
    hl_total         = hl_specific_hits * 2 + hl_generic_hits

    if   hl_total >= 8: d3 = 5
    elif hl_total >= 5: d3 = 4
    elif hl_total >= 3: d3 = 3
    elif hl_total >= 1: d3 = 2
    else:               d3 = 0

    # ── D4: Bad News Burial ──────────────────────────────────────────────────
    cutoff    = int(n * 0.60)
    neg_front = count_hits(SE_NEGATIVE, tl[:cutoff])
    neg_back  = count_hits(SE_NEGATIVE, tl[cutoff:])
    neg_total = neg_front + neg_back

    if neg_total == 0:
        d4 = 3
        burial_pct = 50
    else:
        burial_pct = int(neg_back / neg_total * 100)
        if   burial_pct > 78: d4 = 5
        elif burial_pct > 62: d4 = 4
        elif burial_pct > 45: d4 = 3
        elif burial_pct > 28: d4 = 2
        else:                  d4 = 1

    # ── D5: GAAP Demotion ────────────────────────────────────────────────────
    dem_hits = count_hits(SE_DEMOTION, tl)

    if   dem_hits >= 5: d5 = 5
    elif dem_hits >= 3: d5 = 4
    elif dem_hits >= 2: d5 = 3
    elif dem_hits >= 1: d5 = 2
    else:               d5 = 0

    # ── D6: Qualifier Asymmetry ──────────────────────────────────────────────
    pos_fact  = count_hits(SE_POS_FACT,  tl)
    ext_blame = count_hits(SE_EXT_BLAME, tl)

    if pos_fact == 0 and ext_blame == 0:
        d6 = 2
    elif pos_fact == 0:
        d6 = 1
    else:
        blame_ratio = ext_blame / pos_fact
        if   blame_ratio > 2.5: d6 = 5
        elif blame_ratio > 1.5: d6 = 4
        elif blame_ratio > 0.8: d6 = 3
        elif blame_ratio > 0.3: d6 = 2
        else:                   d6 = 1

    # ── Python preliminary score ──────────────────────────────────────────────
    # Each D-dimension already scored 0-5; raw sum 0-30.
    # Old: raw * 25/30 → compressed range, clustered at 12-13.
    # New: raw directly as SE score (0-25 cap). D-dimensions are 0-5 each;
    #      raw=25 requires five 5s (rare, high-hype); raw=0 means all neutral.
    #      This gives genuine spread across 0-25.
    raw       = d1 + d2 + d3 + d4 + d5 + d6
    python_se = min(25, max(0, round(raw * 1.5)))

    return {
        "empty":        False,
        "ng_count":     ng_count,     "g_count":      g_count,
        "ng_ratio":     ng_ratio,     "denom":        denom,
        "ng_first_pct": int(pos_ng / n * 100) if pos_ng < n else 100,
        "g_first_pct":  int(pos_g  / n * 100) if pos_g  < n else 100,
        "hl_total":     hl_total,
        "neg_front":    neg_front,    "neg_back":     neg_back,
        "neg_total":    neg_total,    "burial_pct":   burial_pct,
        "dem_hits":     dem_hits,
        "pos_fact":     pos_fact,     "ext_blame":    ext_blame,
        "d1": d1, "d2": d2, "d3": d3,
        "d4": d4, "d5": d5, "d6": d6,
        "raw": raw, "python_se": python_se,
    }


# ── Stage 2: LLM synthesises signals into SE score + reasoning ───────────────

SE_SIGNALS_SYSTEM = """You are a senior financial disclosure analyst specialising in earnings press release framing.

You will receive structured signals automatically extracted from an S&P 500 8-K earnings filing.
Your job: synthesise these signals into one SE (Selective Emphasis) score 0–25 and write a 2-sentence justification.

SE measures how asymmetrically a filing presents positive vs negative information, and non-GAAP vs GAAP metrics.
Genre baseline: 9–13 = typical S&P 500 8-K filing.

Score guide:
SE 0–4:   GAAP leads or co-equal. Negatives front-loaded. Symmetric framing.
SE 5–8:   Mild non-GAAP preference. Minor positive tilt.
SE 9–13:  Non-GAAP headlines, GAAP secondary, some burial. [GENRE AVERAGE]
SE 14–17: Non-GAAP dominates, negatives clearly buried or minimised.
SE 18–21: GAAP absent from prose. Significant selective omission.
SE 22–25: Extreme asymmetry — only positives shown, no GAAP, negatives absent."""


def call_se_signals_llm(signals: dict, previous_error: str = "") -> dict:
    retry_note = (
        f"Previous response was invalid: {previous_error}. Return ONLY the JSON object.\n\n"
        if previous_error else ""
    )
    signal_block = (
        f"EXTRACTED SIGNALS from S&P 500 earnings press release (8-K):\n"
        f"- Non-GAAP metric mentions: {signals['ng_count']}x | "
        f"GAAP metric mentions: {signals['g_count']}x | "
        f"Non-GAAP share of all metric language: {signals['ng_ratio']:.0%}\n"
        f"- First non-GAAP mention: at {signals['ng_first_pct']}% into document | "
        f"First GAAP mention: at {signals['g_first_pct']}% into document\n"
        f"- Non-GAAP language in opening headline (first 500 chars): "
        f"{signals['hl_total']} weighted hits\n"
        f"- Negative result terms: {signals['neg_front']} in front 60% | "
        f"{signals['neg_back']} in back 40% ({signals['burial_pct']}% of negatives buried)\n"
        f"- GAAP relegated-to-tables phrases: {signals['dem_hits']} instances\n"
        f"- Positive claims stated as fact: {signals['pos_fact']} | "
        f"External blame phrases: {signals['ext_blame']}\n"
        f"- Python sub-scores "
        f"[D1-Position / D2-Density / D3-Headline / D4-Burial / D5-Demotion / D6-Asymmetry]: "
        f"{signals['d1']}/{signals['d2']}/{signals['d3']}/"
        f"{signals['d4']}/{signals['d5']}/{signals['d6']} (each out of 5)\n"
        f"- Python preliminary SE estimate: {signals['python_se']}/25\n\n"
        f"Based on the signals above, provide your SE score (0–25) and a 2-sentence reasoning "
        f"interpreting the key signals and positioning vs genre baseline."
    )
    payload = {
        "model":  MODEL,
        "stream": False,
        "format": {
            "type": "object",
            "properties": {
                "se_score":     {"type": "integer", "minimum": 0, "maximum": 25},
                "se_reasoning": {"type": "string"},
            },
            "required": ["se_score", "se_reasoning"],
            "additionalProperties": False,
        },
        "think": False,
        "messages": [
            {"role": "system", "content": SE_SIGNALS_SYSTEM},
            {"role": "user",   "content": "/no_think\n" + retry_note + signal_block},
        ],
        "options": {
            "temperature": 0,
            "top_k":       1,
            "top_p":       0.1,
            "seed":        42,
            "num_predict": 80,    # score + 2 short sentences ≈ 60 tokens; 80 is safe ceiling
            "num_ctx":     512,   # signals (~200 tokens) + system prompt (~150) fits easily
        },
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    content = data.get("message", {}).get("content", "")
    if not content:
        raise ValueError("Empty response from model")

    parsed = json.loads(content)
    v = parsed.get("se_score")
    if not isinstance(v, int) or not (0 <= v <= 25):
        raise ValueError(f"se_score={v!r} out of range 0–25")
    if not isinstance(parsed.get("se_reasoning"), str):
        raise ValueError("se_reasoning missing or not a string")
    return parsed


def score_se_with_signals(signals: dict) -> dict:
    """
    SE scoring — Python D1-D6 formula only (LLM removed for speed).

    LLM was removed because:
      1. Speed: qwen3:8b took 60-80s/row (need ≤10s). Python is <1s.
      2. D1-D6 sub-signals already capture the key SE dimensions deterministically:
         D1=GAAP/non-GAAP position, D2=density ratio, D3=headline, D4=burial,
         D5=demotion phrases, D6=qualifier asymmetry.
      3. Python formula raw/30×25 is monotone and interpretable — adequate for
         cross-sectional ranking across 14,000+ filings.

    The call_se_signals_llm() and SE_SIGNALS_SYSTEM are kept below for
    optional re-activation if a batch GPU environment becomes available.
    """
    if signals.get("empty"):
        return {"se_score": 0,
                "se_reasoning": "Empty text; SE=0 (no signals detected)."}

    se = signals["python_se"]
    return {
        "se_score": se,
        "se_reasoning": (
            f"Python D1-D6 scoring (deterministic; LLM disabled for speed). "
            f"D1={signals['d1']} (GAAP/nonGAAP position) "
            f"D2={signals['d2']} (density ratio) "
            f"D3={signals['d3']} (headline) "
            f"D4={signals['d4']} (burial) "
            f"D5={signals['d5']} (demotion) "
            f"D6={signals['d6']} (qualifier asymmetry). "
            f"raw={signals['raw']}/30 → SE=round(raw×1.5)={se}/25 (capped at 25). "
            f"[Old formula raw×25/30 compressed range; new formula uses ×1.5 scaling]"
        ),
    }


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def truncate_to_words(text: str, max_words: int) -> str:
    if max_words <= 0:
        return text
    words = text.split()
    return text if len(words) <= max_words else " ".join(words[:max_words])

def word_count(text: str) -> int:
    return len(text.split())

def load_already_scored() -> set:
    done: set = set()
    if not RESUME or not os.path.exists(OUTPUT_CSV):
        return done
    with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            done.add((row["ticker"], row["cik"], row["filingDate"], row["accessionNumber"]))
    return done


# ─── WARMUP + ROW WORKER ──────────────────────────────────────────────────────

def warmup_model() -> None:
    """Send a no-op call to ensure the model is loaded into RAM before scoring."""
    print(f"[{datetime.now():%H:%M:%S}] Warming up {MODEL}...")
    try:
        payload = {
            "model": MODEL, "stream": False, "think": False,
            "messages": [{"role": "user", "content": "/no_think hi"}],
            "options": {"num_predict": 1, "num_ctx": 128, "temperature": 0},
        }
        req = urllib.request.Request(
            f"{OLLAMA_URL.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
        print(f"[{datetime.now():%H:%M:%S}] Model warm. Starting scored run.")
    except Exception as exc:
        print(f"[{datetime.now():%H:%M:%S}] Warmup skipped ({exc}); continuing anyway.")


def score_one_row(
    row: dict,
    nlp,
    matchers: dict,
    weight_lookup: dict,
) -> dict:
    """
    Score a single filing row. Fully self-contained — safe to call from multiple threads.
    Returns a result dict ready to write to the output CSV.
    """
    t0 = time.time()

    text_stripped = remove_safe_harbor(row.get("item_202_text", ""))
    text_spacy    = truncate_to_words(text_stripped, SPACY_MAX_WORDS)
    wc            = word_count(text_spacy)

    doc = nlp(text_spacy)
    wsc = compute_wsc(doc, matchers, weight_lookup)

    qo_score, qo_reasoning = compute_qo(wsc["A"], wsc["B"])
    cl_score, cl_reasoning, cl_diag = compute_cl(doc)
    sa_score, sa_reasoning = compute_sa(wsc["E"], wsc["F"])

    signals   = extract_se_signals(text_spacy)
    se_result = score_se_with_signals(signals)
    se_score  = se_result["se_score"]
    se_reasoning = se_result["se_reasoning"]

    hype_score = qo_score + se_score + cl_score + sa_score

    # ── v3: 9-criterion, data-driven restructuring (see compute_hype_v3 docstring) ──
    hype_v3, v3_components, v3_reasoning = compute_hype_v3(wsc, cl_diag, signals)

    elapsed    = time.time() - t0

    result = {
        "ticker":          row["ticker"],
        "cik":             row["cik"],
        "filingDate":      row["filingDate"],
        "accessionNumber": row["accessionNumber"],
        "source":          row.get("source", ""),
        "wsc_a": round(wsc["A"], 4), "wsc_b": round(wsc["B"], 4),
        "wsc_c": round(wsc["C"], 4), "wsc_d": round(wsc["D"], 4),
        "wsc_e": round(wsc["E"], 4), "wsc_f": round(wsc["F"], 4),
        "qo_score":    qo_score,  "qo_reasoning":  qo_reasoning,
        "se_score":    se_score,  "se_reasoning":  se_reasoning,
        "cl_score":    cl_score,  "cl_reasoning":  cl_reasoning,
        "sa_score":    sa_score,  "sa_reasoning":  sa_reasoning,
        "hype_score":  hype_score,
        "word_count":  wc,
        # ── DIAGNOSTIC-ONLY fields (v4 instrumentation) ──────────────────────
        # Raw continuous signals, not yet folded into any score. Purpose: feed
        # the correlation/factor analysis on the 1000-row test run so component
        # count and weights are decided from data, not asserted. Safe to ignore
        # for anyone just consuming qo/se/cl/sa/hype_score as before.
        "diag_se_ng_ratio":    round(signals.get("ng_ratio", 0), 4) if not signals.get("empty") else 0,
        "diag_se_burial_pct":  signals.get("burial_pct", 0) if not signals.get("empty") else 0,
        "diag_se_dem_hits":    signals.get("dem_hits", 0) if not signals.get("empty") else 0,
        "diag_se_hl_total":    signals.get("hl_total", 0) if not signals.get("empty") else 0,
        "diag_se_pos_fact":    signals.get("pos_fact", 0) if not signals.get("empty") else 0,
        "diag_se_ext_blame":   signals.get("ext_blame", 0) if not signals.get("empty") else 0,
        "diag_se_d1": signals.get("d1", 0) if not signals.get("empty") else 0,
        "diag_se_d2": signals.get("d2", 0) if not signals.get("empty") else 0,
        "diag_se_d3": signals.get("d3", 0) if not signals.get("empty") else 0,
        "diag_se_d4": signals.get("d4", 0) if not signals.get("empty") else 0,
        "diag_se_d5": signals.get("d5", 0) if not signals.get("empty") else 0,
        "diag_se_d6": signals.get("d6", 0) if not signals.get("empty") else 0,
        "diag_cl_guidance_sents":     cl_diag["cl_guidance_sents"],
        "diag_cl_guidance_density":   cl_diag["cl_guidance_density"],
        "diag_cl_strong_modal_count": cl_diag["cl_strong_modal_count"],
        "diag_cl_weak_modal_count":   cl_diag["cl_weak_modal_count"],
        "diag_cl_modal_ratio":        cl_diag["cl_modal_ratio"],
        "diag_cl_precision_count":    cl_diag["cl_precision_count"],
        "diag_cl_unc_density":        cl_diag["cl_unc_density"],
        # ── v3 9-criterion score (candidate replacement for hype_score) ──────
        "hype_v3":     hype_v3,
        "v3_reasoning": v3_reasoning,
        **v3_components,
        "_elapsed":    elapsed,   # internal — stripped before CSV write
    }
    return result


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    # ── Load NLP ──────────────────────────────────────────────────────────────
    print(f"[{datetime.now():%H:%M:%S}] Loading spaCy model (en_core_web_sm)...")
    nlp = load_nlp()
    matchers, weight_lookup, n_phrases = build_matchers(nlp)
    print(f"[{datetime.now():%H:%M:%S}] Ready. {n_phrases} phrases across 6 categories.")
    print(f"[{datetime.now():%H:%M:%S}] Config: SPACY_MAX={SPACY_MAX_WORDS} MODEL={MODEL} "
          f"CONCURRENCY={CONCURRENCY} SAMPLE={SAMPLE_SIZE}")

    # ── Warm up model (LLM disabled — SE is now Python-only) ─────────────────
    # warmup_model()  # Re-enable if LLM is re-activated for SE

    # ── Load input data ────────────────────────────────────────────────────────
    print(f"[{datetime.now():%H:%M:%S}] Loading {INPUT_CSV} ...")
    rows: list[dict] = []
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    print(f"[{datetime.now():%H:%M:%S}] Loaded {len(rows):,} rows.")

    rows = [r for r in rows if word_count(r.get("item_202_text", "")) >= MIN_WORDS]
    print(f"[{datetime.now():%H:%M:%S}] {len(rows):,} rows after {MIN_WORDS}-word minimum filter.")

    if SAMPLE_SIZE > 0 and len(rows) > SAMPLE_SIZE:
        random.seed(RANDOM_SEED)
        rows = random.sample(rows, SAMPLE_SIZE)
        print(f"[{datetime.now():%H:%M:%S}] Sampled {SAMPLE_SIZE} rows (seed={RANDOM_SEED}).")

    already_done = load_already_scored()
    if already_done:
        print(f"[{datetime.now():%H:%M:%S}] RESUME: skipping {len(already_done)} already scored rows.")
    todo = [
        r for r in rows
        if (r["ticker"], r["cik"], r["filingDate"], r["accessionNumber"]) not in already_done
    ]
    print(f"[{datetime.now():%H:%M:%S}] {len(todo)} rows to score.")
    if not todo:
        print("Nothing to do. Exiting.")
        return

    # ── Output files ──────────────────────────────────────────────────────────
    out_fields = [
        "ticker", "cik", "filingDate", "accessionNumber", "source",
        "wsc_a", "wsc_b", "wsc_c", "wsc_d", "wsc_e", "wsc_f",
        "qo_score", "qo_reasoning",
        "se_score", "se_reasoning",
        "cl_score", "cl_reasoning",
        "sa_score", "sa_reasoning",
        "hype_score", "word_count",
        # diagnostic-only — see score_one_row() note. Not part of hype_score.
        "diag_se_ng_ratio", "diag_se_burial_pct", "diag_se_dem_hits",
        "diag_se_hl_total", "diag_se_pos_fact", "diag_se_ext_blame",
        "diag_se_d1", "diag_se_d2", "diag_se_d3",
        "diag_se_d4", "diag_se_d5", "diag_se_d6",
        "diag_cl_guidance_sents", "diag_cl_guidance_density",
        "diag_cl_strong_modal_count", "diag_cl_weak_modal_count",
        "diag_cl_modal_ratio", "diag_cl_precision_count", "diag_cl_unc_density",
        # v3 rev2: 8-criterion candidate score, corrected after stress-testing the
        # first 1000-row v3 run (see compute_hype_v3 docstring for what changed).
        # hype_score above is still v2 (unchanged) — compare the two before switching.
        "hype_v3", "v3_reasoning",
        "v3_c1_sa_attribution", "v3_c2_qo_superlative", "v3_c3_se_concealment",
        "v3_c4_cl_guidance_strength", "v3_c5_se_qualifier_asymmetry",
        "v3_c6_qo_vague_positive", "v3_c7_se_framing", "v3_c8_cl_uncertainty",
    ]
    err_fields = ["ticker", "cik", "filingDate", "accessionNumber", "error"]

    output_exists = os.path.exists(OUTPUT_CSV) and os.path.getsize(OUTPUT_CSV) > 0
    errors_exist  = os.path.exists(ERRORS_CSV) and os.path.getsize(ERRORS_CSV) > 0

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

    # Shared state for the threaded loop (protected by write_lock)
    write_lock  = threading.Lock()
    scored      = 0
    total_done  = 0
    elapsed_sum = 0.0
    run_start   = time.time()

    with open(OUTPUT_CSV, "a", newline="", encoding="utf-8") as out_f, \
         open(ERRORS_CSV, "a", newline="", encoding="utf-8") as err_f:

        out_w = csv.DictWriter(out_f, fieldnames=out_fields)
        err_w = csv.DictWriter(err_f, fieldnames=err_fields)
        if not output_exists:
            out_w.writeheader()
        if not errors_exist:
            err_w.writeheader()

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
            future_to_row = {
                executor.submit(score_one_row, row, nlp, matchers, weight_lookup): row
                for row in todo
            }

            for future in as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    result = future.result()
                    elapsed = result.pop("_elapsed")   # strip internal field

                    with write_lock:
                        out_w.writerow(result)
                        out_f.flush()
                        scored     += 1
                        total_done += 1
                        elapsed_sum += elapsed
                        avg_elapsed  = elapsed_sum / total_done
                        remaining    = len(todo) - total_done
                        eta_min      = (avg_elapsed * remaining) / 60

                        if total_done % PROGRESS_EVERY == 0 or total_done == len(todo):
                            pct = 100 * total_done / len(todo)
                            print(f"[{datetime.now():%H:%M:%S}] {total_done:3d}/{len(todo)} ({pct:.0f}%) "
                                  f"| scored={scored} "
                                  f"| {result['ticker']} QO={result['qo_score']} "
                                  f"SE={result['se_score']} CL={result['cl_score']} "
                                  f"SA={result['sa_score']} → {result['hype_score']} "
                                  f"| {avg_elapsed:.1f}s/row avg | ETA ~{eta_min:.0f}min")

                except Exception as exc:
                    with write_lock:
                        total_done += 1
                        err_w.writerow({
                            "ticker":          row["ticker"],
                            "cik":             row["cik"],
                            "filingDate":      row["filingDate"],
                            "accessionNumber": row["accessionNumber"],
                            "error":           str(exc),
                        })
                        err_f.flush()
                        print(f"[{datetime.now():%H:%M:%S}] ERROR {row['ticker']}: {exc}")

    wall = (time.time() - run_start) / 60
    print(f"\n[{datetime.now():%H:%M:%S}] ══ COMPLETE ══  Scored={scored} | Wall time={wall:.1f}min")
    print(f"  Output : {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
