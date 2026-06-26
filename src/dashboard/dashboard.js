const text = (id, value) => {
  document.getElementById(id).textContent = value ?? "-";
};

const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "\"": "&quot;",
  "'": "&#39;",
}[char]));

async function refresh() {
  const response = await fetch("/status", {cache: "no-store"});
  const s = await response.json();

  text("run_status", s.run_status);
  text("completed", s.completed);
  text("failed", s.failed);
  text("validated", s.validated);
  text("corrected", s.corrected);
  text("eta", s.eta);
  text("rate", `${Number(s.rows_per_minute || 0).toFixed(2)} rows/min`);
  text("elapsed", `elapsed ${s.elapsed}`);
  text(
    "progress_text",
    `${s.processed}/${s.target_rows} processed (${Number(s.percent || 0).toFixed(1)}%), ` +
      `submitted ${s.submitted}, skipped ${s.skipped}, pending ${s.pending}, ` +
      `validated ${s.validated}, corrected ${s.corrected}`,
  );
  text("model", `${s.model} via ${s.ollama_url}`);
  text("paths", `output: ${s.output}; validation_pct: ${s.validation_pct}`);

  document.getElementById("bar").style.width = `${Math.min(100, Math.max(0, s.percent || 0))}%`;

  const queued = Object.values(s.queued_rows || {})
    .sort((a, b) => Number(a.row_number) - Number(b.row_number))
    .slice(0, 20);
  document.getElementById("queued").innerHTML = queued.length
    ? queued.map((r) => `<tr><td>${esc(r.row_number)}</td><td>${esc(r.ticker)}</td><td>${esc(r.filingDate)}</td><td><code>${esc(r.accessionNumber)}</code></td><td>${esc(r.chars)}</td></tr>`).join("")
    : '<tr><td colspan="5" class="subtle">No queued rows</td></tr>';

  const current = Object.values(s.current_rows || {});
  document.getElementById("current").innerHTML = current.length
    ? current.map((r) => `<tr><td>${esc(r.row_number)}</td><td>${esc(r.stage)}</td><td>${esc(r.ticker)}</td><td>${esc(r.filingDate)}</td><td><code>${esc(r.accessionNumber)}</code></td><td>${esc(r.chars)}</td><td>${esc(r.attempt)}/${esc(r.max_attempts)}</td></tr>`).join("")
    : '<tr><td colspan="7" class="subtle">No active rows</td></tr>';

  const scores = s.recent_scores || [];
  document.getElementById("scores").innerHTML = scores.length
    ? scores.map((r) => `<tr><td>${esc(r.time)}</td><td>${esc(r.ticker)}</td><td>${esc(r.filingDate)}</td><td>${esc(r.score)}</td><td>${esc(r.validation_status)}</td><td class="wrap-cell">${esc(r.reasoning)}</td></tr>`).join("")
    : '<tr><td colspan="6" class="subtle">No scored rows yet</td></tr>';

  const validations = s.recent_validations || [];
  document.getElementById("validations").innerHTML = validations.length
    ? validations.map((r) => `<tr><td>${esc(r.time)}</td><td>${esc(r.ticker)}</td><td>${esc(r.filingDate)}</td><td>${esc(r.score)}</td><td>${esc(r.validation_status)}</td><td class="wrap-cell">${esc(r.validation_reasoning)}</td></tr>`).join("")
    : '<tr><td colspan="6" class="subtle">No validated rows yet</td></tr>';

  const errors = s.recent_errors || [];
  document.getElementById("errors").innerHTML = errors.length
    ? errors.map((e) => `<tr><td>${esc(e.time)}</td><td>${esc(e.ticker)}</td><td><code>${esc(e.accessionNumber)}</code></td><td class="bad">${esc(e.error)}</td></tr>`).join("")
    : '<tr><td colspan="4" class="subtle">No errors</td></tr>';
}

refresh().catch(console.error);
setInterval(() => refresh().catch(console.error), 1000);
