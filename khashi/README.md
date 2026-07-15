# Khashi HypeScore Scorer — Methodology Guide

Standalone, deterministic scoring pipeline for corporate hype language in SEC
Form 8-K Item 2.02 earnings press releases. Built for the TUM seminar project
*"Advanced Topics in FinTech: AI Agents and Blockchain"* (SS26) — workstream:
**Corporate Hype Narratives & Stock Price Reversals**.

This document is written so anyone on the team can pick up the codebase cold:
what we measure, why we measure it that way, what the vocabularies actually
contain, and the full history of what we tried, broke, and fixed to get here.

---

## 1. The hypothesis

Unusually high "hype" language in an earnings press release predicts a stock
price **reversal**: management overclaims in the language of the release, the
market initially rewards the tone, and the price corrects once the numbers
catch up. `HypeScore` (0–100) turns "hype" into a number so this can be tested
statistically across the full dataset (~14,351 S&P 500 8-K Item 2.02 filings,
2018–2025) rather than argued about qualitatively.

## 2. Why Python + spaCy, not an LLM

Scoring 14,351 filings sentence-by-sentence with an LLM is too slow and not
reproducible run-to-run (temperature, model drift, rate limits). Everything in
this pipeline is **deterministic vocabulary + rule-based scoring** — same
input text always produces the same score, forever, on any machine. An LLM
path exists in the code for one narrow use (SE's signal synthesis) but is
disabled: it took 60–80s/row on CPU versus <1s for the Python formula, for no
measurable accuracy gain over the six-signal breakdown it would have
summarized anyway.

## 3. Step-by-step: what happens to a filing

1. **Load** the row from `item202_clean.csv` (`ticker, cik, filingDate,
   accessionNumber, source, item_202_text`).
2. **Strip safe-harbor boilerplate** (`remove_safe_harbor()`): sentences in
   the last 40% of the text that contain phrases like *"forward-looking
   statements"* or *"actual results may differ"* AND are under 80 words are
   removed. We only touch the back 40% and only short sentences, because many
   8-Ks have no paragraph breaks — stripping too aggressively would delete
   real content that happens to mention "forward-looking" once.
3. **Truncate** to the first 3,000 words (`SPACY_MAX_WORDS`) — long enough to
   capture the guidance section and management commentary, short enough to
   keep spaCy fast across 14k filings.
4. **Parse with spaCy** (`en_core_web_sm`, NER and the lemmatizer disabled —
   we don't need either, and disabling them roughly doubles throughput).
5. **Match six vocabulary categories [A]–[F]** against every sentence via
   `PhraseMatcher` (see §4).
6. **Aggregate into WSC scores** — one float per category (see §5).
7. **Run CL's separate sentence-level pass** (guidance-sentence detection +
   modal analysis, see §7).
8. **Run SE's six deterministic sub-signals** (`extract_se_signals()`, see
   §8).
9. **Combine into two parallel final scores**: `hype_score` (v2, four
   equal-weighted components) and `hype_v3` (rev3, eight data-driven
   criteria) — both written to every output row, see §9–§10.

## 4. The six vocabulary categories [A]–[F]

Matching is **exact phrase, case-insensitive** (`PhraseMatcher` with
`attr="LOWER"`) — not semantic or embedding-based. This is a deliberate
precision-over-recall tradeoff: a fixed vocabulary is auditable (you can point
to exactly which phrase triggered a score) and perfectly reproducible; a
semantic matcher would catch paraphrases we missed but couldn't be defended
sentence-by-sentence to a reviewer, and its behavior could drift between runs.
The lemmatizer is disabled, so morphological variants ("grew" vs. "growing")
must be listed separately if we want them caught — this is a real recall
limitation, documented, not hidden.

Every phrase has a **tier weight** (1, 2, or 3 — stronger claim = higher
tier), and every category has category-specific **suppression rules** so the
matcher doesn't fire on boilerplate or negated language.

| Cat | Name | Feeds into | Suppression rule |
|---|---|---|---|
| **[A]** | Strong Superlatives | QO | Exclusion list (below) |
| **[B]** | Unanchored Vague Positives | QO | Anchor suppression (below) |
| **[C]** | Certainty Assertions | *not scored — see §11* | — |
| **[D]** | Epistemic Hedges | *not scored — see §11* | — |
| **[E]** | Internal Attribution | SA | — |
| **[F]** | External Attribution | SA | — |

**[A] Strong Superlatives** — unambiguous marketing language. Tier 1 (weight
3): *"record," "unprecedented," "world-class," "best-in-class," "historic
milestone," "exceptional," "phenomenal."* Tier 2 (weight 2): *"remarkable,"
"impressive," "cutting-edge," "breakthrough."* Tier 3 (weight 1): *"notable,"
"milestone," "strong momentum."* ~60 phrases total.

*Why an exclusion list:* "record" and "outstanding" are genuinely ambiguous —
"record revenue" is hype, "outstanding shares" and "record date" are neutral
accounting/legal terms. `A_EXCLUSIONS` suppresses the match when the phrase
appears next to context like *"outstanding balance," "outstanding shares,"
"track record," "record date," "landmark ruling."* Without this, [A] would
have been badly noisy — this exclusion list came directly from reading real
false positives during calibration.

**[B] Unanchored Vague Positives** — words like *"strong," "robust," "solid,"
"well-positioned," "optimistic," "ahead of expectations"* (tier 1, weight 2)
and *"good," "great," "resilient," "confident," "improving"* (tier 2, weight
1). ~26 phrases.

*Why "unanchored":* the whole point of [B] is to catch positive language that
is **not backed by a specific number**. `ANCHOR_PATTERN` suppresses a match if
a dollar figure, percentage, or magnitude word ("billion," "million," "bps")
appears within 80 characters *after* the match. "Strong growth" (vague) scores
differently from "strong growth of 12%" (anchored, arguably not hype — it's a
verifiable claim). This is the single most theoretically important
suppression rule in the whole vocabulary: it's what makes QO measure
*unsubstantiated* optimism specifically, not just positive language in
general.

**[C] Certainty Assertions** and **[D] Epistemic Hedges** — phrase lists for
guidance commitment ("we will deliver," "reaffirming guidance," "raising our
guidance" — [C], tier weights 2–3) and hedging ("we believe," "subject to,"
"in the range of," "no assurance" — [D], tier weights 1–2). ~57 and ~38
phrases respectively. **These are computed (`wsc_c`, `wsc_d`) but not used in
either final score** — see §11 for why.

**[E] Internal Attribution** — management crediting itself: *"driven by our
strategy," "our execution," "our team delivered," "reflecting our strategic,"
"our operational excellence"* (tier 3) down to short forms like *"we
achieved," "we delivered," "our platform," "execute our"* (tier 1–2). ~60
phrases. Grounded in Baginski et al. (2004) on causal attribution in earnings
disclosures — the accounting-literature finding that management systematically
over-attributes good news to its own skill and bad news to the environment.

**[F] External Attribution** — the mirror image: blaming outside forces for
weak results. *"headwinds," "fx impact," "supply chain disruption,"
"inflationary pressure," "geopolitical," "challenging environment"* (tier 3)
down to *"external factors," "market volatility," "weaker demand"* (tier 2).
~46 phrases. [E] and [F] together operationalize the Baginski attribution
asymmetry directly: heavy [E] + light [F] = "I did this"; heavy [F] = "the
market did this to me."

## 5. Weighted Sentence Coverage (WSC) — the core formula

```
WSC_X = Σ [ MAX_tier_weight(sentence_i) × position_weight_i ] / n_sentences
```

Three design choices, each deliberate:

- **Position weighting** (first third × 2.0, middle × 1.0, last third × 0.5):
  front-loaded language counts more. A press release's opening paragraph is
  the headline framing choice; the same superlative buried in paragraph 12 is
  much less likely to be the thing that moved the market's initial reaction.
- **MAX, not SUM, per sentence**: if one sentence has three tier-1
  superlatives, it counts once at the tier-1 weight, not three times. This
  stops a single hype-dense sentence from dominating the whole document's
  score — we want to measure how *pervasive* the language is, not how
  repetitive one paragraph is.
- **Negation suppression**: a 4-token look-back window checks for *"not,"
  "never," "no," "without,"* etc. before counting a match — "not
  unprecedented" doesn't score as [A].

## 6. QO and SA — the two WSC-driven scores

**QO (Qualitative Optimism)** = point-band lookup on `wsc_a` (0–18 pts) +
`wsc_b` (0–7 pts), summed and capped at 25. Bands were calibrated empirically
on the 100-row pilot (see `khashi_scorer.py`'s `compute_qo()` docstring for
the exact cutoffs) — not asserted, fit to where the real distribution's
percentiles fell so the score actually spreads across 0–25 instead of
clustering near the mean.

**SA (Self-Attribution)** = tier lookup on `wsc_e` (0–22 pts) + a signed
modifier from `wsc_f` (−5 to +3). The modifier rewards *pure* internal credit
(`wsc_f == 0` → +3) and penalizes heavy external blame (`wsc_f > 0.15` → −5).

## 7. CL — Certainty Language (why it's not just [C]/[D])

CL does **not** use the [C]/[D] vocabulary. It runs a separate, more surgical
pass: find sentences containing both a guidance verb (*"expect," "target,"
"guidance," "outlook"*) **and** a $/% figure — only those sentences count as
"guidance sentences." Within just those sentences, it scores:

1. **Count** of guidance sentences (0–15 pts, banded).
2. **Modal strength** — ratio of Loughran-McDonald *strong* modals
   (*"will," "shall," "certainly"*) to *weak* modals (*"may," "could,"
   "approximately"*) within guidance sentences only (0–10 pts).
3. **Precision bonus** — does the guidance give an actual numeric range
   ("between $X and $Y")? (0–5 pts).
4. **Uncertainty penalty** — density of explicit uncertainty words
   (*"uncertain," "unpredictable," "contingent"*) across the whole filing
   (0 to −5 pts).

*Why not just phrase-match [C]/[D] like everything else?* Every 8-K is legally
required to hedge forward statements somewhere for SEC safe-harbor compliance
— "we expect... in the range of..." appears in nearly every filing regardless
of how genuinely confident management is. A blunt phrase count treats
boilerplate legal hedging the same as a real communicative choice. Restricting
the analysis to sentences that are *actually* forward guidance (verb + number)
is what makes CL measure something real instead of legal boilerplate density.

## 8. SE — Selective Emphasis (six deterministic sub-signals)

`extract_se_signals()` computes six 0–5 sub-signals per filing, all from
simple text-position and phrase-count arithmetic — no WSC, no spaCy sentence
parsing required:

| Signal | Question it answers |
|---|---|
| D1 — Opening Position | Does non-GAAP language appear before GAAP language? |
| D2 — Volume Density | Non-GAAP mention count vs. GAAP mention count |
| D3 — Headline Capture | Non-GAAP language in the first 500 characters |
| D4 — Bad News Burial | Are negative-result terms concentrated in the back 40%? |
| D5 — GAAP Demotion | Is GAAP relegated to reconciliation tables/footnotes? |
| D6 — Qualifier Asymmetry | Positives stated as fact vs. negatives externally blamed |

v2's `se_score` is these six signals summed (0–30 raw) and linearly rescaled
(`round(raw × 1.5)`, capped at 25). v3 treats them very differently — see §10.

## 9. `hype_score` (v2) — four equal buckets

```
hype_score = QO + SE + CL + SA        (each 0-25, sum 0-100)
```

Calibrated on a 100-row pilot after one full recalibration round (the first
formula had a ceiling problem: max 68, mean 40 — the 0–100 scale wasn't being
used). After recalibration: mean 53.8, max 87 (Tyler Technologies), min 12
(HOOD, a truncated filing).

## 10. `hype_v3` (rev3) — eight data-driven, unequally-weighted criteria

**Why we moved past v2:** four equal 0–25 buckets assumes qualitative
optimism, selective emphasis, certainty language, and self-attribution are
equally informative about "hype." Nothing forced that assumption to be true.
We tested it: ran the scorer on 1,000 filings with full diagnostic
instrumentation (every raw sub-signal, not just the four final scores), then
regressed `hype_score` on all 16 standardized raw signals via OLS. The
resulting standardized coefficients (β) told us, empirically, which raw
signals actually carried the discriminating power:

| Signal | β | Component |
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
| `wsc_c`, `wsc_d` (unused vocab) | +0.59 (p=0.02), +0.47 (**not significant**, p=0.09) | excluded — see §11 |

This directly overturned the intuitive assumption we started with (that QO
deserved the biggest weight boost): the data said self-attribution (`wsc_e`)
is the single strongest driver, with QO's superlative signal a close second.

**The eight criteria**, ranked by that evidence:

| # | Criterion | Range | What it measures |
|---|---|---|---|
| C1 | SA-Attribution | 0–22 | `wsc_e` — strongest driver |
| C2 | QO-Superlative | 0–18 | `wsc_a` — second strongest |
| C3 | SE-Concealment | 0–14 | SE's D4 (burial) + D5 (demotion) merged — independent signals, r=−0.01 with each other |
| C4 | CL-GuidanceStrength | 0–15 | guidance density + precision count + modal-strength bonus |
| C5 | SE-QualifierAsymmetry | 0–8 | SE's D6 |
| C6 | QO-VaguePositive | 0–7 | `wsc_b`, band unchanged from v2 |
| C7 | SE-Framing | 0–10 | SE's D1+D2 merged — these correlate r=0.53 with each other, genuinely redundant, so merged rather than kept as two criteria |
| C8 | CL-Uncertainty | 0 to −2 | pure uncertainty-density penalty |

Raw criterion sum is rescaled by a constant factor (`V3_SCALE = 1.1688 =
90/77`) so the top of the empirical distribution lands at ~90 — the same
"observed ceiling → rescale" fix v2 already used for SE (`×1.5`).

### The stress-test history — three rounds, real bugs found and fixed

We did not treat the first version as final. Every round below was caught by
re-running the same 1,000 filings and checking specific named companies
against what we already knew about them, not just aggregate statistics.

- **Rev 1** (first cut): dropped SE's D4 (burial) and D3 (headline) entirely
  since their *average* OLS importance was low. This caused large,
  spurious-looking swings for filings whose hype behavior happened to
  concentrate in exactly those signals — e.g. **EQT's 2020-01-13 filing**
  scored SE=25/25 in v2 (driven mostly by D4=5, maximum burial) and collapsed
  to a much lower SE-equivalent under rev 1, dropping its overall rank from
  614 to 930 out of 1,000 for reasons that had nothing to do with its actual
  hype language changing.
- **Rev 2** (fix #1): reinstated D4, merged into C3 (SE-Concealment) rather
  than given its own token slot — a first attempt at a tiny standalone D4
  criterion only reached r=0.055 with the total, too weak to matter; merging
  with D5 reached r=0.31. Also **removed** the SA-ExternalBlame criterion
  entirely: measured live, it correlated r=−0.059 with the total —
  statistically indistinguishable from zero, because `wsc_f=0` (no blame
  language) in over half of all filings. A criterion with no discriminating
  power doesn't belong in the formula just to hit a target count.
- **Rev 3** (fix #2, a real bug not just a gap): while checking why NFLX,
  MTD, and SBUX dropped 15–19 points under rev 2 despite writing unusually
  assertive, strong-modal guidance language, we found the hedging modifier
  had an **inverted sign** — it was subtracting points for *high* modal-ratio
  filings (mostly strong, certain modals like "will"), which is backwards:
  high modal-ratio means *less* hedging, not more. Confirmed directly on
  NFLX's 2018-01-22 filing (modal_ratio = 1.0, 100% strong modals, zero
  hedging) being pulled toward a penalty it didn't deserve. Fixed by moving
  modal-strength scoring into C4 with the correct sign, and narrowing C8 to a
  pure uncertainty-density penalty with no overlap.

**Net effect across all three rounds:** correlation with v2 rose
0.932 → 0.940 → **0.949**, and mean absolute rank shift vs. v2 fell
85 → 80 → **75** (out of 1,000) — each fix converged the formula rather than
introducing a new problem, which is why rev 3 was judged ready for the full
14,351-row run. Full row-level data for all four runs (v2 baseline + v3
rev1/rev2/rev3) is versioned in
[`calibration_samples/`](calibration_samples/README.md).

**Both scores are written to every output row.** The paper can report
`hype_v3` as primary with `hype_score` as a robustness check, or vice versa —
that choice is a methodology-writing decision for the team, not something
baked into the pipeline.

## 11. Known limitations — checked, not hidden

- **`wsc_c` (Certainty Assertions) and `wsc_d` (Epistemic Hedges) are computed
  but not scored.** They correlate only 0.22–0.29 with CL's own sentence-level
  guidance/uncertainty signals — a cruder, redundant first attempt at the same
  construct CL now measures more precisely (see §7 for why CL exists as a
  separate pass). Not a bug: checked against live data, confirmed weak/
  redundant, not just unused by oversight.
- **D3 (headline capture)** fires in only 18% of filings and correlates
  0.18–0.28 with D1/D2 — a narrower re-measurement of what D1/D2 already
  capture across the whole document. Excluded from v3 for the same reason.
- Future work (not done — flagged for after the deadline): an LLM pass on
  filings where `wsc_c`/`wsc_d` disagree sharply with CL's signals could
  determine whether this is a genuine vocabulary coverage gap or confirms the
  redundancy.

## 12. Running it

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
  version so runs stay comparable row-for-row (this is how the four
  calibration_samples files stayed directly comparable to each other).

**Known environment issue:** the sync layer between some editor/tooling
sessions and disk can occasionally serve a stale view of `khashi_scorer.py`
mid-edit. Always run `python -m py_compile khashi_scorer.py` yourself before a
long run — don't trust a compile check from another tool without also
confirming locally.

## 13. Output schema

One row per filing: identifying fields (`ticker`, `cik`, `filingDate`,
`accessionNumber`, `source`), raw WSC signals (`wsc_a`–`wsc_f`), v2's four
scores (`qo_score`, `se_score`, `cl_score`, `sa_score`, `hype_score`) with a
human-readable reasoning string for each, v3's score and eight criteria
(`hype_v3`, `v3_c1_sa_attribution` … `v3_c8_cl_uncertainty`, `v3_reasoning`),
and ~20 `diag_*` diagnostic columns (raw SE/CL sub-signals) kept for any
future recalibration — not part of either score, safe to ignore if you only
need the final numbers.

## 14. Academic grounding

- Loughran, T., & McDonald, B. (2011). When is a liability not a liability?
  Textual analysis, dictionaries, and 10-Ks. *The Journal of Finance*, 66(1),
  35–65.
- Henry, E. (2008). Are investors influenced by how earnings press releases
  are written? *The Journal of Business Communication*, 45(4), 363–407.
- Baginski, S. P., Hassell, J. M., & Hutton, A. P. (2004). The causal
  attribution of management forecast disclosures — theoretical basis for the
  [E]/[F] internal/external attribution asymmetry underlying SA.
