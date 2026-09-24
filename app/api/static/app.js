/**
 * Frontend for the 10-Q retrieval system.
 *
 * The interface exists to make the pipeline legible, not just to look tidy:
 * table evidence is rendered from the normalised rows rather than as raw
 * Markdown, figure evidence shows the extracted image beside its page render so
 * a reader can check the model against the source, and refusals are a distinct
 * visual state rather than an answer-shaped paragraph.
 */

const $ = (selector) => document.querySelector(selector);

const form = $("#ask-form");
const input = $("#question");
const submit = $("#submit");
const results = $("#results");
const empty = $("#empty");
const examplesList = $("#examples");
const healthButton = $("#health");
const healthText = $("#health-text");

let contentType = "";

/* ───────────────────────────── utilities ───────────────────────────── */

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (character) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character],
  );

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

/* ─────────────────────────────── theme ─────────────────────────────── */

const storedTheme = (() => {
  try {
    return localStorage.getItem("theme");
  } catch {
    return null; // Private mode or blocked storage: fall back to system preference.
  }
})();

if (storedTheme === "light" || storedTheme === "dark") {
  document.documentElement.dataset.theme = storedTheme;
}

$("#theme").addEventListener("click", () => {
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const current = document.documentElement.dataset.theme;
  const resolved = current === "light" || current === "dark" ? current : prefersDark ? "dark" : "light";
  const next = resolved === "dark" ? "light" : "dark";

  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("theme", next);
  } catch {
    /* Theme still applies for this visit. */
  }
});

/* ─────────────────────────────── health ────────────────────────────── */

async function refreshHealth() {
  try {
    const response = await fetch("/api/health");
    const data = await response.json();

    let state = "pill--ok";
    let label = "ready";

    if (!data.api_key_configured) {
      state = "pill--error";
      label = "no API key";
    } else if (!data.qdrant?.reachable) {
      state = "pill--error";
      label = "Qdrant unreachable";
    } else if (!data.qdrant?.indexed) {
      state = "pill--warn";
      label = "not indexed";
    } else {
      label = `${data.qdrant.chunks} chunks indexed`;
    }

    healthButton.className = `pill ${state}`;
    healthText.textContent = label;
    healthButton.title = `${data.llm_model} · vision ${data.vision_model} · ${data.embedding_model}`;
  } catch {
    healthButton.className = "pill pill--error";
    healthText.textContent = "API unreachable";
  }
}

healthButton.addEventListener("click", refreshHealth);

/* ────────────────────────────── examples ───────────────────────────── */

async function loadExamples() {
  try {
    const response = await fetch("/api/examples");
    const examples = await response.json();

    examplesList.innerHTML = examples
      .map(
        (example) => `
        <button type="button" class="example" data-question="${escapeHtml(example.question)}"
                title="${escapeHtml(example.note)}">
          <span class="chip chip--${escapeHtml(example.category)}">${escapeHtml(example.category)}</span>
          <span class="example__text">${escapeHtml(example.question)}</span>
        </button>`,
      )
      .join("");

    examplesList.querySelectorAll(".example").forEach((button) => {
      button.addEventListener("click", () => {
        input.value = button.dataset.question;
        autosize();
        form.requestSubmit();
      });
    });
  } catch {
    examplesList.innerHTML = '<p class="ask__hint">Examples unavailable.</p>';
  }
}

/* ───────────────────────────── input sizing ────────────────────────── */

function autosize() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

input.addEventListener("input", autosize);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

/* ──────────────────────────── type filter ──────────────────────────── */

document.querySelectorAll(".segmented__option").forEach((option) => {
  option.addEventListener("click", () => {
    document.querySelectorAll(".segmented__option").forEach((other) => other.classList.remove("is-active"));
    option.classList.add("is-active");
    contentType = option.dataset.type;
  });
});

/* ──────────────────────────── rendering ────────────────────────────── */

/** Render a table from its normalised rows, not from Markdown. */
function renderTable(evidence) {
  if (!evidence.table_headers?.length || !evidence.table_rows?.length) return "";

  const headers = evidence.table_headers;
  const head = headers.map((header) => `<th>${escapeHtml(header || "")}</th>`).join("");

  const body = evidence.table_rows
    .map((row) => {
      const cells = headers.map((header, index) => {
        const key = index === 0 ? "label" : header || `column_${index}`;
        return `<td>${escapeHtml(row[key] ?? "")}</td>`;
      });
      return `<tr>${cells.join("")}</tr>`;
    })
    .join("");

  return `<div class="table-scroll"><table class="grid"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderFigure(evidence) {
  const extracted = evidence.figure_url
    ? `<figure class="figure-extracted">
         <img src="${escapeHtml(evidence.figure_url)}" alt="Image extracted from the filing" loading="lazy">
         <figcaption>Extracted image</figcaption>
       </figure>`
    : "";

  return `
    <div class="figure-pair">
      ${extracted}
      <figure class="figure-page">
        <img src="${escapeHtml(evidence.page_render_url)}" alt="Rendered page ${evidence.pdf_page}" loading="lazy">
        <figcaption>Source page, as published</figcaption>
      </figure>
    </div>`;
}

function renderEvidence(evidence) {
  const tags = [];
  if (evidence.cited_by_model) tags.push('<span class="tag tag--cited">cited</span>');
  if (evidence.content_type === "table" && !evidence.table_trustworthy) {
    tags.push('<span class="tag tag--warn">structure unverified</span>');
  }
  if (evidence.content_type === "figure" && evidence.figure_decorative) {
    tags.push('<span class="tag tag--warn">decorative</span>');
  }
  tags.push(`<span class="tag">${escapeHtml(evidence.found_by)}</span>`);

  let body = "";
  if (evidence.content_type === "table") {
    body = renderTable(evidence) || `<div class="source__text">${escapeHtml(evidence.display_text)}</div>`;
    if (!evidence.table_trustworthy) {
      body += `<p class="source__meta">This table's structure could not be verified, so its cells are not quoted as exact. The page image below shows the filing as published.</p>${renderFigure(evidence)}`;
    }
  } else if (evidence.content_type === "figure") {
    body = `${renderFigure(evidence)}<div class="source__text">${escapeHtml(evidence.display_text)}</div>`;
  } else {
    body = `<div class="source__text">${escapeHtml(evidence.display_text)}</div>`;
  }

  return `
    <details class="source ${evidence.cited_by_model ? "source--cited" : ""}">
      <summary class="source__summary">
        <svg class="source__chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="m9 6 6 6-6 6"/></svg>
        <span class="chip chip--${escapeHtml(evidence.content_type)}">${escapeHtml(evidence.content_type)}</span>
        <span class="source__page">${escapeHtml(evidence.citation)}</span>
        <span class="source__section">${escapeHtml(evidence.section)}</span>
        <span class="source__tags">${tags.join("")}</span>
      </summary>
      <div class="source__body">${body}</div>
    </details>`;
}

function renderTelemetry(telemetry) {
  const mix =
    Object.entries(telemetry.content_mix || {})
      .map(([key, value]) => `${key} ${value}`)
      .join(" · ") || "none";

  const metrics = [
    ["model", telemetry.model],
    ["routed", telemetry.routed_to],
    ["vision", telemetry.used_vision ? "yes" : "no"],
    ["chunks", `${telemetry.chunks_retrieved} (${mix})`],
    ["context", `${telemetry.context_characters.toLocaleString()} chars`],
    ["retrieve", `${telemetry.seconds_retrieve.toFixed(2)}s`],
    ["generate", `${telemetry.seconds_generate.toFixed(2)}s`],
  ];

  return `<div class="telemetry">${metrics
    .map(
      ([key, value]) =>
        `<span class="metric"><span class="metric__key">${escapeHtml(key)}</span><span class="metric__value">${escapeHtml(value)}</span></span>`,
    )
    .join("")}</div>`;
}

function renderAnswer(data) {
  const cited = data.evidence.filter((item) => item.cited_by_model).length;

  const note = data.refused
    ? `<p class="answer__note">The filing does not support an answer to this question. The system declines rather than inferring one — this is the intended behaviour for questions outside the document.</p>`
    : "";

  return `
    <article class="answer ${data.refused ? "answer--refused" : ""}">
      <div class="answer__head">
        <span class="answer__status">${data.refused ? "No supported answer" : "Answer"}</span>
        <span class="answer__question">${escapeHtml(data.question)}</span>
      </div>
      <div class="answer__body">${escapeHtml(data.answer)}</div>
      ${note}
      ${renderTelemetry(data.telemetry)}
    </article>

    <section class="evidence">
      <div class="evidence__header">
        <h2 class="evidence__title">Evidence</h2>
        <span class="evidence__count">${plural(data.evidence.length, "block")} retrieved${cited ? `, ${cited} cited` : ""}</span>
      </div>
      <div class="evidence__list">${data.evidence.map(renderEvidence).join("")}</div>
    </section>`;
}

function renderError(message) {
  return `
    <article class="answer answer--error">
      <div class="answer__head"><span class="answer__status">Error</span></div>
      <div class="answer__body">${escapeHtml(message)}</div>
    </article>`;
}

/* ─────────────────────────────── submit ────────────────────────────── */

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const question = input.value.trim();
  if (!question) return;

  submit.disabled = true;
  submit.classList.add("is-busy");
  empty.classList.add("is-hidden");
  results.innerHTML = `<article class="answer"><div class="answer__body">Searching the filing…</div></article>`;

  try {
    const response = await fetch("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        content_types: contentType ? [contentType] : null,
      }),
    });

    const data = await response.json();
    results.innerHTML = response.ok ? renderAnswer(data) : renderError(data.error || "The request failed.");
  } catch {
    results.innerHTML = renderError("Could not reach the server. Is it still running?");
  } finally {
    submit.disabled = false;
    submit.classList.remove("is-busy");
    refreshHealth();
  }
});

/* ──────────────────────────────── init ─────────────────────────────── */

refreshHealth();
loadExamples();
autosize();
