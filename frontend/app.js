(() => {
  const byId = (id) => document.getElementById(id);
  const number = (value) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  const percent = (value) => value == null || !Number.isFinite(Number(value)) ? "—" : `${(Number(value) * 100).toFixed(2)}%`;
  let result = null;
  let greek = "delta";

  function drawChart(id, rows, key, colour, { zero = false, overlay } = {}) {
    const element = byId(id);
    const context = element?.getContext?.("2d");
    const values = (rows || []).map((row) => Number(row[key])).filter(Number.isFinite);
    if (!context || !values.length) return;
    const width = Math.max(element.clientWidth || 0, 260);
    const height = element.getAttribute("height") ? Number(element.getAttribute("height")) : 210;
    const scale = window.devicePixelRatio || 1;
    element.width = width * scale;
    element.height = height * scale;
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.clearRect(0, 0, width, height);
    context.fillStyle = "#0b1728";
    context.fillRect(0, 0, width, height);
    const all = overlay ? values.concat(overlay.map((row) => Number(row[key])).filter(Number.isFinite)) : values;
    const low = Math.min(...all, zero ? 0 : Infinity);
    const high = Math.max(...all, zero ? 0 : -Infinity);
    const range = Math.max(high - low, 0.0001);
    const pad = 24;
    const y = (value) => pad + ((high - value) / range) * (height - pad * 2);
    context.strokeStyle = "#263950";
    context.lineWidth = 1;
    for (let i = 0; i < 4; i += 1) {
      const lineY = pad + i * (height - pad * 2) / 3;
      context.beginPath(); context.moveTo(pad, lineY); context.lineTo(width - pad, lineY); context.stroke();
    }
    if (zero && low < 0 && high > 0) {
      context.strokeStyle = "#7b8da7";
      context.beginPath(); context.moveTo(pad, y(0)); context.lineTo(width - pad, y(0)); context.stroke();
    }
    const line = (points, stroke) => {
      context.strokeStyle = stroke; context.lineWidth = 2.5; context.beginPath();
      points.forEach((row, index) => {
        const x = pad + index * (width - pad * 2) / Math.max(points.length - 1, 1);
        index ? context.lineTo(x, y(Number(row[key]))) : context.moveTo(x, y(Number(row[key])));
      });
      context.stroke();
    };
    line(rows, colour);
    if (overlay?.length) line(overlay, "#47d7a1");
  }

  function volatilityRows(data) {
    const iv = data.impliedVol.marketIv;
    const rv = data.volatility.realizedVol || data.impliedVol.modelIv;
    return Array.from({ length: 24 }, (_, index) => ({
      iv: iv * (0.94 + Math.sin(index / 3) * 0.04),
      rv: rv * (0.92 + Math.cos(index / 4) * 0.06),
    }));
  }

  function updateScenario() {
    if (!result) return;
    const spotMove = Number(byId("spot-shock").value) / 100;
    const ivMove = Number(byId("iv-shock").value) / 100;
    const spot = Number(byId("spot").value);
    const g = result.greeks;
    const pnl = g.delta * spot * spotMove + 0.5 * g.gamma * (spot * spotMove) ** 2 + g.vega * ivMove;
    byId("spot-shock-value").textContent = `${(spotMove * 100).toFixed(0)}%`;
    byId("iv-shock-value").textContent = `${(ivMove * 100).toFixed(0)}%`;
    const output = byId("scenario-pnl");
    output.textContent = `${pnl >= 0 ? "+" : ""}₹ ${number(pnl)}`;
    output.className = pnl >= 0 ? "gain" : "loss";
    byId("vega-limit").textContent = number(g.vega);
  }

  function render(data) {
    result = data;
    byId("m-price").textContent = `₹ ${number(data.modelPrice)}`;
    byId("m-iv").textContent = percent(data.impliedVol.marketIv);
    byId("m-rv").textContent = percent(data.volatility.realizedVol);
    byId("m-latency").textContent = `${number(data.requestMs)} ms`;
    byId("m-signal").textContent = `${data.impliedVol.signal} IV signal`;
    byId("request-time").textContent = `${number(data.requestMs)} ms`;
    byId("vol-regime").textContent = data.volatility.regime;
    byId("vol-regime").className = `pill ${data.volatility.regime}`;
    byId("vol-info").innerHTML = [["Garman–Klass", percent(data.volatility.gkVol)], ["30d forecast", percent(data.volatility.forward30d)], ["IV / RV", `${number(data.volatility.ivVsRealized)}×`]].map(([label, value]) => `<span>${label} <b>${value}</b></span>`).join("");
    byId("greek-cards").innerHTML = Object.entries(data.greeks).map(([name, value]) => `<article><small>${name}</small><strong>${number(value)}</strong><span>${name === "theta" ? "daily decay" : name === "vega" ? "per 1% vol" : "current exposure"}</span></article>`).join("");
    const title = greek[0].toUpperCase() + greek.slice(1);
    byId("surface-title").textContent = `${title} surface`;
    byId("surface-metric").textContent = greek === "delta" ? "Δ" : title;
    byId("greek-chart-caption").textContent = `${title} response across strike levels`;
    byId("surface").querySelector("tbody").innerHTML = data.surface.map((row) => `<tr><td>${row.dte}d</td><td>${number(row.strike)}</td><td style="--v:${Math.min(1, Math.abs(row[greek]))}">${number(row[greek])}</td><td>${number(row.gamma)}</td><td>${number(row.vega)}</td></tr>`).join("");
    const strategy = byId("strategy");
    byId("strategy-title").textContent = `${strategy.options[strategy.selectedIndex]?.text || "Strategy"} payoff`;
    const loss = Math.abs(Math.min(...data.payoff.map((point) => point.pnl)));
    byId("payoff-metrics").innerHTML = `<span>Max loss <b>₹ ${number(loss)}</b></span><span>Break-even <b>₹ ${number(Number(byId("strike").value) + data.modelPrice)}</b></span><span>Max gain <b>${data.strategy === "long_call" ? "Unlimited" : "Varies"}</b></span>`;
    const v = volatilityRows(data);
    drawChart("payoff-chart", data.payoff, "pnl", "#73a7ff", { zero: true });
    drawChart("greek-chart", data.surface.filter((row) => row.dte === 30).map((row) => ({ value: row[greek] })), "value", "#b28cff", { zero: greek === "theta" });
    drawChart("vol-chart", v, "iv", "#b28cff", { overlay: v.map(({ rv }) => ({ iv: rv })) });
    updateScenario();
  }

  function requestData() {
    return { spot: Number(byId("spot").value), strike: Number(byId("strike").value), vol: Number(byId("vol").value), rate: Number(byId("rate").value), maturity: Number(byId("maturity").value), option_type: byId("type").value, strategy: byId("strategy").value, series: [] };
  }

  async function runAnalysis(event) {
    const controls = [byId("run-btn"), byId("run-top"), byId("run-hero")];
    controls.forEach((button) => { button.disabled = true; });
    byId("request-time").textContent = "Calculating…";
    try {
      const response = await fetch("/api/analytics", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(requestData()) });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Analytics request failed");
      render(payload);
    } catch (error) {
      byId("request-time").textContent = `Analysis failed: ${error.message}`;
      byId("model-note").textContent = "Unable to load analytics. Check that the dashboard is opened through the FastAPI server.";
    } finally {
      controls.forEach((button) => { button.disabled = false; });
    }
  }

  ["run-btn", "run-top", "run-hero", "refresh-btn"].forEach((id) => { byId(id).onclick = runAnalysis; });
  byId("strategy").onchange = runAnalysis;
  byId("spot-shock").oninput = updateScenario;
  byId("iv-shock").oninput = updateScenario;
  document.querySelectorAll(".segmented button").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll(".segmented button").forEach((item) => item.classList.toggle("active", item === button));
    greek = button.textContent.trim().toLowerCase();
    if (result) render(result);
  }));
  window.addEventListener("resize", () => result && render(result));
  runAnalysis();
})();
