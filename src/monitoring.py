from __future__ import annotations

import csv
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:
    from . import config as cfg
except ImportError:  # Allows running via python src/main.py
    import config as cfg

LOGGER = logging.getLogger("hype_vagueness_scoring")
TERMINAL_LOCK = threading.Lock()
TERMINAL_STATUS_LENGTH = 0
MONITOR_LOCK = threading.Lock()
MONITOR_STATE: dict[str, Any] = {
    "run_status": "not started",
    "current_rows": {},
    "recent_errors": [],
}


def setup_logging() -> None:
    """Configure console and file logging for the scoring run."""
    cfg.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False

    for handler in LOGGER.handlers[:]:
        handler.close()
        LOGGER.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    LOGGER.addHandler(console_handler)

    file_handler = logging.FileHandler(cfg.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)


def write_terminal_status(message: str) -> None:
    """Write a high-frequency terminal-only status line that overwrites itself."""
    global TERMINAL_STATUS_LENGTH

    with TERMINAL_LOCK:
        padding = " " * max(0, TERMINAL_STATUS_LENGTH - len(message))
        sys.stdout.write(f"\r{message}{padding}")
        sys.stdout.flush()
        TERMINAL_STATUS_LENGTH = len(message)


def clear_terminal_status() -> None:
    """Clear the terminal-only status line before writing regular log messages."""
    global TERMINAL_STATUS_LENGTH

    with TERMINAL_LOCK:
        if TERMINAL_STATUS_LENGTH:
            sys.stdout.write("\r" + (" " * TERMINAL_STATUS_LENGTH) + "\r")
            sys.stdout.flush()
            TERMINAL_STATUS_LENGTH = 0


def format_duration(seconds: float) -> str:
    """Format elapsed or remaining seconds as a compact human-readable duration."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def progress_target_rows(target_rows: int, completed_before_run: int) -> int:
    """Return the progress denominator including rows completed before resume."""
    already_completed = max(completed_before_run, 0)
    return max(already_completed + max(target_rows, 0), already_completed)


def progress_metrics(
    progress: dict[str, int],
    target_rows: int,
    start_time: float,
    completed_before_run: int,
) -> dict[str, int | float]:
    """Calculate progress and ETA without letting resume rows inflate throughput."""
    run_done = progress["completed"] + progress["failed"]
    already_completed = max(completed_before_run, 0)
    processed_total = already_completed + run_done
    target_total = progress_target_rows(target_rows, already_completed)
    elapsed = time.monotonic() - start_time
    rows_per_minute = (run_done / elapsed) * 60 if elapsed > 0 else 0.0
    remaining = max(target_total - processed_total, 0)
    percent_done = min(processed_total, target_total) if target_total else processed_total
    percent = (percent_done / target_total) * 100 if target_total else 100.0
    eta_seconds = (remaining / rows_per_minute) * 60 if rows_per_minute > 0 else 0

    return {
        "already_completed": already_completed,
        "run_done": run_done,
        "processed_total": processed_total,
        "target_total": target_total,
        "elapsed": elapsed,
        "rows_per_minute": rows_per_minute,
        "remaining": remaining,
        "percent": percent,
        "eta_seconds": eta_seconds,
    }


def format_eta(remaining: int | float, rows_per_minute: float, eta_seconds: float) -> str:
    """Format ETA while avoiding fake estimates before new rows finish."""
    if remaining <= 0:
        return "0s"
    if rows_per_minute <= 0:
        return "-"
    return format_duration(eta_seconds)


def log_run_start(total_rows: int, target_rows: int, completed_before_run: int) -> None:
    """Log the run configuration and monitoring baseline."""
    LOGGER.info("starting hype/vagueness scoring")
    LOGGER.info(
        "model=%s ollama_url=%s concurrency=%s retries=%s terminal_status_every=%s",
        cfg.MODEL,
        cfg.OLLAMA_URL,
        cfg.CONCURRENCY,
        cfg.RETRIES,
        cfg.TERMINAL_STATUS_EVERY,
    )
    LOGGER.info("input=%s", cfg.INPUT_CSV)
    LOGGER.info("output=%s", cfg.OUTPUT_CSV)
    LOGGER.info("errors=%s", cfg.ERRORS_CSV)
    LOGGER.info("log=%s", cfg.LOG_FILE)
    LOGGER.info(
        "rows_total=%s rows_target_this_run=%s rows_target_with_resume=%s resume=%s already_completed=%s limit=%s",
        total_rows,
        target_rows,
        progress_target_rows(target_rows, completed_before_run),
        cfg.RESUME,
        completed_before_run,
        cfg.LIMIT,
    )


def log_progress(
    progress: dict[str, int],
    target_rows: int,
    start_time: float,
    pending_count: int,
    completed_before_run: int,
) -> None:
    """Log a result-write checkpoint with throughput, percent done, and ETA."""
    clear_terminal_status()

    metrics = progress_metrics(progress, target_rows, start_time, completed_before_run)

    LOGGER.info(
        "results written processed=%s/%s %.1f%% already_completed=%s completed=%s failed=%s skipped=%s submitted=%s pending=%s "
        "rate=%.2f rows/min elapsed=%s eta=%s",
        metrics["processed_total"],
        metrics["target_total"],
        metrics["percent"],
        metrics["already_completed"],
        progress["completed"],
        progress["failed"],
        progress["skipped"],
        progress["submitted"],
        pending_count,
        metrics["rows_per_minute"],
        format_duration(metrics["elapsed"]),
        format_eta(metrics["remaining"], metrics["rows_per_minute"], metrics["eta_seconds"]),
    )


def log_results_written_if_needed(
    progress: dict[str, int],
    target_rows: int,
    start_time: float,
    pending_count: int,
    completed_before_run: int,
) -> None:
    """Log a result-write checkpoint after enough newly written rows."""
    done = progress["completed"] + progress["failed"]
    if cfg.PROGRESS_EVERY > 0 and done - progress["last_logged"] >= cfg.PROGRESS_EVERY:
        progress["last_logged"] = done
        log_progress(progress, target_rows, start_time, pending_count, completed_before_run)


def dashboard_html() -> str:
    """Build the local monitoring dashboard HTML page."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>8-K Hype/Vagueness Scoring Monitor</title>
  <style>
    :root {
      --bg: #f4efe6;
      --ink: #18201b;
      --muted: #6c665b;
      --card: #fffaf0;
      --line: #d8ccb8;
      --accent: #0f6b5f;
      --bad: #9b2f25;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at top left, #fff7df, var(--bg) 42%, #e8dfd1);
      color: var(--ink);
      font-family: Georgia, "Times New Roman", serif;
    }
    main { max-width: 1180px; margin: 0 auto; padding: 28px; }
    header { display: flex; justify-content: space-between; gap: 18px; align-items: end; margin-bottom: 24px; }
    h1 { margin: 0; font-size: clamp(28px, 5vw, 54px); line-height: .95; letter-spacing: -1px; }
    .subtle { color: var(--muted); font-size: 14px; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }
    .card {
      background: color-mix(in srgb, var(--card) 92%, white);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
      box-shadow: 0 10px 28px rgba(48, 38, 22, .08);
    }
    .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
    .value { font-size: clamp(24px, 4vw, 38px); margin-top: 8px; font-variant-numeric: tabular-nums; }
    .bar { height: 18px; border-radius: 999px; background: #ded4c1; overflow: hidden; border: 1px solid var(--line); }
    .bar span { display: block; height: 100%; width: 0%; background: linear-gradient(90deg, var(--accent), #d18b2c); transition: width .4s ease; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; font-variant-numeric: tabular-nums; }
    th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 10px 8px; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .07em; }
    .wide { grid-column: span 2; }
    .full { grid-column: 1 / -1; }
    .bad { color: var(--bad); }
    code { font-family: Consolas, "Courier New", monospace; font-size: 12px; }
    @media (max-width: 850px) { .grid { grid-template-columns: 1fr 1fr; } .wide { grid-column: 1 / -1; } header { display: block; } }
    @media (max-width: 560px) { main { padding: 16px; } .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="subtle">Local Ollama pipeline</div>
        <h1>8-K scoring monitor</h1>
      </div>
      <div class="subtle">Auto-refreshes every second<br><code>/status</code> returns JSON</div>
    </header>
    <section class="grid">
      <div class="card"><div class="label">Status</div><div class="value" id="run_status">-</div></div>
      <div class="card"><div class="label">Completed</div><div class="value" id="completed">0</div></div>
      <div class="card"><div class="label">Failed</div><div class="value bad" id="failed">0</div></div>
      <div class="card"><div class="label">ETA</div><div class="value" id="eta">-</div></div>
      <div class="card full">
        <div class="label">Progress</div>
        <div class="bar" aria-label="progress"><span id="bar"></span></div>
        <p class="subtle" id="progress_text">-</p>
      </div>
      <div class="card wide">
        <div class="label">Throughput</div>
        <div class="value" id="rate">0 rows/min</div>
        <p class="subtle" id="elapsed">elapsed -</p>
      </div>
      <div class="card wide">
        <div class="label">Run</div>
        <p><code id="model">-</code></p>
        <p class="subtle" id="paths">-</p>
      </div>
      <div class="card full">
        <div class="label">Currently scoring</div>
        <table>
          <thead><tr><th>Row</th><th>Ticker</th><th>Date</th><th>Accession</th><th>Chars</th><th>Attempt</th></tr></thead>
          <tbody id="current"><tr><td colspan="6" class="subtle">No active rows</td></tr></tbody>
        </table>
      </div>
      <div class="card full">
        <div class="label">Recent errors</div>
        <table>
          <thead><tr><th>Time</th><th>Ticker</th><th>Accession</th><th>Error</th></tr></thead>
          <tbody id="errors"><tr><td colspan="4" class="subtle">No errors</td></tr></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const text = (id, value) => document.getElementById(id).textContent = value ?? "-";
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[c]));
    async function refresh() {
      const response = await fetch('/status', {cache: 'no-store'});
      const s = await response.json();
      text('run_status', s.run_status);
      text('completed', s.completed);
      text('failed', s.failed);
      text('eta', s.eta);
      text('rate', `${Number(s.rows_per_minute || 0).toFixed(2)} rows/min`);
      text('elapsed', `elapsed ${s.elapsed}`);
      text('progress_text', `${s.processed}/${s.target_rows} processed (${Number(s.percent || 0).toFixed(1)}%), submitted ${s.submitted}, skipped ${s.skipped}, pending ${s.pending}`);
      text('model', `${s.model} via ${s.ollama_url}`);
      text('paths', `output: ${s.output}`);
      document.getElementById('bar').style.width = `${Math.min(100, Math.max(0, s.percent || 0))}%`;
      const current = Object.values(s.current_rows || {});
      document.getElementById('current').innerHTML = current.length ? current.map(r => `<tr><td>${esc(r.row_number)}</td><td>${esc(r.ticker)}</td><td>${esc(r.filingDate)}</td><td><code>${esc(r.accessionNumber)}</code></td><td>${esc(r.chars)}</td><td>${esc(r.attempt)}/${esc(r.max_attempts)}</td></tr>`).join('') : '<tr><td colspan="6" class="subtle">No active rows</td></tr>';
      const errors = s.recent_errors || [];
      document.getElementById('errors').innerHTML = errors.length ? errors.map(e => `<tr><td>${esc(e.time)}</td><td>${esc(e.ticker)}</td><td><code>${esc(e.accessionNumber)}</code></td><td class="bad">${esc(e.error)}</td></tr>`).join('') : '<tr><td colspan="4" class="subtle">No errors</td></tr>';
    }
    refresh().catch(console.error);
    setInterval(() => refresh().catch(console.error), 1000);
  </script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve the local HTML dashboard and JSON status endpoint."""

    def do_GET(self) -> None:
        """Handle dashboard and status requests."""
        if self.path == "/":
            body = dashboard_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/status":
            body = json.dumps(monitor_snapshot()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress per-request HTTP logging."""
        return


def monitor_snapshot() -> dict[str, Any]:
    """Return a thread-safe copy of the current dashboard state."""
    with MONITOR_LOCK:
        return {
            **MONITOR_STATE,
            "current_rows": dict(MONITOR_STATE.get("current_rows", {})),
            "recent_errors": list(MONITOR_STATE.get("recent_errors", [])),
        }


def start_dashboard() -> ThreadingHTTPServer | None:
    """Start the optional local monitoring dashboard in a daemon thread."""
    if not cfg.DASHBOARD_ENABLED:
        return None

    try:
        server = ThreadingHTTPServer((cfg.DASHBOARD_HOST, cfg.DASHBOARD_PORT), DashboardHandler)
    except OSError as exc:
        LOGGER.warning("dashboard disabled: could not bind http://%s:%s error=%s", cfg.DASHBOARD_HOST, cfg.DASHBOARD_PORT, exc)
        return None

    thread = threading.Thread(target=server.serve_forever, name="monitor-dashboard", daemon=True)
    thread.start()
    LOGGER.info("dashboard=http://%s:%s", cfg.DASHBOARD_HOST, cfg.DASHBOARD_PORT)
    return server


def init_monitor_state(total_rows: int, target_rows: int, completed_before_run: int, start_time: float) -> None:
    """Initialize dashboard state at the start of a scoring run."""
    already_completed = max(completed_before_run, 0)
    monitored_target_rows = progress_target_rows(target_rows, already_completed)
    percent = (already_completed / monitored_target_rows) * 100 if monitored_target_rows else 100.0

    with MONITOR_LOCK:
        MONITOR_STATE.clear()
        MONITOR_STATE.update(
            {
                "run_status": "running",
                "model": cfg.MODEL,
                "ollama_url": cfg.OLLAMA_URL,
                "input": str(cfg.INPUT_CSV),
                "output": str(cfg.OUTPUT_CSV),
                "errors": str(cfg.ERRORS_CSV),
                "log": str(cfg.LOG_FILE),
                "total_rows": total_rows,
                "target_rows": monitored_target_rows,
                "target_rows_this_run": target_rows,
                "already_completed": already_completed,
                "submitted": 0,
                "completed": already_completed,
                "completed_this_run": 0,
                "failed": 0,
                "skipped": 0,
                "pending": 0,
                "processed": already_completed,
                "percent": percent,
                "rows_per_minute": 0.0,
                "elapsed": "0s",
                "eta": "0s" if already_completed >= monitored_target_rows else "-",
                "start_time": start_time,
                "current_rows": {},
                "recent_errors": [],
            }
        )


def update_monitor_progress(
    progress: dict[str, int],
    target_rows: int,
    start_time: float,
    pending_count: int,
    completed_before_run: int,
) -> None:
    """Update dashboard counters, throughput, and ETA."""
    metrics = progress_metrics(progress, target_rows, start_time, completed_before_run)

    with MONITOR_LOCK:
        MONITOR_STATE.update(
            {
                "target_rows": metrics["target_total"],
                "target_rows_this_run": target_rows,
                "already_completed": metrics["already_completed"],
                "submitted": progress["submitted"],
                "completed": metrics["already_completed"] + progress["completed"],
                "completed_this_run": progress["completed"],
                "failed": progress["failed"],
                "skipped": progress["skipped"],
                "pending": pending_count,
                "processed": metrics["processed_total"],
                "percent": metrics["percent"],
                "rows_per_minute": metrics["rows_per_minute"],
                "elapsed": format_duration(metrics["elapsed"]),
                "eta": format_eta(metrics["remaining"], metrics["rows_per_minute"], metrics["eta_seconds"]),
            }
        )


def update_monitor_current_row(
    active_id: str,
    row: dict[str, str],
    row_number: int,
    text_chars: int,
    attempt: int,
) -> None:
    """Update the dashboard entry for a currently scoring worker row."""
    with MONITOR_LOCK:
        MONITOR_STATE.setdefault("current_rows", {})[active_id] = {
            "row_number": row_number,
            "ticker": row["ticker"],
            "filingDate": row["filingDate"],
            "accessionNumber": row["accessionNumber"],
            "chars": text_chars,
            "attempt": attempt,
            "max_attempts": cfg.RETRIES + 1,
        }


def clear_monitor_current_row(active_id: str) -> None:
    """Remove a worker row from the dashboard active-row list."""
    with MONITOR_LOCK:
        MONITOR_STATE.setdefault("current_rows", {}).pop(active_id, None)


def add_monitor_error(row: dict[str, str], error: Exception) -> None:
    """Add a recent error entry to the dashboard state."""
    with MONITOR_LOCK:
        recent_errors = MONITOR_STATE.setdefault("recent_errors", [])
        recent_errors.insert(
            0,
            {
                "time": time.strftime("%H:%M:%S"),
                "ticker": row["ticker"],
                "accessionNumber": row["accessionNumber"],
                "error": str(error),
            },
        )
        del recent_errors[20:]


def mark_finished() -> None:
    """Mark the dashboard run as finished."""
    with MONITOR_LOCK:
        MONITOR_STATE["run_status"] = "finished"
        MONITOR_STATE["pending"] = 0
