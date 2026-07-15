# Khashi HypeScore Scorer — WSC Methodology

Standalone, deterministic scoring pipeline for corporate hype language in SEC
Form 8-K Item 2.02 earnings press releases. Built for the TUM seminar project
*"Advanced Topics in FinTech: AI Agents and Blockchain"* (SS26) — workstream:
**Corporate Hype Narratives & Stock Price Reversals**.

This is a separate pipeline from [`src/`](../src/) (the Ollama/LLM-based vagueness
scorer documented in the repo root [README.md](../README.md)). This one is pure
Python + spaCy — no LLM calls, fully deterministic, reproducible, and fast enough
to run on the full ~14,351-filing dataset in about 1.5 hours on a single laptop.

## What it measures, and why

**Hypothesis:** unusually high "hype" language in an earnings press release
predicts a stock price reversal — management overclaims, the market initially
rewards it, then corrects. `HypeScore` (0–100) operationalizes "hype" so this can
be tested statistically across thousands of filings.

## Methodology

Six word/phrase categories **[A]–[F]** are detected via spaCy `PhraseMatcher`
(exact, case-insensitive phrase matching — not semantic/embedding-based) using a
tiered vocabulary (~300 phrases). Matches are aggregated per category into a
**Weighted Sentence Coverage (WSC)** score:

```
WSC_X = Σ [ MAX_tier_weight(sentence_i) × position_weight_i ] / n_sentences
```

- Positional weights: first third of the filing = 2.0, middle = 1.0, last third = 0.5
  (front-loaded language counts more — headline framing matters more than boilerplate).
- Tier weights: Tier 1 = 3, Tier 2 = 2, Tier 3 = 1.
- **MAX, not SUM**, per sentence — prevents one hype-dense paragraph from dominating.

Category → construct mapping:

| Category | Construct | Feeds into |
|---|---|---|
| [A] Superlatives, [B] Vague positives | Qualitative Optimism | QO / v3 C2, C6 |
| [E] Internal attribution, [F] External blame | Self-Attribution Bias | SA / v3 C1 |
| [C] Certainty assertions, [D] Epistemic hedges | (superseded — see Known Limitations) | not scored |

**CL** (Claim-Leniency / certainty language) does **not** use [C]/[D]. It runs a
separate sentence-level pass: find sentences with both a guidance verb
("expect," "target," "guidance"...) and a $/% figure, then score modal strength
(Loughran & McDonald 2011 strong/weak modal word lists) and precision
("in the range of $X to $Y") only within those sentences. This is more precise
than phrase-matching because SEC safe-harbor rules require every 8-K to hedge
forward statements somewhere — phrase-matching alone can't distinguish
boilerplate legal hedging from a genuine communicative choice.

**SE** (Selective Emphasis) is six deterministic Python sub-signals (D1–D6),
not LLM-scored (an LLM synthesis path exists in the code but is disabled — it
took 60–80s/row on CPU; Python is <1s and the D-signals are already
interpretable on their own):

| Signal | What it measures |
|---|---|
| D1 | Does non-GAAP language appear before GAAP language? |
| D2 | Non-GAAP mention density vs. GAAP mention density |
| D3 | Non-GAAP language in the opening 500 characters (headline) |
| D4 | Are negative-result terms concentrated in the back 40% of the filing? |
| D5 | Is GAAP relegated to reconciliation tables / footnotes? |
| D6 | Are positives stated as fact while negatives are externally blamed? |

## Two scoring formulas, both in the output

- **`hype_score`** (v2) = QO + SE + CL + SA, four equal-weighted 0–25 buckets.
  Calibrated on a 100-row pilot: mean 53.8, max 87 (Tyler Technologies), min 12
  (HOOD, truncated filing).
- **`hype_v3`** (rev3) = 8 data-driven, unequally-weighted criteria, calibrated
  and stress-tested across three 1000-row validation rounds via OLS regression
  of `hype_score` on all raw signals. See `compute_hype_v3()` docstring in
  `khashi_scorer.py` for the full derivation, criterion-by-criterion weights,
  and the two real bugs found and fixed during stress-testing (a dead
  external-blame criterion removed in rev2; a sign-inverted modal-ratio penalty
  fixed in rev3). Both formulas are written to every output row so the paper
  can report v3 as primary and v2 as a robustness check, or vice versa.

**Do not treat either as final until you've read the stress-test history** —
particularly which raw signals were tried, dropped, or merged, and why. That
reasoning is the actual methodological contribution here, not just the final
formula.

## Known limitations (documented, not hidden)

- **[C] Certainty Assertions and [D] Epistemic Hedges are computed but not
  scored.** They correlate only 0.22–0.29 with CL's own sentence-level
  guidance/uncertainty signals — they're a cruder, redundant first attempt at
  the same construct CL now measures more precisely, not missing information.
  Retained as diagnostic columns (`wsc_c`, `wsc_d`) for transparency.
- **D3 (headline capture)** fires in only 18% of filings and correlates
  0.18–0.28 with D1/D2 — it's a narrower re-measurement of what D1/D2 already
  capture across the whole document, not independent signal. Excluded from v3.
- Neither of the above is a bug — both were checked against live data before
  being excluded. Future work: an LLM pass on filings where wsc_c/wsc_d
  disagree sharply with CL could validate whether this is a genuine vocabulary
  coverage gap or confirms the redundancy.

## Running it

```powershell
cd C:\Users\khash\Desktop\FINTech_HypeAnalysis\khashi
python -m py_compile khashi_scorer.py     # always compile-check before a full run
python khashi_scorer.py
```

Requirements: `pip install spacy --break-system-packages` and
`python -m spacy download en_core_web_sm`.

Key settings in the `CONFIGURATION` block at the top of `khashi_scorer.py`:

- `SAMPLE_SIZE` — `0` for the full dataset, or an integer for a random
  (seeded) subsample during calibration.
- `RESUME` — `True` to append/skip already-scored rows across interrupted
  runs (safe for the full 14k run); `False` for a clean rescore (delete the
  output CSV first if reusing a filename with `RESUME=False`).
- `OUTPUT_CSV` / `ERRORS_CSV` — change the filename for each new
  methodology version so runs stay comparable row-for-row.

**Known environment issue:** on some machines the file-sync layer between an
editor/tooling session and disk can serve a stale view of `khashi_scorer.py`
mid-edit. Always run `python -m py_compile khashi_scorer.py` yourself before a
long run — don't trust a green checkmark from another tool without also
confirming locally.

## Output schema

One row per filing: identifying fields (`ticker`, `cik`, `filingDate`,
`accessionNumber`, `source`), raw WSC signals (`wsc_a`–`wsc_f`), both scores
(`qo_score`/`se_score`/`cl_score`/`sa_score`/`hype_score` for v2,
`hype_v3` + `v3_c1`…`v3_c8` for v3), human-readable reasoning strings for every
score, and ~20 `diag_*` diagnostic columns (raw SE/CL sub-signals) kept for
future recalibration — not part of either score, safe to ignore if you only
need the final numbers.

## Academic grounding

- Loughran, T., & McDonald, B. (2011). When is a liability not a liability?
  Textual analysis, dictionaries, and 10-Ks. *The Journal of Finance*, 66(1), 35–65.
- Henry, E. (2008). Are investors influenced by how earnings press releases
  are written? *The Journal of Business Communication*, 45(4), 363–407.
- Baginski, S. P., Hassell, J. M., & Hutton, A. P. (2004). Is management
  earnings forecast credibility affected by other forecast disclosures?
  *Journal of Accounting Research*, wider causal-attribution literature.
