/*
  Student view for the 4-inch touchscreen (480x320).

  - Which kit is this? Use ?kit=K03 in the address. The default is K01.
  - Live readings come over the WebSocket at /ws.
  - Nothing is stored in the browser. Saved runs go to the backend (POST /api/runs).
  - Graphs use the plain canvas, so everything works offline.
  - The experiment ids below (friction, pendulum, spring, ohm, heat, light) must be the
    "experiment" values the real kit sends.
*/
(function () {
  "use strict";

  const KIT = (new URLSearchParams(location.search).get("kit") || "K01").trim();
  const MAX_RECENT = 150;      // points kept for the live graph
  const MAX_RECORDED = 5000;   // points kept while recording
  const STALE_MS = 3000;       // a reading older than this shows as "--"

  const UNIT_LABEL = { C: "°C", "m/s2": "m/s²" };
  const unitLabel = (u) => UNIT_LABEL[u] || u;
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------ maths helpers

  function average(values) {
    return values.reduce((a, b) => a + b, 0) / values.length;
  }

  // Time for one full swing. Counts how often the value goes from low to high.
  // Uses the time each reading arrived.
  function swingPeriod(points) {
    if (points.length < 8) return null;
    const values = points.map((p) => p.v);
    const min = Math.min.apply(null, values);
    const max = Math.max.apply(null, values);
    const range = max - min;
    if (range < 1e-6) return null;
    const mid = average(values);
    const high = mid + 0.15 * range;
    const low = mid - 0.15 * range;
    let state = null;
    const crossings = [];
    points.forEach((p) => {
      if (p.v < low) {
        state = "low";
      } else if (p.v > high && state === "low") {
        crossings.push(p.t);
        state = "high";
      }
    });
    if (crossings.length < 3) return null;
    return (crossings[crossings.length - 1] - crossings[0]) / (crossings.length - 1);
  }

  function periodExtra(label) {
    return function (data) {
      const p = swingPeriod(data[0]);
      return [{ label: label, value: p ? p.toFixed(2) + " s" : "Not enough swings yet" }];
    };
  }

  function ohmExtras(data) {
    if (!data[0].length || !data[1].length) return [];
    const volts = average(data[0].map((p) => p.v));
    const milliamps = average(data[1].map((p) => p.v));
    if (milliamps < 0.01) return [];
    return [{ label: "Resistance", value: Math.round(volts / (milliamps / 1000)) + " Ω" }];
  }

  function heatExtras(data) {
    const d = data[0];
    if (d.length < 2) return [];
    const change = d[d.length - 1].v - d[0].v;
    return [{ label: "Temperature change", value: (change > 0 ? "+" : "") + change.toFixed(1) + " °C" }];
  }

  // ------------------------------------------------------------- experiments

  const EXPERIMENTS = [
    {
      id: "friction", title: "Friction", sub: "Pull a block",
      channels: [
        { sensor: "HX711", unit: "N", label: "Force", dec: 2 },
        { sensor: "HC-SR04", unit: "cm", label: "Distance", dec: 1 },
      ],
      steps: [
        "Put the block on the table.",
        "Tie the string to the force sensor.",
        "Press Start. Pull the block at a steady speed.",
        "Press Stop. Look at the average force.",
      ],
    },
    {
      id: "pendulum", title: "Pendulum", sub: "Time the swing",
      channels: [{ sensor: "MPU6050", unit: "m/s2", label: "Swing", dec: 2 }],
      extras: periodExtra("Swing time"),
      steps: [
        "Hang the weight on the string.",
        "Pull it a little to one side.",
        "Press Start. Let it go.",
        "Let it swing 10 times. Press Stop.",
      ],
    },
    {
      id: "spring", title: "Spring", sub: "Bounce a mass",
      channels: [
        { sensor: "HC-SR04", unit: "cm", label: "Mass position", dec: 1 },
        { sensor: "HX711", unit: "N", label: "Force", dec: 2 },
      ],
      extras: periodExtra("Bounce time"),
      steps: [
        "Hang the spring from the stand.",
        "Add the mass.",
        "Press Start. Pull the mass down a little. Let go.",
        "Press Stop after 10 bounces.",
      ],
    },
    {
      id: "ohm", title: "Ohm's law", sub: "Voltage and current",
      channels: [
        { sensor: "INA219", unit: "V", label: "Voltage", dec: 2 },
        { sensor: "INA219", unit: "mA", label: "Current", dec: 1 },
      ],
      extras: ohmExtras,
      steps: [
        "Connect the parts as your teacher shows you.",
        "Ask your teacher to check the wires.",
        "Press Start. Change the voltage a little at a time.",
        "Press Stop. Look at voltage and current.",
      ],
    },
    {
      id: "heat", title: "Heat change", sub: "Log temperature",
      channels: [{ sensor: "DHT22", unit: "C", label: "Temperature", dec: 1 }],
      extras: heatExtras,
      steps: [
        "Put the sensor in place.",
        "Ask your teacher before you heat anything.",
        "Press Start. Watch the temperature change.",
        "Press Stop when your teacher says so.",
      ],
    },
    {
      id: "light", title: "Light", sub: "Light and distance",
      channels: [
        { sensor: "BH1750", unit: "lux", label: "Light", dec: 0 },
        { sensor: "INA219", unit: "mA", label: "Light sensor current", dec: 2 },
      ],
      steps: [
        "Fix the lamp in one place.",
        "Put the light sensor close to the lamp.",
        "Press Start. Move the sensor away a little at a time.",
        "Press Stop. Compare the readings.",
      ],
    },
  ];

  // ------------------------------------------------------------------- state

  let current = null;          // the chosen experiment, or null
  let graphChannel = 0;        // which reading the graph shows
  let groupName = "";
  const recent = {};           // "experiment|sensor|unit" -> last values
  const latest = {};           // "experiment|sensor|unit" -> { value, at }
  const rec = {
    on: false, hasRun: false, saved: false,
    startPerf: 0, stopPerf: 0, startedAtMs: 0, endedAtMs: 0,
    data: [],                  // one list of { t, v } for each reading of the experiment
  };

  const chKey = (ch) => current.id + "|" + ch.sensor + "|" + ch.unit;

  // ---------------------------------------------------------------- graph
  // This small canvas graph is also in teacher.js, so each file works alone.

  function axisDigits(range) {
    if (range < 1) return 3;
    if (range < 10) return 2;
    if (range < 100) return 1;
    return 0;
  }

  function drawGraph(canvas, values) {
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

    if (values.length < 2) {
      ctx.fillStyle = textColor;
      ctx.font = "bold 14px sans-serif";
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

    const left = 40, right = 8, top = 10, bottom = 10;
    const plotW = w - left - right;
    const plotH = h - top - bottom;
    const digits = axisDigits(max - min);

    ctx.lineWidth = 1;
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = textColor;
    ctx.font = "11px sans-serif";
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let i = 0; i < 3; i++) {
      const y = top + (plotH * i) / 2;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(w - right, y);
      ctx.stroke();
      ctx.fillText((max - ((max - min) * i) / 2).toFixed(digits), left - 5, y);
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

  // ------------------------------------------------------------------ screens

  function showScreen(name) {
    $("s-home").hidden = name !== "home";
    $("s-exp").hidden = name !== "exp";
  }

  function showPane(name) {
    ["live", "steps", "results"].forEach((p) => {
      $("pane-" + p).hidden = p !== name;
    });
    document.querySelectorAll(".s-tabs button").forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.pane === name));
    });
    if (name === "results") renderResults();
    if (name === "live") renderLive();
  }

  function buildHome() {
    const grid = $("s-grid");
    grid.innerHTML = "";
    EXPERIMENTS.forEach((exp, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "s-exp-btn";
      b.innerHTML =
        '<span class="s-exp-num"></span><span class="s-exp-name"></span><span class="s-exp-sub"></span>';
      b.querySelector(".s-exp-num").textContent = String(i + 1);
      b.querySelector(".s-exp-name").textContent = exp.title;
      b.querySelector(".s-exp-sub").textContent = exp.sub;
      b.addEventListener("click", () => openExperiment(exp.id));
      grid.appendChild(b);
    });
  }

  function buildTiles() {
    const box = $("s-tiles");
    box.innerHTML = "";
    box.classList.toggle("single", current.channels.length === 1);
    current.channels.forEach((ch, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "s-tile";
      b.innerHTML =
        '<span class="s-tile-label"></span><span class="s-tile-value"><span class="num">--</span> <small></small></span>';
      b.querySelector(".s-tile-label").textContent = ch.label;
      b.querySelector("small").textContent = unitLabel(ch.unit);
      b.addEventListener("click", () => { graphChannel = i; renderLive(); });
      box.appendChild(b);
    });
  }

  function buildSteps() {
    const list = $("s-steps");
    list.innerHTML = "";
    current.steps.forEach((text) => {
      const li = document.createElement("li");
      li.textContent = text;
      list.appendChild(li);
    });
  }

  function openExperiment(id) {
    current = EXPERIMENTS.find((e) => e.id === id) || null;
    if (!current) return;
    graphChannel = 0;
    resetRun();
    $("s-title").textContent = current.title;
    buildTiles();
    buildSteps();
    showScreen("exp");
    showPane("live");
  }

  function goHome() {
    if (rec.on) return;
    current = null;
    showScreen("home");
  }

  // ---------------------------------------------------------- recording

  function resetRun() {
    rec.on = false;
    rec.hasRun = false;
    rec.saved = false;
    rec.startPerf = 0;
    rec.stopPerf = 0;
    rec.data = current ? current.channels.map(() => []) : [];
    $("s-save-msg").textContent = "";
    updateButtons();
    renderTimer();
    renderResults();
  }

  function startRun() {
    if (!current || rec.on) return;
    rec.data = current.channels.map(() => []);
    rec.on = true;
    rec.hasRun = true;
    rec.saved = false;
    rec.startPerf = performance.now();
    rec.startedAtMs = Date.now();
    $("s-save-msg").textContent = "";
    updateButtons();
    renderTimer();
    renderResults();
  }

  function stopRun() {
    if (!rec.on) return;
    rec.on = false;
    rec.stopPerf = performance.now();
    rec.endedAtMs = Date.now();
    updateButtons();
    renderTimer();
    showPane("results");
  }

  function elapsedSeconds() {
    if (!rec.hasRun) return 0;
    const end = rec.on ? performance.now() : rec.stopPerf;
    return Math.max(0, (end - rec.startPerf) / 1000);
  }

  function totalCount() {
    return rec.data.reduce((n, list) => n + list.length, 0);
  }

  function updateButtons() {
    $("s-start").disabled = rec.on;
    $("s-stop").disabled = !rec.on;
    $("s-reset").disabled = !rec.on && !rec.hasRun;
    $("s-back").disabled = rec.on;
    $("s-save").disabled = !(rec.hasRun && !rec.on && !rec.saved && totalCount() > 0);
    $("s-timer").classList.toggle("recording", rec.on);
  }

  function renderTimer() {
    const s = Math.floor(elapsedSeconds());
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    $("s-timer").textContent = mm + ":" + ss;
  }

  // ------------------------------------------------------------ results

  function summarise() {
    const rows = [];
    current.channels.forEach((ch, i) => {
      const values = rec.data[i].map((p) => p.v);
      if (!values.length) return;
      rows.push({
        label: ch.label,
        unit: ch.unit,
        dec: ch.dec,
        avg: average(values),
        min: Math.min.apply(null, values),
        max: Math.max.apply(null, values),
        count: values.length,
      });
    });
    const extras = current.extras ? current.extras(rec.data) : [];
    return { v: 1, seconds: Math.round(elapsedSeconds()), rows: rows, extras: extras };
  }

  function cell(text, tag) {
    const el = document.createElement(tag || "td");
    el.textContent = text;
    return el;
  }

  function renderResults() {
    const empty = $("s-results-empty");
    const table = $("s-table");
    const body = $("s-table-body");
    const count = $("s-count");
    body.innerHTML = "";
    count.textContent = "";

    if (!current || !rec.hasRun) {
      empty.textContent = "Press Start on the Live tab to record.";
      empty.hidden = false;
      table.hidden = true;
      return;
    }
    if (rec.on) {
      empty.textContent = "Recording. Press Stop to see the results.";
      empty.hidden = false;
      table.hidden = true;
      return;
    }

    const s = summarise();
    if (!s.rows.length) {
      empty.textContent = "No readings came in. Check that the kit is on.";
      empty.hidden = false;
      table.hidden = true;
      return;
    }
    empty.hidden = true;
    table.hidden = false;
    s.rows.forEach((r) => {
      const tr = document.createElement("tr");
      const u = " " + unitLabel(r.unit);
      tr.appendChild(cell(r.label));
      tr.appendChild(cell(r.avg.toFixed(r.dec) + u));
      tr.appendChild(cell(r.min.toFixed(r.dec) + u));
      tr.appendChild(cell(r.max.toFixed(r.dec) + u));
      body.appendChild(tr);
    });
    s.extras.forEach((e) => {
      const tr = document.createElement("tr");
      tr.className = "extra";
      tr.appendChild(cell(e.label));
      const td = cell(e.value);
      td.colSpan = 3;
      tr.appendChild(td);
      body.appendChild(tr);
    });
    count.textContent = "Readings: " + s.rows[0].count + "   Time: " + s.seconds + " s";
  }

  async function saveRun() {
    if (!current || !rec.hasRun || rec.on || rec.saved) return;
    const exp = current;
    const summary = summarise();
    $("s-save").disabled = true;
    try {
      const res = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          kit_id: KIT,
          experiment: exp.id,
          started_at: rec.startedAtMs / 1000,
          ended_at: rec.endedAtMs / 1000,
          notes: JSON.stringify(summary),
        }),
      });
      if (!res.ok) throw new Error("save failed");
      rec.saved = true;
      $("s-save-msg").textContent = "Run saved.";
    } catch (e) {
      $("s-save-msg").textContent = "Could not save. Try again.";
    }
    updateButtons();
  }

  // ------------------------------------------------------------ live view

  let renderQueued = false;
  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => {
      renderQueued = false;
      renderLive();
    });
  }

  function renderLive() {
    if (!current || $("pane-live").hidden || $("s-exp").hidden) return;
    const now = performance.now();
    const tiles = $("s-tiles").children;
    current.channels.forEach((ch, i) => {
      const info = latest[chKey(ch)];
      const fresh = info && now - info.at < STALE_MS;
      tiles[i].querySelector(".num").textContent = fresh ? info.value.toFixed(ch.dec) : "--";
      tiles[i].setAttribute("aria-pressed", String(i === graphChannel));
    });
    const key = chKey(current.channels[graphChannel]);
    drawGraph($("s-graph"), recent[key] || []);
  }

  function handleReading(r) {
    if (r.kit_id !== KIT) return;
    const key = r.experiment + "|" + r.sensor + "|" + r.unit;
    if (!recent[key]) recent[key] = [];
    recent[key].push(r.value);
    if (recent[key].length > MAX_RECENT) recent[key].shift();
    latest[key] = { value: r.value, at: performance.now() };

    if (!current || r.experiment !== current.id) return;
    const idx = current.channels.findIndex((c) => c.sensor === r.sensor && c.unit === r.unit);
    if (idx < 0) return;
    if (rec.on) {
      const list = rec.data[idx];
      list.push({ t: performance.now() / 1000, v: r.value });
      if (list.length > MAX_RECORDED) list.shift();
    }
    scheduleRender();
  }

  // -------------------------------------------------------------- AI hint

  const HINT_FALLBACK = {
    spike: "One reading jumped. Check that the wires are tight.",
    flat: "The reading is not changing. Check the sensor and the wires.",
    dropout: "The readings stopped. Check that the wires are tight.",
    range: "The reading looks wrong. Check where the sensor is.",
    outlier: "The readings look odd. Check your setup.",
  };

  function setHint(alert) {
    const box = $("s-hint");
    const text = $("s-hint-text");
    if (!alert) {
      box.classList.remove("alert");
      text.textContent = "No problems found.";
      return;
    }
    box.classList.add("alert");
    text.textContent = alert.student_text || HINT_FALLBACK[alert.kind] || "Check your setup.";
  }

  async function getJSON(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(path + " " + res.status);
    return res.json();
  }

  async function poll() {
    if (document.body.dataset.view !== "student") return;
    try {
      const results = await Promise.all([getJSON("/api/kits"), getJSON("/api/alerts?resolved=false")]);
      const me = results[0].find((k) => k.kit_id === KIT);
      groupName = me && me.group ? me.group : "";
      renderKitLabel();
      const mine = results[1].filter((a) => a.kit_id === KIT);
      setHint(mine.length ? mine[0] : null);
    } catch (e) {
      // The backend may be starting. Try again at the next poll.
    }
  }

  function renderKitLabel() {
    $("s-kit").textContent = groupName ? KIT + " · " + groupName : KIT;
  }

  // ------------------------------------------------------------ WebSocket

  function setLink(on) {
    const el = $("s-link");
    el.textContent = on ? "Live" : "No link";
    el.classList.toggle("s-link-on", on);
    el.classList.toggle("s-link-off", !on);
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(proto + "//" + location.host + "/ws");
    ws.onopen = () => setLink(true);
    ws.onclose = () => { setLink(false); setTimeout(connect, 2000); };
    ws.onerror = () => ws.close();
    ws.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      if (msg.type === "reading") handleReading(msg.data);
      else if (msg.type === "alert" && msg.data.kit_id === KIT) setHint(msg.data);
    };
  }

  async function loadHistory() {
    try {
      const rows = await getJSON("/api/readings?kit_id=" + encodeURIComponent(KIT) + "&limit=300");
      rows.forEach((r) => {
        const key = r.experiment + "|" + r.sensor + "|" + r.unit;
        if (!recent[key]) recent[key] = [];
        recent[key].push(r.value);
        if (recent[key].length > MAX_RECENT) recent[key].shift();
      });
      scheduleRender();
    } catch (e) {
      // No history yet. The graph fills as new readings arrive.
    }
  }

  // ----------------------------------------------------------------- start

  function init() {
    buildHome();
    renderKitLabel();
    showScreen("home");

    $("s-back").addEventListener("click", goHome);
    $("s-start").addEventListener("click", startRun);
    $("s-stop").addEventListener("click", stopRun);
    $("s-reset").addEventListener("click", resetRun);
    $("s-save").addEventListener("click", saveRun);
    document.querySelectorAll(".s-tabs button").forEach((b) => {
      b.addEventListener("click", () => showPane(b.dataset.pane));
    });

    window.addEventListener("resize", renderLive);
    window.addEventListener("viewchange", () => { renderLive(); poll(); });
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", renderLive);
    }

    setInterval(() => {
      if (rec.on) renderTimer();
      renderLive();
    }, 250);
    setInterval(poll, 4000);

    connect();
    loadHistory();
    poll();
  }

  init();
})();
