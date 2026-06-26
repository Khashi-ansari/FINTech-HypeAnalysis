from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_CSV = PROJECT_ROOT / "resources/item202_clean.csv"
OUTPUT_CSV = PROJECT_ROOT / "outputs/item202_hype_vagueness_scores.csv"
ERRORS_CSV = PROJECT_ROOT / "outputs/item202_hype_vagueness_errors.csv"
PROMPT_FILE = PROJECT_ROOT / "prompts/hype_vagueness_score.md"
VALIDATION_PROMPT_FILE = PROJECT_ROOT / "prompts/hype_vagueness_validation.md"
LOG_FILE = PROJECT_ROOT / "outputs/scoring.log"

MODEL = "qwen3:8b"
OLLAMA_URL = "http://localhost:11434"

# Recommended local Ollama server settings for long-context single-worker runs:
# OLLAMA_CONTEXT_LENGTH=16384
# OLLAMA_NUM_PARALLEL=1
# OLLAMA_FLASH_ATTENTION=1
# OLLAMA_KV_CACHE_TYPE=q8_0
CONCURRENCY = 1
QUEUE_MULTIPLIER = 4
REQUEST_TIMEOUT_SECONDS = 300
RETRIES = 4

# Deterministic count-to-score weights. The model emits only counts; Python
# computes the final continuous score from these weights.
WEIGHTS = {"promo": 0.30, "figure": 0.30}

# Probability from 0.0 to 1.0 that a scored row is validated by a second model call.
# 0.0 disables validation; 1.0 validates every scored row.
VALIDATION_PCT = 0.1

# 0 means no row cap.
LIMIT = 0

RESUME = True
PROGRESS_EVERY = 25
TERMINAL_STATUS_EVERY = 1
DASHBOARD_ENABLED = True
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8765
