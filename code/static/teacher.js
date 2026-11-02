/*
  Teacher view for the laptop.

  - The page asks the backend for kits, alerts, runs and sessions every 2 seconds.
  - Live readings for the chosen kit come over the WebSocket at /ws.
  - Nothing is stored in the browser. The database is the only storage.
  - Alert "kind" values from the backend: spike, flat, dropout, range, outlier.
*/
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const MAX_POINTS = 500;      // points kept for the detail graph
  const GRAPH_POINTS = 200;    // points drawn
  const POLL_MS = 2000;

  const EXPERIMENT_NAMES = {
    friction: "Friction",
    pendulum: "Pendulum",
    spring: "Spring",
    ohm: "Ohm's law",
    heat: "Heat change",
    light: "Light",
  };
  const UNIT_LABEL = { C: "°C", "m/s2": "m/s²" };
  const SENSOR_LABEL = {
    "HX711|N": "Force",
    "HC-SR04|cm": "Distance",
    "MPU6050|m/s2": "Acceleration",
    "INA219|V": "Voltage",
    "INA219|mA": "Current",
    "DHT22|C": "Temperature",
    "DHT22|%": "Humidity",
    "BH1750|lux": "Light",
  };
  const KIND_TEXT = {
    spike: "Sudden jump",
    flat: "Flat line (value not changing)",
    dropout: "No data",
    range: "Value out of range",
    outlier: "Unusual pattern",
  };
  const STATUS_TEXT = {
    online: "● Online",
    offline: "○ Offline",
    error: "▲ Error",
  };

  const unitLabel = (u) => UNIT_LABEL[u] || u;
  const expName = (id) => (id ? EXPERIMENT_NAMES[id] || id : "None yet");
  const seriesLabel = (sensor, unit) => SENSOR_LABEL[sensor + "|" + unit] || sensor;

  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  // Only change the page when the text really changed. This keeps scrolling and focus steady.
  function setHTML(el, html) {
    if (el._last !== html) {
      el._last = html;
      el.innerHTML = html;
    }
  }

  function isTeacher() {
    return document.body.dataset.view === "teacher";
  }

  // ------------------------------------------------------------------- state

  const state = {
    kits: [],
    alerts: [],
    runs: [],
    sessions: [],
    filters: { group: "", experiment: "", status: "" },
    selected: null,
    seriesKey: null,
    exportSession: null,
  };
  const store = {};      // selected kit only: "experiment|sensor|unit" -> [{ v, ts }]
  const lastExp = {};    // kit_id -> experiment of the newest reading

  // -------------------------------------------------------------- formatting

  function fmtTime(sec) {
    if (!sec) return "—";
    return new Date(sec * 1000).toLocaleTimeString([], { hour12: false });
  }

  function fmtDateTime(sec) {
    if (!sec) return "—";
    const d = new Date(sec * 1000);
    const today = new Date();
    const time = d.toLocaleTimeString([], { hour12: false });
    return d.toDateString() === today.toDateString() ? time : d.toLocaleDateString() + " " + time;
  }

  function fmtDuration(start, end) {
    if (!start || !end) return "—";
    const s = Math.max(0, Math.round(end - start));
    return s < 60 ? s + " s" : Math.floor(s / 60) + " min " + (s % 60) + " s";
  }

  // The student view saves results as JSON in the notes. Show it as plain text.
  function runSummary(notes) {
    try {
      const n = JSON.parse(notes);
      if (n && Array.isArray(n.rows)) {
        const parts = n.rows.map((r) => {
          const dec = typeof r.dec === "number" ? r.dec : 2;
          return r.label + ": " + Number(r.avg).toFixed(dec) + " " + unitLabel(r.unit) + " (average)";
        });
        (n.extras || []).forEach((e) => parts.push(e.label + ": " + e.value));
        return parts.join("  ·  ");
      }
    } catch (e) {
      // Not JSON. Show the text as it is.
    }
    return notes || "";
  }

  // ---------------------------------------------------------------- network

  async function api(path, method, body) {
    const options = { method: method || "GET" };
    if (body !== undefined) {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify(body);
    }
    const res = await fetch(path, options);
    if (!res.ok) {
      let detail = "";
      try {
        const data = await res.json();
        detail = typeof data.detail === "string" ? data.detail : "Please check what you typed.";
      } catch (e) {
        detail = "Error " + res.status;
      }
      throw new Error(detail);
    }
    return res.json();
  }

  let msgTimer = null;
  function say(text) {
    const el = $("t-msg");
    el.textContent = text;
    clearTimeout(msgTimer);
    msgTimer = setTimeout(() => { el.textContent = ""; }, 5000);
  }

  let refreshing = false;
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      const results = await Promise.all([
        api("/api/kits"), api("/api/alerts"), api("/api/runs"), api("/api/sessions"),
      ]);
      state.kits = results[0];
      state.alerts = results[1];
      state.runs = results[2];
      state.sessions = results[3];
      setLink(true);
      renderAll();
    } catch (e) {
      setLink(false);
    } finally {
      refreshing = false;
    }
  }

  function setLink(ok) {
    const el = $("t-link");
    el.textContent = ok ? "● Backend connected" : "○ Backend not reachable";
    el.className = "pill " + (ok ? "pill-online" : "pill-error");
  }

  // ----------------------------------------------------------------- graph
  // This small canvas graph is also in student.js, so each file works alone.

  function axisDigits(range) {
    if (range < 1) return 3;
    if (range < 10) return 2;
    if (range < 100) return 1;
    return 0;
  }

  function drawGraph(canvas, values, title, note) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (!w || !h) return;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const css = getComputedStyle(document.documentElement);
    const gridColor = css.getPropertyValue("--graph-grid").trim() || "#ccc";
    const lineColor = css.getPropertyValue("--graph-line").trim() || "#1B7F8C";
    const textColor = css.getPropertyValue("--muted").trim() || "#555";

    ctx.fillStyle = textColor;
    ctx.font = "bold 13px sans-serif";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    if (title) ctx.fillText(title, 56, 6);
    ctx.textAlign = "right";
    if (note) ctx.fillText(note, w - 10, 6);

    if (values.length < 2) {
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("Waiting for data...", w / 2, h / 2);
      return;
    }

    let min = Math.min.apply(null, values);
    let max = Math.max.apply(null, values);
    if (max - min < 1e-9) { min -= 1; max += 1; }
    const pad = (max - min) * 0.1;
    min -= pad;
    max += pad;

    const left = 50, right = 10, top = 28, bottom = 12;
    const plotW = w - left - right;
    const plotH = h - top - bottom;
    const digits = axisDigits(max - min);

    ctx.lineWidth = 1;
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = textColor;
    ctx.font = "12px sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let i = 0; i < 5; i++) {
      const y = top + (plotH * i) / 4;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(w - right, y);
      ctx.stroke();
      ctx.fillText((max - ((max - min) * i) / 4).toFixed(digits), left - 6, y);
    }

    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = "round";
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = left + (plotW * i) / (values.length - 1);
      const y = top + plotH * (1 - (v - min) / (max - min));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  let graphQueued = false;
  function scheduleGraph() {
    if (graphQueued) return;
    graphQueued = true;
    requestAnimationFrame(() => {
      graphQueued = false;
      drawDetailGraph();
    });
  }

  function seriesKeysFor(kitId) {
    const exp = lastExp[kitId] || (state.kits.find((k) => k.kit_id === kitId) || {}).experiment;
    return Object.keys(store).filter((key) => key.split("|")[0] === exp);
  }

  function drawDetailGraph() {
    if (!isTeacher() || !state.selected) return;
    const keys = seriesKeysFor(state.selected);
    const key = keys.indexOf(state.seriesKey) >= 0 ? state.seriesKey : keys[0];
    const points = key ? store[key].slice(-GRAPH_POINTS) : [];
    const values = points.map((p) => p.v);
    let title = "";
    let note = "";
    if (key) {
      const parts = key.split("|");
      title = seriesLabel(parts[1], parts[2]) + " (" + unitLabel(parts[2]) + ")";
      if (points.length > 1) {
        const span = points[points.length - 1].ts - points[0].ts;
        if (span > 0 && span < 36000) note = "last " + Math.round(span) + " s";
      }
    }
    drawGraph($("d-graph"), values, title, note);
  }

  // ----------------------------------------------------------------- render

  function renderAll() {
    renderSession();
    renderFilters();
    renderCards();
    renderDetail();
    renderResults();
    renderExport();
    renderAlerts();
  }

  function renderSession() {
    const active = state.sessions.find((s) => !s.ended_at);
    $("t-session-text").textContent = active
      ? "Session running: " + active.name + " (since " + fmtTime(active.started_at) + ")"
      : "No session running.";
    $("t-end-session").disabled = !active;
  }

  function uniqueSorted(list) {
    return Array.from(new Set(list.filter(Boolean))).sort();
  }

  function fillSelect(select, firstLabel, values, labelFn, current) {
    let html = '<option value="">' + esc(firstLabel) + "</option>";
    values.forEach((v) => {
      html += '<option value="' + esc(v) + '">' + esc(labelFn ? labelFn(v) : v) + "</option>";
    });
    setHTML(select, html);
    select.value = values.indexOf(current) >= 0 ? current : "";
    return select.value;
  }

  function renderFilters() {
    const f = state.filters;
    f.group = fillSelect($("t-filter-group"), "All groups", uniqueSorted(state.kits.map((k) => k.group)), null, f.group);
    f.experiment = fillSelect($("t-filter-exp"), "All experiments", uniqueSorted(state.kits.map((k) => k.experiment)), expName, f.experiment);
  }

  function visibleKits() {
    const f = state.filters;
    return state.kits.filter((k) =>
      (!f.group || k.group === f.group) &&
      (!f.experiment || k.experiment === f.experiment) &&
      (!f.status || k.status === f.status)
    );
  }

  function renderCards() {
    const kits = visibleKits();
    if (!kits.length) {
      setHTML($("t-cards"), '<div class="empty">' + (state.kits.length
        ? "No kits match these filters."
        : "No kits yet. Start the simulator or add a group.") + "</div>");
      return;
    }
    const html = kits.map((k) => {
      return '<button type="button" class="kit-card status-' + esc(k.status) +
        (k.kit_id === state.selected ? " selected" : "") + '" data-kit="' + esc(k.kit_id) + '">' +
        '<span class="kit-top"><span class="kit-id">' + esc(k.kit_id) + "</span>" +
        '<span class="pill pill-' + esc(k.status) + '">' + esc(STATUS_TEXT[k.status] || k.status) + "</span></span>" +
        '<span class="kit-line"><b>Group</b>' + esc(k.group || "—") + "</span>" +
        '<span class="kit-line"><b>Experiment</b>' + esc(expName(k.experiment)) + "</span>" +
        '<span class="kit-line"><b>Last reading</b>' + esc(fmtTime(k.last_seen)) + "</span>" +
        '<span class="kit-line"><b>Sensors</b>' + esc(k.sensors.length ? k.sensors.join(", ") : "—") + "</span>" +
        "</button>";
    }).join("");
    setHTML($("t-cards"), html);
  }

  function renderDetail() {
    const kit = state.kits.find((k) => k.kit_id === state.selected);
    $("t-detail-empty").hidden = !!kit;
    $("t-detail-body").hidden = !kit;
    if (!kit) return;

    $("d-title").textContent = kit.kit_id + " · " + (kit.group || "no group") + " · " + expName(kit.experiment);
    const status = $("d-status");
    status.textContent = STATUS_TEXT[kit.status] || kit.status;
    status.className = "pill pill-" + kit.status;

    // Buttons for each reading of the current experiment.
    const keys = seriesKeysFor(kit.kit_id);
    if (keys.indexOf(state.seriesKey) < 0) state.seriesKey = keys[0] || null;
    setHTML($("d-series"), keys.map((key) => {
      const parts = key.split("|");
      return '<button type="button" class="chip" data-key="' + esc(key) + '" aria-pressed="' +
        String(key === state.seriesKey) + '">' + esc(seriesLabel(parts[1], parts[2])) +
        " (" + esc(unitLabel(parts[2])) + ")</button>";
    }).join(""));

    // Saved runs for this kit.
    const runs = state.runs.filter((r) => r.kit_id === kit.kit_id).slice(0, 20);
    setHTML($("d-runs"), runs.length
      ? '<table class="table"><thead><tr><th>Started</th><th>Length</th><th>Results</th></tr></thead><tbody>' +
        runs.map((r) =>
          "<tr><td>" + esc(fmtDateTime(r.started_at)) + "</td><td>" + esc(fmtDuration(r.started_at, r.ended_at)) +
          "</td><td>" + esc(runSummary(r.notes)) + "</td></tr>").join("") +
        "</tbody></table>"
      : '<p class="muted">No saved runs yet.</p>');

    // AI notes for this kit (open and resolved).
    const notes = state.alerts.filter((a) => a.kit_id === kit.kit_id).slice(0, 20);
    setHTML($("d-notes"), notes.length
      ? notes.map((a) =>
          '<div class="alert-item' + (a.resolved ? " done" : "") + '">' +
          '<div class="alert-top"><span>' + esc(a.sensor) + "</span><span>" + esc(fmtTime(a.ts)) + "</span></div>" +
          '<div class="alert-kind">' + esc(KIND_TEXT[a.kind] || a.kind) + (a.resolved ? " (resolved)" : "") + "</div>" +
          "<p>" + esc(a.teacher_text || "No explanation yet.") + "</p></div>").join("")
      : '<p class="muted">No AI notes for this kit.</p>');

    scheduleGraph();
  }

  function renderResults() {
    const runs = state.runs.slice(0, 200);
    setHTML($("t-results"), runs.length
      ? '<table class="table"><thead><tr><th>Group</th><th>Kit</th><th>Experiment</th><th>Started</th><th>Length</th><th>Results</th></tr></thead><tbody>' +
        runs.map((r) =>
          "<tr><td>" + esc(r.group || "—") + "</td><td>" + esc(r.kit_id) + "</td><td>" + esc(expName(r.experiment)) +
          "</td><td>" + esc(fmtDateTime(r.started_at)) + "</td><td>" + esc(fmtDuration(r.started_at, r.ended_at)) +
          "</td><td>" + esc(runSummary(r.notes)) + "</td></tr>").join("") +
        "</tbody></table>"
      : '<p class="muted">No saved runs yet. Students press "Save run" on their kit.</p>');
  }

  function renderExport() {
    const select = $("t-export-session");
    let html = '<option value="">All data</option>';
    state.sessions.forEach((s) => {
      html += '<option value="' + esc(s.id) + '">' + esc(s.name) + " (" + esc(fmtDateTime(s.started_at)) + ")" +
        (s.ended_at ? "" : " - running") + "</option>";
    });
    setHTML(select, html);
    let want = state.exportSession;
    if (want === null) want = state.sessions.length ? String(state.sessions[0].id) : "";
    select.value = want;
    if (select.value !== want) select.value = "";
  }

  function renderAlerts() {
    const open = state.alerts.filter((a) => !a.resolved);
    $("t-alert-count").textContent = String(open.length);
    setHTML($("t-alerts"), open.length
      ? open.map((a) =>
          '<article class="alert-item">' +
          '<div class="alert-top"><span><b>' + esc(a.kit_id) + "</b> · " + esc(a.sensor) + "</span><span>" + esc(fmtTime(a.ts)) + "</span></div>" +
          '<div class="alert-kind">' + esc(KIND_TEXT[a.kind] || a.kind) + "</div>" +
          "<p>" + esc(a.teacher_text || "No explanation yet.") + "</p>" +
          (a.student_text ? '<p class="alert-student">Students see: ' + esc(a.student_text) + "</p>" : "") +
          '<button type="button" class="btn btn-small" data-resolve="' + esc(a.id) + '">Mark resolved</button>' +
          "</article>").join("")
      : '<p class="muted">No open alerts.</p>');
  }

  // ----------------------------------------------------------------- actions

  async function selectKit(kitId) {
    state.selected = kitId;
    state.seriesKey = null;
    Object.keys(store).forEach((k) => delete store[k]);
    renderCards();
    renderDetail();
    try {
      const rows = await api("/api/readings?kit_id=" + encodeURIComponent(kitId) + "&limit=" + MAX_POINTS);
      if (state.selected !== kitId) return;
      rows.forEach((r) => addPoint(r));
      if (rows.length) lastExp[kitId] = rows[rows.length - 1].experiment;
      renderDetail();
    } catch (e) {
      say("Could not load readings.");
    }
  }

  function addPoint(r) {
    const key = r.experiment + "|" + r.sensor + "|" + r.unit;
    if (!store[key]) store[key] = [];
    store[key].push({ v: r.value, ts: r.ts });
    if (store[key].length > MAX_POINTS) store[key].shift();
  }

  function handleReading(r) {
    lastExp[r.kit_id] = r.experiment;
    if (r.kit_id !== state.selected) return;
    addPoint(r);
    scheduleGraph();
  }

  async function startSession(event) {
    event.preventDefault();
    try {
      const s = await api("/api/sessions/start", "POST", { name: $("t-session-name").value });
      $("t-session-name").value = "";
      state.exportSession = null;
      say("Session started: " + s.name);
      refresh();
    } catch (e) {
      say("Could not start the session. " + e.message);
    }
  }

  async function endSession() {
    try {
      await api("/api/sessions/end", "POST");
      say("Session ended.");
      refresh();
    } catch (e) {
      say("Could not end the session. " + e.message);
    }
  }

  async function addGroup(event) {
    event.preventDefault();
    const group = $("t-group").value.trim();
    const kit = $("t-kit").value.trim();
    if (!group || !kit) {
      say("Type a group name and a kit ID.");
      return;
    }
    try {
      await api("/api/kits", "POST", { kit_id: kit, group: group });
      $("t-group").value = "";
      $("t-kit").value = "";
      say("Added " + group + " with kit " + kit + ".");
      refresh();
    } catch (e) {
      say("Could not add the group. " + e.message);
    }
  }

  async function resolveAlert(id) {
    try {
      await api("/api/alerts/" + encodeURIComponent(id) + "/resolve", "POST");
      refresh();
    } catch (e) {
      say("Could not resolve the alert. " + e.message);
    }
  }

  function exportCsv() {
    const id = $("t-export-session").value;
    window.location.href = "/api/export.csv" + (id ? "?session_id=" + encodeURIComponent(id) : "");
  }

  // ---------------------------------------------------------------- WebSocket

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(proto + "//" + location.host + "/ws");
    ws.onclose = () => setTimeout(connect, 2000);
    ws.onerror = () => ws.close();
    ws.onmessage = (event) => {
      if (!isTeacher()) return;
      let msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      if (msg.type === "reading") {
        handleReading(msg.data);
      } else if (msg.type === "alert") {
        if (!state.alerts.some((a) => a.id === msg.data.id)) {
          state.alerts.unshift(msg.data);
          renderAlerts();
          renderDetail();
        }
        refresh();
      }
    };
  }

  // -------------------------------------------------------------------- start

  function init() {
    $("t-session-form").addEventListener("submit", startSession);
    $("t-end-session").addEventListener("click", endSession);
    $("t-group-form").addEventListener("submit", addGroup);
    $("t-export").addEventListener("click", exportCsv);
    $("t-export-session").addEventListener("change", (e) => { state.exportSession = e.target.value; });

    $("t-filter-group").addEventListener("change", (e) => { state.filters.group = e.target.value; renderCards(); });
    $("t-filter-exp").addEventListener("change", (e) => { state.filters.experiment = e.target.value; renderCards(); });
    $("t-filter-status").addEventListener("change", (e) => { state.filters.status = e.target.value; renderCards(); });

    $("t-cards").addEventListener("click", (e) => {
      const card = e.target.closest("[data-kit]");
      if (card) selectKit(card.dataset.kit);
    });
    $("d-series").addEventListener("click", (e) => {
      const chip = e.target.closest("[data-key]");
      if (chip) { state.seriesKey = chip.dataset.key; renderDetail(); }
    });
    $("t-alerts").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-resolve]");
      if (btn) resolveAlert(btn.dataset.resolve);
    });

    window.addEventListener("resize", scheduleGraph);
    window.addEventListener("viewchange", () => { if (isTeacher()) { refresh(); scheduleGraph(); } });
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", scheduleGraph);
    }

    setInterval(() => { if (isTeacher()) refresh(); }, POLL_MS);
    connect();
    if (isTeacher()) refresh();
  }

  init();
})();
