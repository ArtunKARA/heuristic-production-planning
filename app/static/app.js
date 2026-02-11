const el = (id) => document.getElementById(id);

const state = {
  problemData: null,
  scenarioConfig: null,
  frameId: null,
  algorithms: [],
  algorithmsLoaded: false,
  algorithmsLoading: false,
  algoParams: {},
  result: null,
};

const HARD_TOGGLES = [
  "HARD_DUE_DATE_FULFILLMENT",
  "HARD_RESOURCE_ROLE_ASSIGNED",
  "HARD_TIME_BUCKET_VALID",
  "HARD_NO_HOLIDAY_WORK",
  "HARD_COMPAT_MACHINE_MOLD_PROCESS",
  "HARD_COMPAT_PRODUCT_MOLD",
  "HARD_CAPACITY_BUCKET",
  "HARD_CAPACITY_SEGMENT",
];

const SOFT_TOGGLES = [
  { code: "SOFT_MOLD_CHANGE_MINIMIZE", weight: "w_mold_change" },
  { code: "SOFT_NIGHT_MOLD_CHANGE", weight: "w_night_mold_change" },
  { code: "SOFT_INVENTORY_LOW", weight: "w_inventory" },
  { code: "SOFT_MACHINE_COUNT_LOW", weight: "w_machine_count" },
];

const SCORE_WEIGHTS = [
  { key: "w_hard_penalty", label: "HARD_PENALTY_WEIGHT" },
];

const DEFAULT_SCENARIO_WEIGHTS = {
  w_mold_change: 10.0,
  w_night_mold_change: 100.0,
  w_inventory: 1.0,
  w_machine_count: 5.0,
  w_hard_penalty: 1.0,
};

const HARD_DESC = {
  HARD_DUE_DATE_FULFILLMENT: "Siparişler son tarihe kadar tamamlanmalı.",
  HARD_RESOURCE_ROLE_ASSIGNED: "Her lot için makine/kalıp gibi zorunlu roller atanmalı.",
  HARD_TIME_BUCKET_VALID: "Üretim zaman dilimleri bucket içinde kalmalı.",
  HARD_NO_HOLIDAY_WORK: "Tatil günlerinde üretim yapılmamalı.",
  HARD_COMPAT_MACHINE_MOLD_PROCESS: "Makine-kalıp-proses uyumlu olmalı.",
  HARD_COMPAT_PRODUCT_MOLD: "Ürün ve kalıp eşleşmesi geçerli olmalı.",
  HARD_CAPACITY_BUCKET: "Bucket kapasitesi aşılmamalı.",
  HARD_CAPACITY_SEGMENT: "Vardiya segment kapasitesi aşılmamalı.",
};

const SOFT_DESC = {
  SOFT_MOLD_CHANGE_MINIMIZE: "Kalıp değişimlerini minimize eder.",
  SOFT_NIGHT_MOLD_CHANGE: "Gece vardiyasında kalıp değişimini azaltır.",
  SOFT_INVENTORY_LOW: "Düşük stok seviyelerini tercih eder.",
  SOFT_MACHINE_COUNT_LOW: "Daha az makine kullanımını hedefler.",
};

const ALGO_DESC = {
  greedy: "Kurallı hızlı yerleştirme ile tek geçiş çözüm.",
  ga: "Evrimsel arama (GA) ile iteratif iyileştirme.",
  tabu: "Tabu arama ile yerel minimumdan kaçış.",
  gatabu: "GA sonrası tabu iyileştirmesi.",
  ga_tabu_inline: "GA içinde, jenerasyonlarda kısa tabu iyileştirmesi.",
  ga_tabu_topk: "GA sonrası en iyi K adayda tabu iyileştirmesi.",
};

const PARAM_DESC = {
  max_iter: "Maksimum iterasyon sayısı.",
  time_shift_hours: "Zaman kaydırma (saat).",
  bucket_shift: "Bucket kaydırma (index adımı).",
  bucket_shift_rate: "Bucket kaydırma uygulanma oranı (0-1).",
  qty_jitter_pct: "Lot miktarı için yüzde jitter (0-1).",
  qty_jitter_rate: "Miktar jitter uygulanma oranı (0-1).",
  machine_swap_rate: "Makine swap uygulanma oranı (0-1).",
  mold_swap_rate: "Kalıp swap uygulanma oranı (0-1).",
  mutation_seed: "Mutasyon için seed (0 = rastgele).",
  population_size: "GA popülasyon boyutu.",
  tabu_iter: "Kısa tabu iterasyon sayısı.",
  tabu_rate: "Tabu uygulama oranı (0-1).",
  top_k: "GA sonrası tabu uygulanacak aday sayısı.",
};

function setStatus(text, ok = true) {
  el("status-pill").textContent = text;
  el("status-pill").style.color = ok ? "#7ee787" : "#ff6b6b";
}

const stepButtons = Array.from(document.querySelectorAll(".step"));
const stepPanels = Array.from(document.querySelectorAll(".step-panel"));

function showStep(stepNo) {
  stepButtons.forEach((btn) => {
    btn.classList.toggle("active", Number(btn.dataset.step) === stepNo);
  });
  stepPanels.forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `step-${stepNo}`);
  });
  if (stepNo === 4) {
    ensureAlgorithms();
  }
}

function parseJsonInput() {
  const raw = el("problem-json").value.trim();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    setStatus("JSON hatalı", false);
    throw e;
  }
}

function normalizeProblem(payload) {
  if (!payload) return null;
  if (payload.problemData) return payload.problemData;
  if (payload.problem) return payload.problem;
  return payload;
}

function parseStateInput() {
  const raw = el("state-json").value.trim();
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (e) {
    setStatus("State JSON hatalı", false);
    throw e;
  }
}

function buildWorkCalendar(timeBuckets, baseCode) {
  if (!timeBuckets || !timeBuckets.length) return [];
  const toDate = (s) => new Date(s + "T00:00:00");
  const entries = [];
  let min = toDate(timeBuckets[0].start_date);
  let max = toDate(timeBuckets[0].end_date);
  for (const tb of timeBuckets) {
    const s = toDate(tb.start_date);
    const e = toDate(tb.end_date);
    if (s < min) min = s;
    if (e > max) max = e;
  }
  for (let d = new Date(min); d <= max; d.setDate(d.getDate() + 1)) {
    const iso = d.toISOString().slice(0, 10);
    entries.push({ date: iso, shift_templates_code: baseCode, holiday: false });
  }
  return entries;
}

function renderConstraints() {
  const container = el("constraints");
  container.innerHTML = "";
  for (const code of HARD_TOGGLES) {
    const item = document.createElement("div");
    item.className = "list-item";
    const desc = HARD_DESC[code] || "";
    item.innerHTML = `
      <div>
        <label>
          <input type="checkbox" data-code="${code}" data-type="hard" checked />
          ${code}
        </label>
        <span class="desc">${desc}</span>
      </div>
      <div class="meta">hard</div>
    `;
    container.appendChild(item);
  }
  for (const entry of SOFT_TOGGLES) {
    const item = document.createElement("div");
    item.className = "list-item";
    const desc = SOFT_DESC[entry.code] || "";
    const defaultWeight = DEFAULT_SCENARIO_WEIGHTS[entry.weight] ?? 1.0;
    item.innerHTML = `
      <div>
        <label>
          <input type="checkbox" data-code="${entry.code}" data-type="soft" checked />
          ${entry.code}
        </label>
        <span class="desc">${desc} (weight: ${entry.weight})</span>
      </div>
      <input type="number" step="0.1" value="${defaultWeight}" data-weight="${entry.weight}" />
    `;
    container.appendChild(item);
  }
  for (const entry of SCORE_WEIGHTS) {
    const item = document.createElement("div");
    item.className = "list-item";
    const defaultWeight = DEFAULT_SCENARIO_WEIGHTS[entry.key] ?? 1.0;
    item.innerHTML = `
      <div>
        <label>${entry.label}</label>
        <span class="desc">Skor için hard ihlal ağırlığı (weight: ${entry.key})</span>
      </div>
      <input type="number" step="0.1" value="${defaultWeight}" data-weight="${entry.key}" />
    `;
    container.appendChild(item);
  }
}

function buildScenarioConfig() {
  const toggles = {};
  const weights = {};
  document.querySelectorAll("#constraints input[type='checkbox']").forEach((cb) => {
    toggles[cb.dataset.code] = cb.checked;
  });
  document.querySelectorAll("#constraints input[data-weight]").forEach((inp) => {
    const key = inp.dataset.weight;
    weights[key] = Number(inp.value || 0);
  });
  return {
    meta: { name: "UI_Scenario" },
    toggles,
    weights,
  };
}

function populateShiftTemplates(problemData) {
  const sel = el("shift-template");
  sel.innerHTML = "";
  const templates = problemData?.shift_templates || [];
  sel.disabled = templates.length === 0;
  templates.forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t.code;
    opt.textContent = `${t.code} - ${t.name || ""}`;
    sel.appendChild(opt);
  });
  if (templates.length) {
    sel.value = problemData.problem_meta?.base_shift_templates_code || templates[0].code;
  }
}

function updateShiftInfo() {
  if (el("shift-template").disabled) {
    el("shift-info").textContent = "Vardiya şablonu bulunamadı.";
    return;
  }
  const code = el("shift-template").value || "-";
  el("shift-info").textContent = `Seçili şablon: ${code}`;
}

function updateProblemInfo(problemData) {
  if (!problemData) {
    el("problem-info").textContent = "Henüz yüklenmedi.";
    return;
  }
  const tb = problemData.time_buckets?.length || 0;
  const orders = problemData.orders?.length || 0;
  const products = problemData.products?.length || 0;
  el("problem-info").textContent = `Buckets: ${tb}, Orders: ${orders}, Products: ${products}`;
}

async function fetchAlgorithms(problemData) {
  const res = await fetch("/sse/algorithms", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ problemData }),
  });
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  state.algorithms = data.algorithms || [];
  renderAlgorithms();
}

async function ensureAlgorithms() {
  if (!state.problemData) {
    setStatus("Önce problem verisini yükle", false);
    return;
  }
  if (state.algorithmsLoaded || state.algorithmsLoading) return;
  state.algorithmsLoading = true;
  setStatus("Algoritmalar yükleniyor", true);
  try {
    await fetchAlgorithms(state.problemData);
    state.algorithmsLoaded = true;
    setStatus("Algoritmalar yüklendi", true);
  } catch (e) {
    setStatus("Algoritma listesi alınamadı", false);
  } finally {
    state.algorithmsLoading = false;
  }
}

function renderAlgorithms() {
  const sel = el("algo-select");
  sel.innerHTML = "";
  for (const algo of state.algorithms) {
    const opt = document.createElement("option");
    opt.value = algo.code;
    opt.textContent = algo.name;
    sel.appendChild(opt);
  }
  if (state.algorithms.length) {
    sel.value = state.algorithms[0].code;
  }
  renderAlgoParams();
}

function renderAlgoParams() {
  const sel = el("algo-select");
  const code = sel.value;
  el("algo-selected").textContent = code || "-";
  const algo = state.algorithms.find((a) => a.code === code);
  const container = el("algo-params");
  container.innerHTML = "";
  if (!algo) return;
  const algoDesc = algo.desc || ALGO_DESC[code] || "";
  if (algoDesc) {
    const descRow = document.createElement("div");
    descRow.className = "list-item";
    descRow.innerHTML = `
      <div>
        <strong>${algo.name}</strong>
        <span class="desc">${algoDesc}</span>
      </div>
      <div class="meta">algo</div>
    `;
    container.appendChild(descRow);
  }
  if (code === "tabu") {
    const warnRow = document.createElement("div");
    warnRow.className = "list-item";
    warnRow.innerHTML = `
      <div>
        <strong>Uyarı</strong>
        <span class="desc">Tabu, varsayılan olarak sınırlı komşuluk üretir. Bucket/qty/makine/kalıp oranlarını sıfırdan farklı yapmazsan sonuçlar düz kalabilir.</span>
      </div>
      <div class="meta">uyarı</div>
    `;
    container.appendChild(warnRow);
  }
  const params = algo.params || {};
  for (const [key, meta] of Object.entries(params)) {
    const desc = meta.desc || PARAM_DESC[key] || "";
    const step = meta.type === "float" ? "0.1" : "1";
    const minAttr = meta.min != null ? `min="${meta.min}"` : "";
    const maxAttr = meta.max != null ? `max="${meta.max}"` : "";
    const item = document.createElement("div");
    item.className = "list-item";
    item.innerHTML = `
      <div>
        <label>${key}</label>
        <span class="desc">${desc}</span>
      </div>
      <input type="number" step="${step}" value="${meta.default ?? 1}" data-param="${key}" ${minAttr} ${maxAttr} />
    `;
    container.appendChild(item);
  }
}

function buildAlgoPayload() {
  const code = el("algo-select").value;
  const params = {};
  document.querySelectorAll("#algo-params input[data-param]").forEach((inp) => {
    params[inp.dataset.param] = Number(inp.value || 0);
  });
  return { strategy: code, ...params };
}

async function ssePost(url, body, onEvent) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    throw new Error(await res.text());
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop();
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      const payload = JSON.parse(line.slice(5).trim());
      onEvent(payload);
    }
  }
}

function logIteration(ev) {
  const log = el("iterations");
  const entry = document.createElement("div");
  entry.className = "log-entry";
  if (ev.type === "iteration") {
    entry.textContent = `#${ev.iteration_no} | feasible=${ev.feasible} | cost=${ev.total_cost} | score=${ev.total_score ?? ev.total_cost}`;
  } else if (ev.type === "meta") {
    entry.textContent = `SSE başlatıldı: strategy=${ev.strategy}, max_iter=${ev.max_iter}`;
  } else if (ev.type === "done") {
    entry.textContent = `Bitti. Iterations=${(ev.result?.iterations || []).length}`;
  } else if (ev.type === "error") {
    entry.textContent = `Hata: ${ev.error}`;
  }
  log.prepend(entry);
}

function setProgress(pct) {
  el("progress-bar").style.width = `${pct}%`;
}

function setResult(obj) {
  state.result = obj;
  el("result-json").value = JSON.stringify(obj, null, 2);
  el("result-card").classList.remove("hidden");
}

function clearResult() {
  state.result = null;
  el("result-json").value = "";
  el("result-card").classList.add("hidden");
}

function buildFramePayload() {
  const scenarioConfig = buildScenarioConfig();
  state.scenarioConfig = scenarioConfig;
  let stateData = { meta: { iteration: 0 }, lots: [] };
  try {
    const custom = parseStateInput();
    if (custom) stateData = custom;
  } catch (e) {
    // handled via status
  }
  return { problemData: state.problemData, scenarioConfig, state: stateData };
}

function applyVardiya(vardiya) {
  if (!state.problemData) return;
  const templates = (vardiya?.shift_templates || []).map((t) => {
    return {
      code: t.id || t.code,
      name: t.name,
      segments: (t.segments || []).map((s) => {
        const endTime = s.end === "24:00" ? "00:00" : s.end;
        return {
          code: s.code,
          start: s.start,
          end: endTime,
          constraints: s.mold_change_allowed === false ? ["NO_MOLD_CHANGE_AT_NIGHT"] : [],
        };
      }),
    };
  });
  state.problemData.shift_templates = templates;
  const base = templates[0]?.code || state.problemData.problem_meta?.base_shift_templates_code;
  if (!state.problemData.problem_meta) state.problemData.problem_meta = { problem_code: "UI" };
  state.problemData.problem_meta.base_shift_templates_code = base;
  populateShiftTemplates(state.problemData);
  updateShiftInfo();
}

function rebuildCalendar() {
  if (!state.problemData) return;
  const base = el("shift-template").value;
  state.problemData.problem_meta.base_shift_templates_code = base;
  state.problemData.work_calendar = buildWorkCalendar(state.problemData.time_buckets || [], base);
  el("shift-info").textContent = `Seçili şablon: ${base} | Takvim: ${state.problemData.work_calendar.length} gün`;
}

// Event wiring
renderConstraints();
showStep(1);

stepButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    showStep(Number(btn.dataset.step));
  });
});

el("btn-parse").addEventListener("click", () => {
  try {
    const payload = parseJsonInput();
    if (!payload) return;
    state.problemData = normalizeProblem(payload);
    state.algorithmsLoaded = false;
    populateShiftTemplates(state.problemData);
    updateShiftInfo();
    updateProblemInfo(state.problemData);
    setStatus("Problem yüklendi", true);
    showStep(2);
  } catch (e) {
    setStatus("JSON hatalı", false);
  }
});

el("problem-file").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const text = await file.text();
  el("problem-json").value = text;
});

el("vardiya-file").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  const text = await file.text();
  try {
    const vardiya = JSON.parse(text);
    applyVardiya(vardiya);
    setStatus("Vardiya uygulandı", true);
    showStep(3);
  } catch (err) {
    setStatus("Vardiya JSON hatalı", false);
  }
});

el("btn-vardiya").addEventListener("click", () => {
  setStatus("Vardiya seçildi", true);
  updateShiftInfo();
  showStep(3);
});

el("btn-calendar").addEventListener("click", () => {
  rebuildCalendar();
  setStatus("Takvim güncellendi", true);
  showStep(3);
});

el("btn-algos").addEventListener("click", async () => {
  await ensureAlgorithms();
  showStep(4);
});

el("algo-select").addEventListener("change", renderAlgoParams);

el("btn-create-frame").addEventListener("click", async () => {
  try {
    if (!state.problemData) throw new Error("ProblemData yok");
    rebuildCalendar();
    const payload = buildFramePayload();
    const res = await fetch("/sse/frame", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    state.frameId = data.id;
    el("frame-id").textContent = data.id;
    if (data.algorithms) {
      state.algorithms = data.algorithms;
      state.algorithmsLoaded = true;
      renderAlgorithms();
    }
    setStatus("Frame hazır", true);
  } catch (e) {
    setStatus("Frame oluşturma hatası", false);
  }
});

el("btn-run").addEventListener("click", async () => {
  if (!state.frameId) {
    setStatus("Önce frame oluştur", false);
    return;
  }
  showStep(5);
  el("iterations").innerHTML = "";
  clearResult();
  setProgress(0);
  el("progress-bar").classList.add("loading");
  const algoPayload = buildAlgoPayload();
  const body = { frame_id: state.frameId, ...algoPayload };
  setStatus("SSE çalışıyor", true);

  let iterCount = 0;
  await ssePost("/sse/optimize/stream", body, (ev) => {
    logIteration(ev);
    if (ev.type === "iteration") {
      iterCount += 1;
      const pct = Math.min(100, (iterCount / (algoPayload.max_iter || 1)) * 100);
      setProgress(pct);
    }
    if (ev.type === "done") {
      setResult(ev.result || {});
      setProgress(100);
      el("progress-bar").classList.remove("loading");
      setStatus("Bitti", true);
    }
    if (ev.type === "error") {
      el("progress-bar").classList.remove("loading");
      setStatus("Hata", false);
    }
  });
});

el("btn-download").addEventListener("click", () => {
  if (!state.result) return;
  const blob = new Blob([JSON.stringify(state.result, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "result.json";
  a.click();
  URL.revokeObjectURL(url);
});

el("btn-sample").addEventListener("click", () => {
  const sample = {
    problemData: {
      problem_meta: { problem_code: "SAMPLE", horizon_type: "Week", base_shift_templates_code: "S3" },
      time_buckets: [{ id: "CW01_26", index: 0, start_date: "2026-01-05", end_date: "2026-01-11" }],
      orders: [],
      stocks: [],
      products: [],
      processes: [],
      resources: { machines: [], molds: [] },
      shift_templates: [{ code: "S3", name: "3'lü", segments: [
        { code: "NIGHT", start: "00:00", end: "08:00", constraints: [] },
        { code: "DAY", start: "08:00", end: "16:00", constraints: [] },
        { code: "EVE", start: "16:00", end: "00:00", constraints: [] }
      ] }],
      work_calendar: [{ date: "2026-01-05", shift_templates_code: "S3", holiday: false }],
      compatibility: { machine_mold_pairs: [], product_molds: [] }
    },
    scenarioConfig: { meta: { name: "Sample" }, toggles: {}, weights: {} },
    state: { meta: { iteration: 0 }, lots: [] }
  };
  el("problem-json").value = JSON.stringify(sample, null, 2);
});

el("btn-clear").addEventListener("click", () => {
  el("problem-json").value = "";
  clearResult();
  el("state-json").value = "";
  el("iterations").innerHTML = "";
  el("frame-id").textContent = "-";
  el("algo-selected").textContent = "-";
  state.problemData = null;
  state.frameId = null;
  state.algorithmsLoaded = false;
  setStatus("Hazır", true);
  showStep(1);
});

el("btn-constraints-continue").addEventListener("click", () => {
  showStep(4);
});

updateShiftInfo();
