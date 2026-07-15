# Calibration Samples — 1000-Row Validation History

These are the actual outputs from the four 1000-row runs used to design and
stress-test the `hype_v3` scoring formula in `khashi_scorer.py`. Same 1000
filings and same random seed (42) in every file, so they're directly
comparable row-for-row. Kept in version control (unlike the rest of
`outputs/`, which is gitignored) so the calibration decisions are auditable
without re-running the pipeline.

| File | What it is |
|---|---|
| `khashi_hype_v2_1000.csv` | Baseline run. `hype_score` (v2, 4 equal-weighted components) plus the first diagnostic instrumentation (`diag_*` columns) that fed the correlation/OLS analysis. |
| `khashi_hype_v3_1000_rev1.csv` | First `hype_v3` attempt: 9 criteria, weighted by standardized OLS coefficients from the v2 run. Had two real problems (see rev2). |
| `khashi_hype_v3_1000_rev2.csv` | Fix: removed the SA-ExternalBlame criterion (r=-0.059 with the total — no discriminating power) and reinstated SE's D4 "burial" signal, merged into a wider SE-Concealment criterion (D4+D5), after rev1 dropped it entirely and caused large, spurious-looking swings (e.g. EQT 2020-01-13: 51→30). |
| `khashi_hype_v3_1000_rev3.csv` | Fix: found and corrected a sign-inverted `modal_ratio` penalty in rev2's hedging modifier (was penalizing the *most* assertive, least-hedged guidance language — backwards). Added a correctly-signed modal-strength bonus to CL-GuidanceStrength instead. This is the version used for the full 14,351-row run. |

**Net result across the three fixes:** correlation with v2 rose 0.932 → 0.940 →
0.949, and mean absolute rank shift vs. v2 fell 85 → 80 → 75 (out of 1000) —
each correction converged rather than introducing new problems, which is why
rev3 was judged ready for the full run.

`*_errors.csv` files are the corresponding per-run error logs — all effectively
empty (header only, 0 scoring failures) across all four runs.
