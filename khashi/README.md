# Khashi HypeScore Scorer — Full Methodology Guide

Standalone, deterministic scoring pipeline for corporate hype language in SEC
Form 8-K Item 2.02 earnings press releases. Built for the TUM seminar project
*"Advanced Topics in FinTech: AI Agents and Blockchain"* (SS26) — workstream:
**Corporate Hype Narratives & Stock Price Reversals**.

This document is the complete, code-backed methodology reference: every
vocabulary, every formula, every design decision, and the academic paper
behind each one. Nothing here should require reading the codebase itself or
having sat through the calibration sessions to understand.

---

## Contents

1. [The hypothesis](#1-the-hypothesis)
2. [Why Python + spaCy, not an LLM](#2-why-python--spacy-not-an-llm)
3. [Pipeline, step by step](#3-pipeline-step-by-step)
4. [Weighted Sentence Coverage (WSC) — the core formula](#4-weighted-sentence-coverage-wsc--the-core-formula)
5. [Complete vocabularies, [A]–[F]](#5-complete-vocabularies-af)
6. [QO — Qualitative Optimism](#6-qo--qualitative-optimism)
7. [SA — Self-Attribution](#7-sa--self-attribution)
8. [CL — Certainty Language](#8-cl--certainty-language)
9. [SE — Selective Emphasis](#9-se--selective-emphasis)
10. [`hype_score` (v2) — four equal buckets](#10-hype_score-v2--four-equal-buckets)
11. [`hype_v3` (rev3) — eight data-driven criteria](#11-hype_v3-rev3--eight-data-driven-criteria)
12. [Stress-test history — three rounds, real bugs fixed](#12-stress-test-history--three-rounds-real-bugs-fixed)
13. [Known limitations](#13-known-limitations)
14. [Running it](#14-running-it)
15. [Output schema](#15-output-schema)
16. [Every paper, and exactly what it grounds](#16-every-paper-and-exactly-what-it-grounds)
17. [LLM validation pass on ambiguous SE rows](#17-llm-validation-pass-on-ambiguous-se-rows)

---

## 1. The hypothesis

Unusually high "hype" language in an earnings press release predicts a stock
price **reversal**: management overclaims in the language of the release, the
market initially rewards the tone, and the price corrects once the numbers
catch up. `HypeScore` (0–100) turns "hype" into a number so this can be
tested statistically across the full dataset (~14,351 S&P 500 8-K Item 2.02
filings, 2018–2025) rather than argued about qualitatively.

## 2. Why Python + spaCy, not an LLM

Scoring 14,351 filings sentence-by-sentence with an LLM is too slow and not
reproducible run-to-run (temperature, model drift, rate limits, cost).
Everything in this pipeline is **deterministic vocabulary + rule-based
scoring** — the same input text always produces the same score, forever, on
any machine, and every score can be traced back to the exact phrase that
caused it. An LLM path exists in the code for one narrow use (SE's signal
synthesis, `score_se_with_signals` / `call_se_signals_llm`) but is disabled:
it took 60–80s/row on CPU versus <1s for the Python formula, for no
measurable accuracy gain over the six-signal breakdown it would have
summarized anyway.

That said, "disabled everywhere" left the pipeline with zero actual
LLM/agent judgment despite being designed for it. A scoped exception was
added afterward: a targeted LLM validation pass on just the ~7% of filings
where the deterministic SE signals genuinely conflict. See
[§17](#17-llm-validation-pass-on-ambiguous-se-rows) and
[`llm_validation/README.md`](llm_validation/README.md) for the full
methodology, results, and findings.

## 3. Pipeline, step by step

1. **Load** the row from `item202_clean.csv`
   (`ticker, cik, filingDate, accessionNumber, source, item_202_text`).
2. **Strip safe-harbor boilerplate** — `remove_safe_harbor()`. Sentences in
   the **last 40%** of the text that contain a safe-harbor marker (below)
   **and** are under 80 words are removed:
   ```python
   SAFE_HARBOR_MARKERS = [
       "actual results may differ", "forward-looking statements",
       "safe harbor", "risk factors", "cautionary statements",
       "may differ materially", "cautionary note",
   ]
   ```
   Only the back 40% and only short sentences are touched, because many
   8-Ks have no paragraph breaks — stripping too aggressively would delete
   real content that happens to mention "forward-looking" once.
3. **Truncate** to the first 3,000 words (`SPACY_MAX_WORDS`) — long enough to
   capture the guidance section and management commentary, short enough to
   keep spaCy fast across 14k filings.
4. **Parse with spaCy** (`en_core_web_sm`, NER and the lemmatizer disabled —
   we don't need either, and disabling them roughly doubles throughput):
   ```python
   spacy.load("en_core_web_sm", disable=["ner", "lemmatizer"])
   ```
5. **Match six vocabulary categories [A]–[F]** against every sentence via
   `PhraseMatcher(nlp.vocab, attr="LOWER")` — exact phrase, case-insensitive.
6. **Aggregate into WSC scores** — one float per category (§4).
7. **Run CL's separate sentence-level pass** (§8).
8. **Run SE's six deterministic sub-signals** (§9).
9. **Combine into two parallel final scores** — `hype_score` (v2, §10) and
   `hype_v3` (rev3, §11) — both written to every output row.

## 4. Weighted Sentence Coverage (WSC) — the core formula

```python
def compute_wsc(doc, matchers, weight_lookup):
    sentences = list(doc.sents)
    n = len(sentences)

    # position weight per sentence: front third=2.0, middle=1.0, back third=0.5
    pos_weights = []
    for s_idx in range(n):
        rel = s_idx / n
        if rel < 0.333:   pos_weights.append(2.0)
        elif rel < 0.667: pos_weights.append(1.0)
        else:             pos_weights.append(0.5)

    sent_max = {cat: [0.0] * n for cat in "ABCDEF"}
    for cat in "ABCDEF":
        for _, start, end in matchers[cat](doc):
            s_idx = sent_of_token.get(start)
            phrase = doc[start:end].text.lower()
            weight = float(weight_lookup[cat].get(phrase, 1))
            # negation suppression: 4-token look-back for "not", "never", "no"...
            # [A]-only exclusion list; [B]-only anchor suppression (see §5)
            if weight > sent_max[cat][s_idx]:
                sent_max[cat][s_idx] = weight   # MAX, not SUM, per sentence

    wsc = {}
    for cat in "ABCDEF":
        total = sum(sent_max[cat][i] * pos_weights[i] for i in range(n))
        wsc[cat] = total / n
    return wsc
```

```
WSC_X = Σ [ MAX_tier_weight(sentence_i) × position_weight_i ] / n_sentences
```

Three design choices, each deliberate:

- **Position weighting** (front third × 2.0, middle × 1.0, back third × 0.5):
  front-loaded language counts more. A press release's opening paragraph is
  the headline framing choice; the same superlative buried in paragraph 12 is
  much less likely to be the thing that moved the market's initial reaction.
- **MAX, not SUM, per sentence**: if one sentence has three tier-1
  superlatives, it counts once at the tier-1 weight, not three times — stops
  a single hype-dense sentence from dominating the whole document's score.
  We want to measure how *pervasive* the language is, not how repetitive one
  paragraph is.
- **Negation suppression**: a 4-token look-back window checks for *"not,"
  "never," "no," "without," "neither," "nor," "hardly," "barely,"
  "scarcely"* before counting a match — "not unprecedented" doesn't score.

## 5. Complete vocabularies, [A]–[F]

Matching is **exact phrase, case-insensitive**, not semantic/embedding-based
— a deliberate precision-over-recall tradeoff. A fixed vocabulary is
auditable (you can point to exactly which phrase triggered a score) and
perfectly reproducible; a semantic matcher would catch paraphrases we missed
but couldn't be defended sentence-by-sentence, and its behavior could drift
between runs. The lemmatizer is disabled, so morphological variants ("grew"
vs. "growing") are listed separately where we want them caught — a real,
documented recall limitation.

### [A] Strong Superlatives → feeds QO

| Tier (weight) | Phrases |
|---|---|
| 1 (×3) | record, record-breaking, record-setting, all-time high, all-time best, all-time record, best ever, best-ever, unprecedented, unparalleled, unmatched, unrivaled, unsurpassed, transformational, transformative, revolutionary, groundbreaking, pioneering, paradigm-shifting, game-changing, world-class, world class, world-leading, world leading, industry-leading, industry leading, market-leading, market leading, best-in-class, best in class, best-in-breed, best in breed, top-tier, top tier, first-of-its-kind, first of its kind, landmark, historic milestone, never-before, exceptional, extraordinary, outstanding, phenomenal, stellar, never been stronger, never been better, never been higher, never been greater, best quarter ever, best year ever, best in history, highest ever, largest ever, fastest ever, lowest ever |
| 2 (×2) | remarkable, impressive, exemplary, distinguished, elite, premium, cutting-edge, cutting edge, state-of-the-art, state of the art, breakout, breakthrough, class-leading, class leading, top-performing, top performing, best-performing, best performing, outperformed, differentiated platform, differentiated model |
| 3 (×1) | notable, significant achievement, strong momentum, milestone, historic |

**Exclusion list** (`A_EXCLUSIONS`) — suppresses ambiguous matches by context:
`outstanding` → suppressed near *"outstanding balance/shares/debt/loan/
obligation/borrowings/principal/amount/notes/warrants"* (financial-statement
sense, not achievement). `record` → suppressed near *"track record, on the
record, for the record, public record, record date, record keeping, record
of"*. `landmark` → suppressed near *"landmark court/ruling/decision/case"*.
`historic` → suppressed near *"historical, in history, throughout history"*.
`milestone` → suppressed near *"milestone payment, milestone achievement"*
(regulatory/contractual term, not a narrative claim).

### [B] Unanchored Vague Positives → feeds QO

| Tier (weight) | Phrases |
|---|---|
| 1 (×2) | strong, robust, solid, healthy, positive, favorable, encouraging, pleased, proud, thrilled, excited, enthusiastic, delighted, gratified, compelling, meaningful, well-positioned, well positioned, optimistic, bullish, ahead of expectations, above expectations, ahead of plan, better-than-expected, better than expected, ahead of our plan, ahead of consensus |
| 2 (×1) | good, great, excellent, resilient, durable, sustained, continued momentum, positive momentum, on solid footing, in good shape, performing well, confident, constructive, attractive, differentiated, improving |

**Anchor suppression** — the single most theoretically important rule in the
whole vocabulary. A match is discarded if a dollar figure, percentage, or
magnitude word appears within 80 characters *after* it:
```python
ANCHOR_PATTERN = re.compile(
    r'(\$\s*\d|€\s*\d|£\s*\d|¥\s*\d'
    r'|\d+\.?\d*\s*%'
    r'|\d+\.?\d*\s*(billion|million|thousand|cents|bps|basis points|x\b|times\b))',
    re.IGNORECASE,
)
```
"Strong growth" (vague, scores) vs. "strong growth of 12%" (anchored to a
verifiable number, doesn't score). This is what makes QO measure
*unsubstantiated* optimism specifically, not positive language in general.

### [C] Certainty Assertions → **computed, not scored** (see §13)

| Tier (weight) | Phrases |
|---|---|
| 1 (×3) | we will deliver, we will achieve, we will exceed, we will grow, we will generate, we will return, we will expand, we will increase, we will reach, we will hit, on track to achieve, on track to deliver, on track to exceed, on track for, we are on track, we remain on track, reaffirming guidance, reiterating guidance, reaffirm our guidance, raising our guidance, raising guidance, raising our outlook, increasing our guidance, increasing our outlook, raising our full-year, raising full-year, raising our full year, we will hit our targets, will meet our targets, will deliver, will achieve, full-year guidance, full year guidance, updated guidance, narrowing our guidance, narrowing guidance, tightening our guidance, tightening guidance, above our guidance, above the high end, above the top end, we are raising, we are increasing our, we are updating, our guidance range, our full-year outlook, our full year outlook, reaffirm guidance, maintaining our guidance, maintaining guidance |
| 2 (×2) | we intend to, we plan to, we are committed to delivering, committed to achieving, confident we will, confident in our ability to, well-positioned to achieve, positioned to deliver, we expect to exceed, we expect to outperform, we expect to achieve, we remain committed, we remain confident, we are committed to, committed to our guidance, committed to delivering, we project, we forecast, targeting, our target, our targets, we are targeting, guidance of, outlook of, we continue to expect, continue to expect, continue to target, continue to plan, we are on pace, on pace to, confident in our outlook, confident in the outlook |

### [D] Epistemic Hedges → **computed, not scored** (see §13)

| Tier (weight) | Phrases |
|---|---|
| 1 (×2) | we believe, we anticipate, we expect, subject to, contingent upon, contingent on, approximately, roughly, around, in the range of, in a range of, in the vicinity of, could range from, may range between, estimated between, assuming, barring unforeseen, if conditions permit, depending on, if market conditions, if demand remains, subject to market, subject to conditions, we hope to, we aim to, we target, no assurance, no guarantee, cannot guarantee, no certainty, we cannot predict, uncertain |
| 2 (×1) | may impact, might affect, could be impacted, could be affected, potentially, may be affected, could result in, if sustained, if this continues, if conditions, may vary, subject to change, cannot be assured, we continue to monitor, we are monitoring, remains to be seen, difficult to predict, hard to predict, we will continue to monitor, remains uncertain, at this time, at current rates |

### [E] Internal Attribution → feeds SA

| Tier (weight) | Phrases |
|---|---|
| 1 (×3) | driven by our strategy, driven by our team, driven by our execution, driven by our innovation, driven by our pricing, driven by our investments, driven by our discipline, driven by our operational, a result of our execution, a result of our strategy, a result of our team, as a result of our continued, as a result of our discipline, as a result of our focus, as a result of our investments, our team executed, our team delivered, our team drove, our execution, our operational excellence, our operational discipline, our cost discipline, our pricing actions, our pricing power, management's focus, management's execution, our management team, our ability to execute, strength of our platform, strength of our portfolio, strength of our model, our strategic investments, our investments in, our innovation pipeline, our r&d investments, reflecting our strategic, reflecting our execution, our differentiated, our competitive advantages, our transformation, our restructuring, our restructuring actions, our efficiency actions, our portfolio actions |
| 2 (×2) | our continued focus, our discipline, our diligence, breadth of our portfolio, the quality of our, our productivity, our initiatives, we achieved, we delivered, we expanded, our business actions, our cost actions, we drove, we generated, we grew, we captured, we continued to, our focus on, our continued investment, our strong execution, our team's, our teams, our employees, our associates, our colleagues, our people, our culture, our technology, our capabilities, our portfolio of, our diversified, our strong, strength of our business, results reflect, results reflect our, driven by our, driven by strong, reflecting the strength, demonstrating our, underpinned by, supported by our, benefiting from our, fueled by our, powered by our, enabled by our, execute our, executing our, our platform, our products, our solutions, our offering, our offerings, our go-to-market, invest in our, investing in our, we continue to invest |
| 3 (×1) | we execute, our strategy, our model, our innovation, our approach |

### [F] External Attribution → feeds SA

| Tier (weight) | Phrases |
|---|---|
| 1 (×3) | headwinds, macro headwinds, macroeconomic headwinds, market headwinds, industry headwinds, sector headwinds, fx impact, fx headwind, foreign exchange impact, foreign exchange headwind, currency impact, currency headwind, currency pressure, foreign exchange pressure, fx pressure, supply chain disruption, supply chain constraints, supply chain challenges, supply chain pressure, inflationary pressure, inflationary environment, cost inflation, input cost pressure, commodity cost pressure, commodity inflation, raw material cost, higher commodity costs, commodity costs, higher interest rates, interest rate pressure, rising rates, elevated rates, rate headwinds, geopolitical, geopolitical uncertainty, geopolitical tensions, geopolitical risk, geopolitical turmoil, challenging environment, difficult environment, unfavorable environment, adverse conditions, difficult conditions, unfavorable conditions, consumer weakness, demand weakness, demand softness, demand deterioration, volume softness, volume pressure, lower volumes, volume shortfall, volume decline, competitive pressure, pricing pressure, competitive dynamics, tariff impact, trade headwinds, regulatory headwinds, market softness, industry softness, sector softness |
| 2 (×2) | external factors, external pressures, broader macro, macroeconomic backdrop, macroeconomic environment, economic uncertainty, market uncertainty, market volatility, weather impact, severe weather, weather-related, seasonal impact, timing impact, industry weakness, sector weakness, broader weakness, cyclical weakness, cyclical pressure, market challenges, industry challenges, interest rate environment, higher cost of capital, slower market growth, market deceleration, softening demand, weaker demand |

[E] and [F] together operationalize an attribution asymmetry directly: heavy
[E] + light [F] = "I did this"; heavy [F] = "the market did this to me."

## 6. QO — Qualitative Optimism

```python
def compute_qo(wsc_a: float, wsc_b: float) -> tuple[int, str]:
    # wsc_a band (0-18)
    if   wsc_a == 0:    s_a = 0
    elif wsc_a <= 0.03: s_a = 2
    elif wsc_a <= 0.08: s_a = 6
    elif wsc_a <= 0.15: s_a = 10
    elif wsc_a <= 0.22: s_a = 14
    else:               s_a = 18

    # wsc_b band (0-7)
    if   wsc_b == 0:    s_b = 0
    elif wsc_b <= 0.05: s_b = 2
    elif wsc_b <= 0.12: s_b = 4
    else:               s_b = 7

    return min(25, s_a + s_b)
```

Point-band rubric directly on WSC[A] (superlatives, 0–18 pts) + WSC[B] (vague
positives, 0–7 pts), summed and capped at 25. Bands are fit to the observed
percentiles of a 100-row pilot (not asserted) so the score spreads across
0–25 instead of clustering near the mean — the same "observed distribution →
band cutoffs" calibration pattern used everywhere in this pipeline.

## 7. SA — Self-Attribution

```python
def compute_sa(wsc_e: float, wsc_f: float) -> tuple[int, str]:
    e, f = wsc_e, wsc_f
    # wsc_e tier (0-22)
    if   e == 0:    tier = 0
    elif e <= 0.02: tier = 4
    elif e <= 0.05: tier = 8
    elif e <= 0.10: tier = 13
    elif e <= 0.18: tier = 18
    else:           tier = 22

    # wsc_f modifier (-5 to +3)
    if   e == 0 and f == 0: mod = 0
    elif e == 0 and f > 0:  mod = -3
    elif f == 0:            mod = +3
    elif f <= 0.02:         mod = +2
    elif f <= 0.05:         mod = +1
    elif f <= 0.10:         mod = 0
    elif f <= 0.15:         mod = -2
    else:                   mod = -5

    return max(0, min(25, tier + mod))
```

Absolute-density tier on WSC[E] (internal credit, 0–22 pts) plus a signed
modifier from WSC[F] (external blame, −5 to +3). An earlier ratio-based
formula (`wsc_e / (wsc_e + wsc_f)`) clustered badly — ~35% of filings scored
SA=25 simply because `wsc_f=0` made the ratio 1.0, regardless of how much
internal credit-taking was actually present. The absolute-density design
fixes this: the modifier rewards *pure* internal credit (`wsc_f == 0` → +3)
and penalizes heavy external blame (`wsc_f > 0.15` → −5), but the base score
still comes from how much internal-credit language is actually present, not
a ratio that can be gamed by having zero of either.

## 8. CL — Certainty Language

CL does **not** use [C]/[D]. It runs a separate, sentence-restricted pass:
find sentences containing both a guidance verb **and** a $/% figure — only
those count as "guidance sentences" — then scores modal strength and
precision *only within them*.

```python
GUIDANCE_VERBS = {
    "expect", "expects", "expected", "anticipate", "anticipates", "anticipated",
    "forecast", "forecasts", "projected", "projects", "project",
    "estimate", "estimates", "estimated", "guide", "guided", "guidance",
    "target", "targets", "targeted", "outlook",
}
LM_STRONG_MODAL = {
    "will", "shall", "must", "always", "never", "certainly", "definitely",
    "clearly", "undoubtedly", "unambiguously", "assuredly", "necessarily",
    "indisputably",
}
LM_WEAK_MODAL = {
    "may", "might", "could", "would", "possibly", "potentially", "perhaps",
    "probably", "presumably", "approximately", "roughly", "nearly", "often",
    "sometimes", "occasionally", "typically", "generally", "usually",
    "estimated", "seldom", "somewhat",
}
LM_UNCERTAINTY = {
    "uncertain", "uncertainty", "unpredictable", "unclear", "indefinite",
    "contingent", "variable", "depends", "depend", "unforeseen",
    "volatile", "volatility", "fluctuate", "fluctuation", "subject",
}
```

```python
for sent in sentences:
    has_guidance = any(w in GUIDANCE_VERBS for w in words)
    has_number   = bool(NUM_RE.search(sentence_text))   # $, %, billion, million, bps
    if has_guidance and has_number:
        guidance_sents += 1
        strong_in_guidance += count(LM_STRONG_MODAL)
        weak_in_guidance   += count(LM_WEAK_MODAL)
        if RANGE_RE.search(sentence_text):   # "range of $X to $Y"
            precision_count += 1
```

Four scored sub-signals within guidance sentences:

1. **Count** of guidance sentences → 0–15 pts, banded (`g=1→4, g=2→6, g≤4→9,
   g≤8→11, g≤13→13, else→15`).
2. **Modal strength** — strong-modal share of all modals found →
   0–10 pts (`modal_total==0→5` neutral; `ratio<0.2→3` weak-dominant;
   `ratio≤0.5→7` balanced; `ratio>0.5→10` strong-dominant/assertive).
3. **Precision bonus** — explicit numeric range ("between $X and $Y") →
   0–5 pts, by count of ranges found.
4. **Uncertainty penalty** — density of `LM_UNCERTAINTY` words across the
   *whole* filing → 0 to −5 pts.

`CL = count_pts + modal_pts + range_pts + unc_pts`, capped 0–25. If zero
guidance sentences exist, `CL = 0` outright — absence of forward guidance is
treated as a meaningful signal, not scored as a floor default.

*Why not just phrase-match [C]/[D] like everything else?* Every 8-K is
legally required to hedge forward statements somewhere for SEC safe-harbor
compliance — "we expect... in the range of..." appears in nearly every
filing regardless of how genuinely confident management is. A blunt phrase
count treats boilerplate legal hedging identically to a real communicative
choice. Restricting the analysis to sentences that are *actually* forward
guidance (verb + number) is what makes CL measure something real instead of
legal-boilerplate density — this is the exact reason [C]/[D] were retired
from scoring (§13).

## 9. SE — Selective Emphasis

Six 0–5 sub-signals, all from simple text-position and phrase-count
arithmetic — no WSC, no spaCy sentence parsing needed. Four supporting
vocabularies (not tiered — presence/count only):

```python
SE_NONGAAP = ["adjusted earnings per share", "adjusted operating income",
    "adjusted gross margin", "adjusted revenue", "adjusted ebitda",
    "adjusted net income", "adjusted earnings", "adjusted free cash flow",
    "non-gaap eps", "non-gaap net income", "non-gaap revenue", "non-gaap",
    "pro forma", "core earnings", "organic revenue", "organic growth",
    "constant currency", "normalized earnings", "underlying earnings",
    "cash earnings", "adjusted", "excluding", ... ]   # 46 phrases total

SE_GAAP = ["gaap net income", "gaap earnings per share", "gaap eps",
    "gaap revenue", "reported net income", "reported revenue", "as reported",
    "gaap", "net income", "net loss", "total revenue", "diluted eps",
    "basic eps"]   # 21 phrases

SE_NEGATIVE = ["below expectations", "below consensus", "below guidance",
    "missed expectations", "net loss", "operating loss", "write-down",
    "write-off", "impairment charge", "restructuring charge",
    "margin compression", "margin decline", "revenue decline",
    "earnings decline", "declined", "decreased", "deteriorated", "shortfall",
    "missed", "unfavorable", "adverse impact", "negative impact",
    "volume decline", ... ]   # 41 phrases

SE_DEMOTION = ["reconciliation of gaap to non-gaap", "a reconciliation of",
    "please refer to the reconciliation", "reconciliation table",
    "financial tables below", "supplemental financial data",
    "non-gaap financial measures", "use of non-gaap financial measures", ...]
    # 25 phrases — relegation-to-tables language

SE_POS_FACT = ["we delivered", "we achieved", "we generated", "we grew",
    "record revenue", "record earnings", "revenue grew", "strong growth",
    "robust growth", ... ]   # 27 phrases — positives stated without hedging

SE_EXT_BLAME = ["due to macro", "due to currency", "due to fx",
    "due to supply chain", "impacted by", "negatively impacted by",
    "driven by macro", "driven by headwinds", "resulting from market",
    "attributable to external", "partially offset by", ... ]  # 42 phrases
```

| Signal | Question | Formula sketch |
|---|---|---|
| **D1** — Opening Position | Does non-GAAP language appear before GAAP language? | `gap_pct = (first_GAAP_pos - first_nonGAAP_pos) / doc_length` → 0–5 pts |
| **D2** — Volume Density | Non-GAAP mentions vs. GAAP mentions | `ng_ratio = ng_count / (ng_count + g_count)` → 1–5 pts |
| **D3** — Headline Capture | Non-GAAP language in the first 500 characters | weighted hit count in opening → 0–5 pts. *Excluded from v3, see §13.* |
| **D4** — Bad News Burial | Negative-result terms concentrated in the back 40%? | `burial_pct = neg_back / (neg_front + neg_back)` → 1–5 pts |
| **D5** — GAAP Demotion | Is GAAP relegated to reconciliation tables/footnotes? | count of `SE_DEMOTION` hits → 0–5 pts |
| **D6** — Qualifier Asymmetry | Positives stated as fact vs. negatives externally blamed | `blame_ratio = ext_blame_hits / pos_fact_hits` → 1–5 pts |

v2's `se_score = round((D1+...+D6) × 1.5)`, capped 25 — all six summed and
linearly rescaled. v3 treats the six very differently (§11).

## 10. `hype_score` (v2) — four equal buckets

```python
hype_score = qo_score + se_score + cl_score + sa_score   # each 0-25, sum 0-100
```

Calibrated on a 100-row pilot after one full recalibration round — the first
formula had a ceiling problem (max 68, mean 40, scale not utilized). After
recalibration: **mean 53.8, max 87 (Tyler Technologies), min 12 (HOOD, a
truncated filing)**.

## 11. `hype_v3` (rev3) — eight data-driven criteria

**Why we moved past v2:** four equal 0–25 buckets assumes QO, SE, CL, and SA
are equally informative about "hype." Nothing forced that assumption to be
true. We tested it directly: ran the scorer on 1,000 filings with full
diagnostic instrumentation (every raw sub-signal, not just the four final
scores), then regressed `hype_score` on all 16 standardized raw signals via
OLS. The resulting standardized coefficients told us, empirically, which
signals actually carried discriminating power:

| Signal | β | |
|---|---|---|
| `wsc_e` (internal attribution) | **+6.70** | strongest driver in the dataset |
| `wsc_a` (superlatives) | +5.01 | second strongest |
| SE's D5 (GAAP demotion) | +3.93 | |
| CL's guidance density | +3.58 | |
| SE's D6 (qualifier asymmetry) | +2.74 | |
| CL's precision count | +2.69 | |
| `wsc_b` (vague positives) | +2.39 | |
| SE's D1 (opening position) | +1.88 | |
| `wsc_f` (external blame) | −1.38 | |
| `wsc_c`, `wsc_d` (§13) | +0.59 (p=0.02), +0.47 (**n.s.**, p=0.09) | excluded |

This directly overturned our starting intuition (that QO deserved the
biggest weight boost): the data said self-attribution is the single
strongest driver, with QO's superlative signal a close second.

```python
def compute_hype_v3(wsc, cl_diag, se_signals):
    e, a, b = wsc["E"], wsc["A"], wsc["B"]
    d1, d2, d4, d5, d6 = se_signals["d1"], se_signals["d2"], se_signals["d4"], se_signals["d5"], se_signals["d6"]
    gd, pc = cl_diag["cl_guidance_density"], cl_diag["cl_precision_count"]
    strong_modal, weak_modal = cl_diag["cl_strong_modal_count"], cl_diag["cl_weak_modal_count"]
    ud = cl_diag["cl_unc_density"]

    # C1 - SA-Attribution (wsc_e), max 22
    if   e == 0:        c1 = 0
    elif e <= 0.0188:   c1 = 6
    elif e <= 0.0606:   c1 = 11
    elif e <= 0.1111:   c1 = 16
    elif e <= 0.1771:   c1 = 19
    else:                c1 = 22

    # C2 - QO-Superlative (wsc_a), max 18
    if   a == 0:        c2 = 0
    elif a <= 0.0405:   c2 = 5
    elif a <= 0.1135:   c2 = 9
    elif a <= 0.1852:   c2 = 13
    elif a <= 0.3209:   c2 = 16
    else:                c2 = 18

    # C3 - SE-Concealment: D4(burial, shifted 0-4) + D5(demotion, 0-5), scaled to 0-14
    concealment_raw = max(0, min(4, d4 - 1)) + max(0, min(5, d5))
    c3 = max(0, min(14, round(concealment_raw * (14 / 9))))

    # C4 - CL-GuidanceStrength: density + precision + modal-strength bonus, max 15
    gd_pts = 0 if gd == 0 else (2 if gd <= 0.0193 else (5 if gd <= 0.0526 else 7))
    pc_pts = 0 if pc == 0 else (2 if pc <= 2 else (4 if pc <= 5 else 5))
    modal_total = strong_modal + weak_modal
    if modal_total == 0:
        modal_pts = 0
    else:
        modal_ratio = strong_modal / modal_total
        modal_pts = 3 if modal_ratio > 0.5 else (1 if modal_ratio > 0.2 else 0)
    c4 = min(15, gd_pts + pc_pts + modal_pts)

    # C5 - SE-QualifierAsymmetry (D6), max 8
    c5 = max(0, min(8, round((d6 - 1) / 4 * 8))) if d6 else 0

    # C6 - QO-VaguePositive (wsc_b), max 7, unchanged from v2
    if   b == 0:      c6 = 0
    elif b <= 0.05:   c6 = 2
    elif b <= 0.12:   c6 = 4
    else:              c6 = 7

    # C7 - SE-Framing: D1 + D2 merged, max 10
    c7 = min(10, d1 + d2)

    # C8 - CL-Uncertainty: pure uncertainty-density penalty, 0 to -2
    c8 = 0
    if   ud > 0.0469: c8 = -2
    elif ud > 0.0238: c8 = -1

    raw_total = c1 + c2 + c3 + c4 + c5 + c6 + c7 + c8
    V3_SCALE = 1.1688   # 90/77, calibrated on the 1000-row run
    return max(0, min(100, round(raw_total * V3_SCALE)))
```

| # | Criterion | Range | Grounded in |
|---|---|---|---|
| C1 | SA-Attribution | 0–22 | `wsc_e` — strongest driver |
| C2 | QO-Superlative | 0–18 | `wsc_a` — second strongest |
| C3 | SE-Concealment | 0–14 | SE's D4 (burial) + D5 (demotion) merged — independent, r=−0.01 |
| C4 | CL-GuidanceStrength | 0–15 | guidance density + precision + modal-strength bonus |
| C5 | SE-QualifierAsymmetry | 0–8 | SE's D6 |
| C6 | QO-VaguePositive | 0–7 | `wsc_b`, unchanged from v2 |
| C7 | SE-Framing | 0–10 | SE's D1+D2 merged — r=0.53, genuinely redundant, so merged not split |
| C8 | CL-Uncertainty | 0 to −2 | pure uncertainty-density penalty |

Raw criterion sum is rescaled by a constant factor (`V3_SCALE = 1.1688 =
90/77`) so the top of the empirical distribution lands at ~90 — the same
"observed ceiling → rescale" fix v2 already used for SE (`×1.5`).

**Both scores are written to every output row.** The paper can report
`hype_v3` as primary with `hype_score` as a robustness check, or vice versa
— a methodology-writing decision for the team, not something baked into the
pipeline.

## 12. Stress-test history — three rounds, real bugs fixed

We did not treat the first version of `hype_v3` as final. Every round below
was caught by re-running the same 1,000 filings and checking specific named
companies against what we already knew about them from v2, not just
aggregate statistics.

- **Rev 1** (first cut): dropped SE's D4 (burial) and D3 (headline) entirely
  since their *average* OLS importance was low. This caused large,
  spurious-looking swings for filings whose hype behavior happened to
  concentrate in exactly those signals — e.g. **EQT's 2020-01-13 filing**
  scored SE=25/25 in v2 (driven mostly by D4=5, maximum burial) and
  collapsed under rev 1, dropping its overall rank from 614 to 930 out of
  1,000 for reasons that had nothing to do with its actual hype language
  changing.
- **Rev 2** (fix #1): reinstated D4, merged into C3 (SE-Concealment) rather
  than given its own token slot — a first attempt at a tiny standalone D4
  criterion only reached r=0.055 with the total, too weak to matter; merging
  with D5 reached r=0.31. Also **removed** the SA-ExternalBlame criterion
  entirely: measured live, it correlated r=−0.059 with the total —
  statistically indistinguishable from zero, because `wsc_f=0` (no blame
  language) in over half of all filings. A criterion with no discriminating
  power doesn't belong in the formula just to hit a target count.
- **Rev 3** (fix #2, a real bug, not just a gap): while checking why NFLX,
  MTD, and SBUX dropped 15–19 points under rev 2 despite writing unusually
  assertive, strong-modal guidance language, we found the hedging modifier
  had an **inverted sign** — `if modal_ratio > 0.5: c8 -= 2` was subtracting
  points for the *most* assertive, least-hedged filings (high modal_ratio =
  mostly strong modals like "will"), exactly backwards from the theory.
  Confirmed on NFLX's 2018-01-22 filing (modal_ratio = 1.0, 100% strong
  modals, zero hedging) being pulled toward a penalty it didn't deserve.
  Fixed by moving modal-strength scoring into C4 with the correct sign, and
  narrowing C8 to a pure uncertainty-density penalty with no overlap.

**Net effect across all three rounds:** correlation with v2 rose
0.932 → 0.940 → **0.949**, and mean absolute rank shift vs. v2 fell
85 → 80 → **75** (out of 1,000) — each fix converged the formula rather than
introducing a new problem, which is why rev 3 was judged ready for the full
14,351-row run. Full row-level data for all four runs (v2 baseline + v3
rev1/rev2/rev3) is versioned in
[`calibration_samples/`](calibration_samples/README.md).

## 13. Known limitations

- **`wsc_c` (Certainty Assertions, [C]) and `wsc_d` (Epistemic Hedges, [D])
  are computed but not scored.** They correlate only 0.22–0.29 with CL's own
  sentence-level guidance/uncertainty signals — a cruder, redundant first
  attempt at the same construct CL now measures more precisely (§8). Not a
  bug: checked against live data, confirmed weak/redundant, not just unused
  by oversight.
- **D3 (headline capture)** fires in only 18% of filings and correlates
  0.18–0.28 with D1/D2 — a narrower re-measurement of what D1/D2 already
  capture across the whole document. Excluded from v3 for the same reason.
- Future work (not done — flagged for after the deadline): an LLM pass on
  filings where `wsc_c`/`wsc_d` disagree sharply with CL's signals could
  determine whether this is a genuine vocabulary coverage gap or confirms
  the redundancy.

## 14. Running it

```powershell
cd C:\Users\khash\Desktop\FINTech_HypeAnalysis\khashi
pip install spacy --break-system-packages
python -m spacy download en_core_web_sm
python -m py_compile khashi_scorer.py     # always compile-check before a full run
python khashi_scorer.py
```

Key settings in the `CONFIGURATION` block at the top of `khashi_scorer.py`:

- `SAMPLE_SIZE` — `0` for the full dataset, or an integer for a random
  (seeded, `RANDOM_SEED=42`) subsample during calibration.
- `RESUME` — `True` to safely interrupt/resume the full run across sessions
  without rescoring rows; `False` for a clean rescore (delete the output CSV
  first if reusing a filename with `RESUME=False`).
- `OUTPUT_CSV` / `ERRORS_CSV` — change the filename for each new methodology
  version so runs stay comparable row-for-row.

**Known environment issue:** the sync layer between some editor/tooling
sessions and disk can occasionally serve a stale view of `khashi_scorer.py`
mid-edit. Always run `python -m py_compile khashi_scorer.py` yourself before
a long run — don't trust a compile check from another tool without also
confirming locally.

## 15. Output schema

One row per filing: identifying fields (`ticker`, `cik`, `filingDate`,
`accessionNumber`, `source`), raw WSC signals (`wsc_a`–`wsc_f`), v2's four
scores (`qo_score`, `se_score`, `cl_score`, `sa_score`, `hype_score`) each
with a human-readable reasoning string, v3's score and eight criteria
(`hype_v3`, `v3_c1_sa_attribution` … `v3_c8_cl_uncertainty`, `v3_reasoning`),
and ~20 `diag_*` diagnostic columns (raw SE/CL sub-signals) kept for any
future recalibration — not part of either score, safe to ignore if you only
need the final numbers.

## 16. Every paper, and exactly what it grounds

| Citation | Grounds |
|---|---|
| Loughran, T., & McDonald, B. (2011). When is a liability not a liability? Textual analysis, dictionaries, and 10-Ks. *The Journal of Finance*, 66(1), 35–65. | `LM_STRONG_MODAL` / `LM_WEAK_MODAL` / `LM_UNCERTAINTY` word lists — the entire CL sentence-level scoring approach (§8), and the theoretical case for why phrase-list hedging measures ([C]/[D]) are too blunt for legally-mandated hedge language (§13). |
| Henry, E. (2008). Are investors influenced by how earnings press releases are written? *The Journal of Business Communication*, 45(4), 363–407. | General case that press-release *writing style* (not just content) measurably affects investor reaction — the underlying premise for treating tone/framing as a legitimate, separate object of study from the reported numbers. |
| Baginski, S. P., Hassell, J. M., & Hutton, A. P. (2004). Management earnings forecast disclosures and the causal attribution of forecast outcomes. *Journal of Accounting Research* (attribution literature). | The internal/external attribution asymmetry — direct theoretical basis for [E]/[F] and the SA formula (§7): management systematically credits itself for good news and blames the environment for bad news. |
| Huang, X., Teoh, S. H., & Zhang, Y. (2014). Tone management. *The Accounting Review*, 89(3), 1083–1113. (ABTONE methodology) | The general "abnormal tone" framework this project's QO measure descends from — quantifying optimistic language in disclosures as a distinct, testable construct, separate from whether the underlying results justify it. |
| Bochkay, K., Hales, J., & Chava, S. (2020). Hyperbole or reality? Investor response to extreme language in earnings conference calls. *The Accounting Review*, 95(2), 31–60. | Supporting bridge: extreme/hyperbolic language in earnings communications produces measurable, separable investor reactions — reinforces the hypothesis (§1) that hype language and information content are distinguishable, testable signals. |

## 17. LLM validation pass on ambiguous SE rows

The full 14,351-row run (§14) is, as noted in §2, entirely deterministic —
the SE component's second stage (an LLM synthesising the D1-D6 signals into
a final score) was designed but disabled for speed. That means the finished
pipeline had zero actual LLM/agent judgment anywhere despite being designed
for it.

A scoped follow-up closes that gap without re-running or re-architecting
anything: only the 1,067 filings (7.4% of the dataset) where the SE
sub-signals genuinely conflict — strong promotion signals (D1+D2) alongside
weak concealment signals (D4+D5), or the reverse — get an LLM call, using
Groq's hosted `llama-3.1-8b-instant`. Everything else keeps its
deterministic score unchanged; this is a validation/robustness pass, not a
rescoring.

**Result:** 1,064 of 1,067 rows scored (99.7%). Correlation between the
deterministic and LLM SE scores is 0.75, mean difference is +0.15 (i.e., no
systematic bias, the LLM mostly confirms the formula), and 89% of rows
agree within 3 points on a 0–25 scale.

**Finding:** the disagreements aren't random. A recurring +5-point gap
(deterministic=9, LLM=14) appears 14 times, and 11 of those share one exact
D1-D6 signature (D1=1, D2=3, D3=0, D4=1, D5=0, D6=1) — moderate non-GAAP
density with quiet, non-obvious burial (no headline push, no explicit
demotion language). The deterministic formula consistently undercounts this
specific pattern; the LLM consistently catches it. That's a named,
reproducible limitation of the keyword-based approach, not unexplained
noise.

Full methodology, implementation details, the complete results table, and
the folder contents are documented separately in
[`llm_validation/README.md`](llm_validation/README.md), kept apart from
this file so the primary deterministic methodology and this validation
add-on don't get tangled together.
