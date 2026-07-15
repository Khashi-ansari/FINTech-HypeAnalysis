# FinTech Hype Analysis

TUM School of Management seminar project — *"Advanced Topics in FinTech: AI
Agents and Blockchain"* (SS26). Workstream: **Corporate Hype Narratives &
Stock Price Reversals**.

**Hypothesis:** unusually high hype language in SEC Form 8-K earnings press
releases predicts a stock price reversal — management overclaims, the market
initially rewards it, then corrects.

## What's in this repo

```
.
├── khashi/
│   ├── khashi_scorer.py        ← the HypeScore scorer (see khashi/README.md)
│   ├── README.md                ← full methodology, calibration history, how to run
│   ├── outputs/                 ← generated run outputs (gitignored — see below)
│   └── calibration_samples/     ← versioned 1000-row calibration runs (v2 → v3 rev1/2/3)
├── HANDOFF_CONTEXT.md           ← working session notes
├── Hype_Definition_Document_v1.docx
├── Hype_Scoring_Rubric_v2.xlsx
└── Literature_Reference_Tracker_v1.xlsx
```

Start with [`khashi/README.md`](khashi/README.md) for the actual scoring
methodology (Weighted Sentence Coverage, the four original components, the
v3 8-criterion restructuring, and the full calibration/stress-test history),
and [`khashi/calibration_samples/README.md`](khashi/calibration_samples/README.md)
for how the formula evolved across three validation rounds.

## Data

Full input dataset (`item202_clean.csv`, ~16k S&P 500 8-K Item 2.02 filings,
2018–2025) and other large research assets live in the team's shared Google
Drive folder, not in this repo. `khashi/outputs/` is gitignored — regenerate
it by running the scorer (see `khashi/README.md`).

