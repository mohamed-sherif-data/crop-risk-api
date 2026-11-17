/* ============================================================================
   Crop Risk — client
   Four inputs, one POST, one report. Talks only to the API on the same origin.
   ========================================================================== */

const $ = (id) => document.getElementById(id);
const num = new Intl.NumberFormat("en-US");
const one = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

const COLORS = {
  gold: "#d4af37",
  green: "#2fa37a",
  blue: "#5b8fd6",
  purple: "#8b6be0",
  red: "#d9614c",
  text: "#eceef1",
  grid: "#1b222a",
  tick: "#8b98a5",
};

const BAND_TEXT = {
  low: {
    label: "Low risk",
    sentence: "Conditions are close to the crop's normal range: little beyond the usual season-to-season wobble.",
    arabic: "الخطر منخفض — الظروف قريبة من المعتاد، ومفيش ضغط كبير على المحصول.",
  },
  medium: {
    label: "Medium risk",
    sentence: "Something is pushing against this crop — usually water at a sensitive stage. Worth watching.",
    arabic: "خطر متوسط — فيه عامل بيضغط على المحصول، وغالبًا المياه في مرحلة حسّاسة. يستحق المتابعة.",
  },
  high: {
    label: "High risk",
    sentence: "The water shortfall and the crop's own history point the same way. Review irrigation before the season.",
    arabic: "خطر مرتفع — نقص المياه وتاريخ المحصول بيشاوروا في نفس الاتجاه. راجع الري قبل الموسم.",
  },
};

const CROP_COLORS = { model: COLORS.gold, water: COLORS.blue, soil: COLORS.purple };

let chart = null;
let crops = [];
let selected = { soil: "Loam", stage: "flowering" };

/* ------------------------------------------------------------------ helpers */
async function get(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function post(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch { /* keep the status text */ }
    throw new Error(detail);
  }
  return response.json();
}

let statusTimer = null;

function status(state, text) {
  const bar = $("status");
  bar.dataset.state = state;
  $("status-text").textContent = text;
  bar.classList.remove("is-hidden");
  clearTimeout(statusTimer);
  if (state === "ready") statusTimer = setTimeout(() => bar.classList.add("is-hidden"), 2400);
}

function chartDefaults() {
  if (!window.Chart) return;
  Chart.defaults.color = COLORS.tick;
  Chart.defaults.font.family = 'Inter, "Segoe UI", system-ui, sans-serif';
  Chart.defaults.font.size = 11;
  Chart.defaults.borderColor = COLORS.grid;
}

/* ---------------------------------------------------------------- bootstrap */
async function boot() {
  chartDefaults();
  wireSegments();
  wireWater();
  wireForm();

  try {
    const [meta, catalogue] = await Promise.all([get("/v1/meta"), get("/v1/crops")]);

    crops = catalogue.items.map((row) => row.item);
    $("crop-list").innerHTML = crops.map((item) => `<option value="${item}"></option>`).join("");
    $("crop-hint").textContent = `${catalogue.count} crops with at least four years of data.`;
    $("forecast-year").textContent = meta.target_year;
    $("stage-hint").textContent =
      "Stage-specific sensitivity is sourced for wheat only; other crops use a flat value.";

    const wheat = crops.find((item) => item === "Wheat");
    $("crop-input").value = wheat || crops[0];
    status("ready", "Ready");
  } catch (error) {
    status("error", "Offline");
    $("crop-hint").textContent = `Could not reach the API — ${error.message}`;
  }
}

function wireSegments() {
  for (const [groupId, key] of [["soil-group", "soil"], ["stage-group", "stage"]]) {
    const group = $(groupId);
    group.addEventListener("click", (event) => {
      const button = event.target.closest(".segmented__item");
      if (!button) return;
      selected[key] = button.dataset.value;
      group.querySelectorAll(".segmented__item").forEach((item) => {
        item.setAttribute("aria-pressed", String(item === button));
      });
    });
  }
}

function wireWater() {
  const slider = $("water-input");
  const paint = () => {
    const percent = ((slider.value - slider.min) / (slider.max - slider.min)) * 100;
    slider.style.setProperty("--fill", `${percent}%`);
    $("water-value").textContent = `${slider.value} %`;
  };
  slider.addEventListener("input", paint);
  paint();
}

function wireForm() {
  $("risk-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    await calculate();
  });
}

/* ---------------------------------------------------------------- calculate */
async function calculate() {
  const typed = $("crop-input").value.trim();
  const crop = crops.find((item) => item.toLowerCase() === typed.toLowerCase());
  if (!crop) {
    $("crop-hint").textContent = `“${typed}” is not in the list — pick one of the ${crops.length} crops.`;
    $("crop-input").focus();
    return;
  }

  const button = $("calculate");
  button.disabled = true;
  button.textContent = "Calculating…";
  status("loading", "Calculating…");

  try {
    const payload = { crop, ...selected, water_percent: Number($("water-input").value) };
    const [report, history] = await Promise.all([
      post("/v1/risk/report", payload),
      get(`/v1/crops/${encodeURIComponent(crop)}/history`),
    ]);

    render(report, history);
    $("result").hidden = false;
    status("ready", "Ready");
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    status("error", "Failed");
    $("crop-hint").textContent = `Could not calculate — ${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = "Calculate risk";
  }
}

/* ------------------------------------------------------------------- render */
function render(report, history) {
  const band = BAND_TEXT[report.risk_level];

  $("score-value").textContent = one.format(report.total_score);
  $("score-value").style.color = `var(--${bandColor(report.risk_level)})`;

  const chip = $("score-chip");
  chip.textContent = band.label;
  chip.className = `chip chip--${report.risk_level}`;

  $("score-sentence").textContent = band.sentence;
  $("score-ar").textContent = band.arabic;

  $("forecast-year").textContent = report.target_year;
  $("forecast-value").textContent = num.format(Math.round(report.forecast_kg_ha));
  $("expected-value").textContent = num.format(Math.round(report.expected_yield_kg_ha));
  $("drop-value").textContent = `${one.format(report.model_risk_pct)} %`;

  renderBars(report);
  renderDrivers(report);
  renderAssumptions(report.assumptions);
  drawChart(report, history);

  $("chart-caption").textContent =
    `${report.crop}: ${history.points.length} annual observations, `
    + `${history.points[0].year}–${report.last_observed_year}. `
    + `The two markers are ${report.target_year}: the naive forecast and what your water level leaves of it.`;
}

function bandColor(level) {
  return level === "low" ? "green" : level === "high" ? "red" : "gold";
}

function renderBars(report) {
  const parts = [
    { key: "model", label: "Model drop chance", value: report.model_risk_pct, unit: "pts" },
    { key: "water", label: `Water loss · Ky ${report.ky.toFixed(2)}`, value: report.water_loss_pct, unit: "pts" },
    { key: "soil", label: `Soil · ${report.inputs.soil}`, value: report.soil_points, unit: "pts" },
  ];
  const total = parts.reduce((sum, part) => sum + part.value, 0) || 1;

  $("bars").innerHTML = `
    <li class="bars__stack" aria-hidden="true">
      ${parts
        .map(
          (part) =>
            `<span style="width:${((part.value / total) * 100).toFixed(1)}%;background:${CROP_COLORS[part.key]}"></span>`
        )
        .join("")}
    </li>
    <li class="bars__legend">
      ${parts
        .map(
          (part) => `<span class="bars__row">
            <span class="bars__dot" style="background:${CROP_COLORS[part.key]}"></span>
            <span>${part.label}</span>
            <span class="bars__value">+${one.format(part.value)}</span>
          </span>`
        )
        .join("")}
    </li>`;

  const capped = report.total_score >= 100 ? " Capped at 100." : "";
  $("bars-caption").textContent =
    `Total ${one.format(report.total_score)} / 100 — model risk plus the water and soil penalties.${capped}`;
}

function renderDrivers(report) {
  $("drivers").innerHTML = report.drivers
    .map(
      (driver) => `<li class="drivers__row">
        <span class="drivers__head">
          <span>${driver.label}</span>
          <b>${one.format(driver.share_pct)}% · ${driver.direction}</b>
        </span>
        <span class="drivers__track">
          <span class="drivers__fill drivers__fill--${driver.direction}"
                style="width:${Math.max(2, driver.share_pct).toFixed(1)}%"></span>
        </span>
      </li>`
    )
    .join("");
}

function renderAssumptions(assumptions) {
  $("assumption-list").innerHTML = assumptions.map((line) => `<li>${line}</li>`).join("");
}

/* -------------------------------------------------------------------- chart */
function drawChart(report, history) {
  const years = history.points.map((point) => point.year);
  const values = history.points.map((point) => point.value);
  const labels = [...years, report.target_year];

  const marker = (value, color) => {
    const series = new Array(labels.length).fill(null);
    series[series.length - 1] = value;
    return {
      data: series,
      borderColor: color,
      backgroundColor: color,
      pointRadius: 6,
      pointHoverRadius: 8,
      showLine: false,
    };
  };

  const datasets = [
    {
      label: "Observed yield",
      data: [...values, null],
      borderColor: COLORS.text,
      backgroundColor: COLORS.text,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.25,
    },
    { label: `Naive ${report.target_year}`, ...marker(report.forecast_kg_ha, COLORS.gold) },
    { label: "With your water", ...marker(report.expected_yield_kg_ha, COLORS.red) },
  ];

  if (chart) {
    chart.data.labels = labels;
    chart.data.datasets = datasets;
    chart.update();
    return;
  }

  chart = new Chart($("chart"), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "top",
          align: "end",
          labels: { boxWidth: 8, boxHeight: 8, usePointStyle: true, pointStyle: "circle", padding: 14 },
        },
        tooltip: {
          backgroundColor: "#12171d",
          borderColor: "#2a323c",
          borderWidth: 1,
          titleColor: "#fff",
          bodyColor: "#c8d1da",
          padding: 10,
          callbacks: {
            label: (item) =>
              item.parsed.y == null ? null : `${item.dataset.label}: ${num.format(Math.round(item.parsed.y))} kg/ha`,
          },
        },
      },
      scales: {
        x: { grid: { display: false }, border: { color: COLORS.grid }, ticks: { maxRotation: 0, autoSkipPadding: 14 } },
        y: { grid: { color: COLORS.grid }, border: { display: false }, ticks: { callback: (value) => num.format(value) } },
      },
    },
  });
}

document.addEventListener("DOMContentLoaded", boot);
