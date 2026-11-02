const $ = (selector) => document.querySelector(selector);
const money = (value) => value == null ? "—" : new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value);
const percent = (value) => value == null ? "—" : `${value > 0 ? "+" : ""}${Number(value).toFixed(2)}%`;
const number = (value, digits = 2) => value == null ? "—" : new Intl.NumberFormat("pt-BR", { maximumFractionDigits: digits }).format(value);
const escaped = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const errorBox = $("#globalError");
let portfolioData = { positions: [], records: [] };
let watchlistData = { items: [], quotes: [], alerts: [] };
let priceByTicker = {};
let ibovChart;
let allocationChart;

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("Sua sessão expirou.");
  }
  if (!response.ok) {
    let detail = `Falha na solicitação (${response.status}).`;
    try { detail = (await response.json()).detail || detail; } catch { /* resposta sem JSON */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

function flashPrice(element, ticker, current) {
  const previous = priceByTicker[ticker];
  if (previous != null && previous !== current) {
    element.classList.remove("flash-up", "flash-down");
    element.classList.add(current > previous ? "flash-up" : "flash-down");
  }
  priceByTicker[ticker] = current;
}

function quoteMap(quotes) {
  return Object.fromEntries((quotes || []).map((quote) => [quote.ticker, quote]));
}

function createSignalRow(signal) {
  const className = signal.signal === "COMPRA" ? "ok" : signal.signal === "ATENÇÃO" ? "bad" : "neutral";
  const criteria = signal.criteria.map((item) => `${item.passed === true ? "✓" : item.passed === false ? "×" : "—"} ${item.label}: ${item.value == null ? "indisponível" : number(item.value)}`).join(" · ");
  return `<details class="signal-detail"><summary class="signal-row"><strong>${escaped(signal.ticker)}</strong><span>${signal.score}/100 · ${escaped(signal.signal)}</span><span class="tag ${className}">${signal.criteria.filter((item) => item.passed === true).length}/${signal.criteria.length} regras</span></summary><small>${escaped(criteria)}</small><small>Calculado: ${new Date(signal.calculated_at).toLocaleString("pt-BR")}</small></details>`;
}

async function loadDashboard() {
  const [overview, portfolio, watchlist] = await Promise.all([
    api("/overview"), api("/portfolio"), api("/watchlist"),
  ]);
  portfolioData = portfolio;
  watchlistData = watchlist;
  const ibov = overview.quotes?.["^BVSP"];
  const dollar = overview.quotes?.USDBRL;
  $("#ibovValue").innerHTML = ibov ? `${number(ibov.price, 0)} <small>pts</small>` : "— <small>pts</small>";
  $("#ibovChange").textContent = ibov ? percent(ibov.change_percent) : overview.error || "Cotação indisponível";
  $("#ibovChartPrice").textContent = ibov ? number(ibov.price, 0) : "—";
  $("#dollarValue").textContent = dollar ? money(dollar.price) : "R$ —";
  $("#dollarChange").textContent = dollar ? percent(dollar.change_percent) : "Cotação indisponível";
  $("#selicValue").innerHTML = overview.selic_percent == null ? "— <small>% a.a.</small>" : `${number(overview.selic_percent)} <small>% a.a.</small>`;
  $("#selicMeta").textContent = overview.selic_error || "Banco Central";
  $("#marketStatus").className = `market-status ${ibov ? ibov.delayed ? "warning" : "ok" : "error"}`;
  $("#marketStatus").innerHTML = `<i></i>${ibov ? (ibov.delay_minutes > 0 ? `Cotação com atraso de ${ibov.delay_minutes} min` : ibov.delayed ? "Cotação não em tempo real" : "Fonte conectada") : "Dados indisponíveis"}`;
  $("#quoteTimestamp").textContent = ibov?.fetched_at ? `Atualizado: ${new Date(ibov.fetched_at).toLocaleString("pt-BR")}${ibov.delay_minutes > 0 ? ` · atraso de ${ibov.delay_minutes} min` : ibov.delayed ? " · fonte não informa tempo real" : ""}` : "Sem cotação atual";
  $("#marketNews").textContent = overview.focus?.length
    ? `Boletim Focus: ${overview.focus.map((item) => `${item.Indicador} ${item.Indicador === "Câmbio" ? money(item.Mediana) : `${number(item.Mediana)}%`} (${item.DataReferencia} · ${item.Data})`).join(" · ")}${overview.focus_error ? ` · ${overview.focus_error}` : ""}`
    : `Selic: ${overview.selic_percent == null ? (overview.selic_error || "indisponível") : `${number(overview.selic_percent)}%`}. ${overview.focus_error || "Boletim Focus indisponível."}`;
  const prices = quoteMap(portfolio.positions.map((item) => item.quote).filter(Boolean));
  renderPortfolio(portfolio, prices);
  renderAllocation(portfolio);
  await loadMovers();
  const tickers = [...new Set([...portfolio.positions.map((item) => item.ticker), ...watchlist.items])].slice(0, 4);
  const signalEntries = await Promise.all(tickers.map(async (ticker) => {
    try { return await api(`/signals/${encodeURIComponent(ticker)}`); }
    catch (error) { return { ticker, error: error.message }; }
  }));
  $("#dashboardSignals").innerHTML = signalEntries.length
    ? signalEntries.map((signal) => signal.error
      ? `<div class="signal-row"><strong>${escaped(signal.ticker)}</strong><span>${escaped(signal.error)}</span></div>`
      : createSignalRow(signal)).join("")
    : '<p class="empty-row">Cadastre ativos na carteira ou watchlist para analisar.</p>';
  renderWatchlist(watchlist);
  renderAlerts(watchlist.alerts);
}

function renderPortfolio(data, quotes = {}) {
  portfolioData = data;
  const combined = data.positions || [];
  const records = data.records || [];
  const total = combined.reduce((sum, item) => sum + (item.pnl?.current_value || 0), 0);
  const pnl = combined.reduce((sum, item) => sum + (item.pnl?.result || 0), 0);
  const invested = combined.reduce((sum, item) => sum + (item.pnl?.invested || 0), 0);
  $("#totalValue").textContent = data.total_value == null ? "R$ —" : money(total);
  $("#totalPositions").textContent = combined.length ? `${combined.length} ativo(s) na carteira` : "Sem posições";
  $("#totalPnl").textContent = data.total_value == null ? "R$ —" : money(pnl);
  $("#totalPnl").className = `metric ${pnl >= 0 ? "positive" : "negative"}`;
  $("#totalPnlPercent").textContent = invested ? percent(pnl / invested * 100) : (data.error || "Sem posições");
  const dayResult = data.day_result;
  $("#dayPnl").textContent = dayResult == null ? "R$ —" : money(dayResult);
  $("#dayPnl").className = `metric ${dayResult == null || dayResult >= 0 ? "positive" : "negative"}`;
  $("#dayPnlPercent").textContent = dayResult == null ? "Cotação indisponível" : percent(data.day_result_percent);
  $("#portfolioTotal").textContent = data.total_value == null ? "P&L: cotação indisponível" : `P&L: ${money(pnl)} (${percent(invested ? pnl / invested * 100 : 0)})`;
  const delayedQuote = Object.values(quotes).find((quote) => quote.delay_minutes != null);
  $("#portfolioQuoteStatus").textContent = data.error || (delayedQuote ? `Cotação com atraso de ${delayedQuote.delay_minutes} min, conforme horário informado pela fonte.` : Object.values(quotes).some((quote) => quote.delayed) ? "Fonte informa cotação não em tempo real; atraso exato indisponível." : "Cotações fornecidas pela fonte configurada.");
  if (!records.length) {
    $("#portfolioRows").innerHTML = '<tr><td colspan="10" class="empty-row">Sua carteira está vazia. Adicione uma posição para começar.</td></tr>';
    return;
  }
  const grouped = Object.fromEntries(combined.map((item) => [item.ticker, item]));
  $("#portfolioRows").innerHTML = records.map((row) => {
    const quote = quotes[row.ticker];
    const pnlRow = quote ? row.quantity * (quote.price - row.average_price) - row.fees : null;
    const isYesterday = row.purchased_at === data.previous_trading_day;
    const display = quote?.price == null ? "—" : money(quote.price);
    const todayClass = (quote?.change_percent || 0) >= 0 ? "positive" : "negative";
    const group = grouped[row.ticker];
    const sparkValues = quote?.sparkline?.length > 1
      ? quote.sparkline.slice(-16)
      : quote?.previous_close != null ? [quote.previous_close, quote.price] : [];
    const spark = sparkValues.length > 1
      ? `<svg class="sparkline" viewBox="0 0 64 18" aria-label="Variação intradiária"><polyline points="${sparkValues.map((value, index, values) => `${index * 64 / (values.length - 1)},${17 - (value - Math.min(...values)) / (Math.max(...values) - Math.min(...values) || 1) * 15}`).join(" ")}" fill="none" stroke="${(quote.change_percent || 0) >= 0 ? "#30d69a" : "#ff667d"}" stroke-width="1.5"/></svg>`
      : "";
    return `<tr><td>${escaped(row.ticker)} ${isYesterday ? '<span class="tag neutral">Compra ontem</span>' : ""}<small>${quote?.data_warning ? escaped(quote.data_warning) : quote?.delayed ? "cotação não em tempo real" : ""}</small>${spark}</td><td>${number(row.quantity, 4)}</td><td>${money(row.average_price)}</td><td class="quote-cell" data-ticker="${escaped(row.ticker)}">${display}</td><td class="${todayClass}">${quote ? percent(quote.change_percent) : "—"}</td><td class="${pnlRow == null ? "" : pnlRow >= 0 ? "positive" : "negative"}">${pnlRow == null ? "—" : money(pnlRow)}${isYesterday && quote ? `<small>vs. compra: ${percent((quote.price / row.average_price - 1) * 100)}</small>` : ""}</td><td>${money(row.quantity * row.average_price + row.fees)}</td><td>${group?.pnl ? money(row.quantity * (quote?.price || 0)) : "—"}</td><td>${group?.weight_percent == null ? "—" : percent(group.weight_percent)}</td><td><button class="table-action edit-position" data-id="${row.id}" title="Editar">✎</button><button class="table-action delete-position" data-id="${row.id}" title="Remover">×</button></td></tr>`;
  }).join("");
  document.querySelectorAll(".quote-cell").forEach((cell) => {
    const quote = quotes[cell.dataset.ticker];
    if (quote) flashPrice(cell, cell.dataset.ticker, quote.price);
  });
  document.querySelectorAll(".delete-position").forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("Remover esta posição da carteira?")) return;
    try { await api(`/portfolio/${button.dataset.id}`, { method: "DELETE" }); await loadDashboard(); } catch (error) { showError(error.message); }
  }));
  document.querySelectorAll(".edit-position").forEach((button) => button.addEventListener("click", () => {
    const row = records.find((item) => String(item.id) === button.dataset.id);
    if (!row) return;
    const form = $("#positionForm");
    form.hidden = false;
    form.dataset.editId = row.id;
    for (const [key, value] of Object.entries(row)) if (form.elements[key]) form.elements[key].value = value;
    $("#addPositionToggle").textContent = "Cancelar edição";
  }));
}

function renderAllocation(data) {
  if (!window.Chart) return;
  const items = data.positions.filter((item) => item.pnl);
  const empty = $("#allocationEmpty");
  if (!items.length) {
    empty.hidden = false;
    allocationChart?.destroy();
    return;
  }
  empty.hidden = true;
  allocationChart?.destroy();
  allocationChart = new Chart($("#allocationChart"), {
    type: "doughnut",
    data: { labels: items.map((item) => item.ticker), datasets: [{ data: items.map((item) => item.pnl.current_value), backgroundColor: ["#4bd7e8", "#9b8cff", "#30d69a", "#f0bb62", "#ff667d", "#6394ff"], borderWidth: 0 }] },
    options: { cutout: "72%", plugins: { legend: { position: "bottom", labels: { color: "#98a7bb", boxWidth: 8, font: { size: 10 } } } } },
  });
}

async function loadMovers() {
  try {
    const data = await api("/market/movers");
    $("#moversScope").textContent = data.scope_notice || "";
    const format = (item) => `<div class="mover-line"><strong>${escaped(item.ticker)}${item.data_warning ? `<small>${escaped(item.data_warning)}</small>` : ""}</strong><span class="${item.change_percent >= 0 ? "positive" : "negative"}">${percent(item.change_percent)}</span></div>`;
    $("#topGainers").innerHTML = data.gainers.length ? data.gainers.map(format).join("") : '<p class="empty-row">Sem destaques disponíveis.</p>';
    $("#topLosers").innerHTML = data.losers.length ? data.losers.map(format).join("") : '<p class="empty-row">Sem destaques disponíveis.</p>';
  } catch (error) {
    $("#topGainers").textContent = error.message;
    $("#topLosers").textContent = error.message;
  }
}

function renderWatchlist(data) {
  watchlistData = data;
  const quotes = quoteMap(data.quotes);
  if (!data.items.length) {
    $("#watchlistRows").innerHTML = `<tr><td colspan="8" class="empty-row">${escaped(data.error || "Watchlist vazia. Adicione um ticker.")}</td></tr>`;
  } else {
    $("#watchlistRows").innerHTML = (data.error ? `<tr><td colspan="8" class="empty-row">${escaped(data.error)}</td></tr>` : "") + data.items.map((ticker) => {
      const quote = quotes[ticker];
      return `<tr><td>${escaped(ticker)}${quote?.data_warning ? `<small>${escaped(quote.data_warning)}</small>` : ""}</td><td class="quote-cell" data-ticker="${escaped(ticker)}">${quote ? money(quote.price) : "—"}</td><td class="${(quote?.change_percent || 0) >= 0 ? "positive" : "negative"}">${quote ? percent(quote.change_percent) : "—"}</td><td>${quote ? `${money(quote.fifty_two_week_low)} / ${money(quote.fifty_two_week_high)}` : "—"}</td><td>${quote?.volume == null ? "—" : number(quote.volume, 0)}</td><td>${number(quote?.pe_ratio)}</td><td>${quote?.dividend_yield == null ? "—" : percent(quote.dividend_yield * 100)}</td><td><button class="table-action remove-watch" data-ticker="${escaped(ticker)}" title="Remover">×</button></td></tr>`;
    }).join("");
  }
  document.querySelectorAll(".remove-watch").forEach((button) => button.addEventListener("click", async () => {
    try { await api(`/watchlist/${encodeURIComponent(button.dataset.ticker)}`, { method: "DELETE" }); await loadDashboard(); } catch (error) { showError(error.message); }
  }));
}

async function renderAlerts(alerts) {
  $("#alertList").innerHTML = alerts?.length ? alerts.map((alert) => `<div class="data-row"><div><strong>${escaped(alert.ticker)}</strong><small>${alert.kind === "above" ? "Acima de" : alert.kind === "below" ? "Abaixo de" : "Queda diária maior que"} ${alert.kind === "drop_percent" ? `${number(alert.threshold)}%` : money(alert.threshold)}</small></div><button class="table-action delete-alert" data-id="${alert.id}">Remover</button></div>`).join("") : '<p class="empty-row">Nenhum alerta configurado.</p>';
  try {
    const fired = await api("/alerts/fired");
    $("#firedAlerts").innerHTML = fired.alerts.length
      ? fired.alerts.map((alert) => `<div class="data-row alert-fired"><div><strong>Alerta disparado · ${escaped(alert.ticker)}</strong><small>Preço atual: ${money(alert.price)} · Regra: ${escaped(alert.kind)}</small></div><span class="tag bad">Disparado</span></div>`).join("")
      : `<p class="empty-row">${escaped(fired.error || "Nenhum alerta disparado.")}</p>`;
  } catch (error) {
    $("#firedAlerts").textContent = error.message;
  }
  document.querySelectorAll(".delete-alert").forEach((button) => button.addEventListener("click", async () => {
    try { await api(`/alerts/${button.dataset.id}`, { method: "DELETE" }); await loadDashboard(); } catch (error) { showError(error.message); }
  }));
}

async function loadOpportunities() {
  $("#opportunityList").innerHTML = '<p class="empty-row">Consultando dados de mercado…</p>';
  try {
    const threshold = encodeURIComponent($("#dropThreshold").value || "3");
    const minimumVolume = encodeURIComponent($("#minimumVolume").value || "0");
    const sector = encodeURIComponent($("#sectorFilter").value || "");
    const timeframe = encodeURIComponent($("#opportunityTimeframe").value);
    const result = await api(`/opportunities?threshold=${threshold}&minimum_volume=${minimumVolume}&sector=${sector}&timeframe=${timeframe}`);
    $("#opportunityScope").textContent = result.scope_notice || "";
    const draw = (item) => `<div class="data-row"><div><strong>${escaped(item.ticker)} ${item.in_portfolio ? '<span class="tag neutral">Na carteira</span>' : ""}</strong><small>${money(item.price)} · ${percent(item.change_percent)} · ${escaped(item.sector || "Setor indisponível")}</small>${item.data_warning ? `<small>${escaped(item.data_warning)}</small>` : ""}${item.in_portfolio ? `<small>Preço médio: ${money(item.average_price)} · ${item.price < item.average_price ? "abaixo" : "acima"} do preço médio</small>` : ""}<small>Liquidez média: ${item.average_volume == null ? "indisponível" : number(item.average_volume, 0)} ações/dia</small><small>${item.fundamentals.map((fact) => `${escaped(fact.name)}: ${fact.value == null ? "indisponível" : number(fact.value)} ${fact.passed === true ? "✓" : fact.passed === false ? "×" : "—"}`).join(" · ")}</small></div><span class="tag ${item.status === "Favorável" ? "ok" : item.status === "Dados insuficientes" ? "neutral" : "bad"}">${escaped(item.status)}</span></div>`;
    const down = result.items.filter((item) => !item.in_portfolio && item.change_percent <= -Number(threshold));
    const owned = result.items.filter((item) => item.in_portfolio);
    $("#opportunityList").innerHTML = down.length ? down.map(draw).join("") : `<p class="empty-row">${escaped(result.error || "Nenhuma queda atende ao limiar informado ou dados indisponíveis.")}</p>`;
    $("#ownedOpportunityList").innerHTML = owned.length ? owned.map(draw).join("") : '<p class="empty-row">Nenhum ativo da carteira entre os dados retornados pela fonte.</p>';
  } catch (error) { $("#opportunityList").textContent = error.message; }
}

async function loadReports() {
  try {
    const entries = await api("/reports");
    $("#reportList").innerHTML = entries.length ? entries.map((report) => `<article class="report-card"><h3>${escaped(report.title)} · ${new Date(report.created_at).toLocaleString("pt-BR")}</h3><pre>${escaped(report.content)}</pre></article>`).join("") : '<p class="empty-row">Nenhum relatório salvo.</p>';
  } catch (error) { $("#reportList").textContent = error.message; }
}

const pageDetails = {
  dashboard: ["MERCADO BRASILEIRO", "Visão geral"],
  carteira: ["ACOMPANHAMENTO DE POSIÇÕES", "Minha Carteira"],
  oportunidades: ["ANÁLISE DE MERCADO", "Oportunidades"],
  watchlist: ["ACOMPANHAMENTO", "Watchlist"],
  relatorios: ["RESUMO AUTOMÁTICO", "Relatórios"],
  configuracoes: ["PREFERÊNCIAS DA CONTA", "Configurações"],
};

function navigate(page) {
  if (!pageDetails[page]) return;
  document.querySelectorAll(".page-section").forEach((section) => section.classList.toggle("active", section.id === `page-${page}`));
  document.querySelectorAll(".nav-item[data-page]").forEach((item) => item.classList.toggle("active", item.dataset.page === page));
  $("#pageEyebrow").textContent = pageDetails[page][0];
  $("#pageTitle").textContent = pageDetails[page][1];
  history.replaceState(null, "", `#${page}`);
  if (page === "oportunidades") loadOpportunities();
  if (page === "relatorios") loadReports();
}

document.querySelectorAll("[data-page]").forEach((link) => link.addEventListener("click", (event) => {
  event.preventDefault();
  navigate(link.dataset.page);
}));

$("#sidebarToggle").addEventListener("click", () => {
  const sidebar = $("#sidebar");
  if (window.matchMedia("(max-width: 760px)").matches) {
    sidebar.classList.toggle("expanded");
  } else {
    sidebar.classList.toggle("collapsed");
    document.querySelector(".main-content").classList.toggle("sidebar-collapsed");
  }
});

$("#refreshButton").addEventListener("click", async () => {
  showError("");
  try { await loadDashboard(); } catch (error) { showError(error.message); }
});

$("#addPositionToggle").addEventListener("click", () => {
  const form = $("#positionForm");
  if (!form.hidden && form.dataset.editId) {
    delete form.dataset.editId;
    form.reset();
    $("#addPositionToggle").textContent = "+ Adicionar ativo";
    form.hidden = true;
    return;
  }
  form.hidden = !form.hidden;
  if (!form.hidden && !form.dataset.editId) form.elements.purchased_at.value = new Date().toISOString().slice(0, 10);
});

$("#positionForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form));
  for (const key of ["quantity", "average_price", "fees"]) payload[key] = Number(payload[key] || 0);
  const editId = form.dataset.editId;
  try {
    await api(editId ? `/portfolio/${editId}` : "/portfolio", { method: editId ? "PUT" : "POST", body: JSON.stringify(payload) });
    form.reset();
    form.hidden = true;
    delete form.dataset.editId;
    $("#addPositionToggle").textContent = "+ Adicionar ativo";
    await loadDashboard();
  } catch (error) { showError(error.message); }
});

$("#watchlistForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  try { await api("/watchlist", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(form))) }); form.reset(); await loadDashboard(); }
  catch (error) { showError(error.message); }
});

$("#alertForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form));
  payload.threshold = Number(payload.threshold);
  try { await api("/alerts", { method: "POST", body: JSON.stringify(payload) }); form.reset(); await loadDashboard(); }
  catch (error) { showError(error.message); }
});

$("#loadOpportunities").addEventListener("click", loadOpportunities);

$("#backtestForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const ticker = new FormData(event.currentTarget).get("ticker");
  const target = $("#backtestResult");
  target.textContent = "Executando…";
  try {
    const result = await api(`/backtest/${encodeURIComponent(ticker)}`);
    target.textContent = result.error || `${result.scope} ${result.trades} operações · ${result.hits} acertos · ${result.errors} erros · taxa de acerto ${number(result.hit_rate)}% · estratégia ${percent(result.strategy_return_percent)} · Ibovespa ${percent(result.benchmark_return_percent)}`;
  } catch (error) { target.textContent = error.message; }
});

$("#generateReport").addEventListener("click", async () => {
  const button = $("#generateReport");
  button.disabled = true;
  button.textContent = "Gerando…";
  try {
    const report = await api("/reports/generate", { method: "POST" });
    $("#reportList").insertAdjacentHTML("afterbegin", `<article class="report-card"><h3>${escaped(report.title)}</h3><pre>${escaped(report.content)}</pre>${report.errors.map((error) => `<small class="negative">${escaped(error)}</small>`).join("")}</article>`);
  } catch (error) { showError(error.message); }
  finally { button.disabled = false; button.textContent = "Gerar agora"; }
});

$("#passwordForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const feedback = $("#passwordMessage");
  feedback.className = "form-feedback";
  try {
    await api("/settings/password", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(form))) });
    feedback.textContent = "Senha alterada com sucesso.";
    form.reset();
    document.querySelector(".notice-warn")?.remove();
  } catch (error) { feedback.classList.add("error"); feedback.textContent = error.message; }
});

document.querySelectorAll("[data-period]").forEach((button) => button.addEventListener("click", async () => {
  document.querySelectorAll("[data-period]").forEach((item) => item.classList.toggle("selected", item === button));
  try {
    const data = await api(`/history/%5EBVSP?period=${button.dataset.period}`);
    const points = data.history;
    $("#chartMessage").hidden = points.length > 0;
    if (!window.Chart) return;
    ibovChart?.destroy();
    ibovChart = new Chart($("#ibovChart"), {
      type: "line",
        data: { labels: points.map((point) => new Date(typeof point.date === "number" ? point.date * 1000 : point.date).toLocaleDateString("pt-BR")), datasets: [{ data: points.map((point) => point.close), borderColor: "#4bd7e8", backgroundColor: "rgba(75,215,232,.08)", fill: true, tension: .3, pointRadius: 0, borderWidth: 2 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { display: false }, y: { grid: { color: "rgba(149,173,205,.08)" }, ticks: { color: "#8290a4", font: { size: 9 } } } } },
    });
  } catch (error) { $("#chartMessage").hidden = false; $("#chartMessage").textContent = error.message; }
}));

function connectStream() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/api/stream`);
  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.error) {
      $("#portfolioQuoteStatus").textContent = payload.error;
      return;
    }
    const updates = quoteMap(payload.quotes);
    portfolioData.positions = portfolioData.positions.map((position) => updates[position.ticker] ? { ...position, quote: updates[position.ticker], pnl: {
      invested: position.quantity * position.average_price + position.fees,
      current_value: position.quantity * updates[position.ticker].price,
      result: position.quantity * (updates[position.ticker].price - position.average_price) - position.fees,
      result_percent: position.average_price ? ((updates[position.ticker].price / position.average_price) - 1) * 100 : 0,
    } } : position);
    const totals = portfolioData.positions.reduce((sum, position) => sum + (position.pnl?.current_value || 0), 0);
    portfolioData.total_value = totals;
    portfolioData.day_result = portfolioData.positions.reduce((sum, position) => {
      const quote = updates[position.ticker];
      return sum + (quote?.previous_close ? position.quantity * (quote.price - quote.previous_close) : 0);
    }, 0);
    const previousValue = totals - portfolioData.day_result;
    portfolioData.day_result_percent = previousValue ? portfolioData.day_result / previousValue * 100 : 0;
    portfolioData.positions.forEach((position) => { if (position.pnl && totals) position.weight_percent = position.pnl.current_value / totals * 100; });
    renderPortfolio(portfolioData, quoteMap(portfolioData.positions.map((position) => position.quote).filter(Boolean)));
    renderAllocation(portfolioData);
    document.querySelectorAll(".quote-cell").forEach((cell) => {
      const quote = updates[cell.dataset.ticker];
      if (quote) {
        cell.textContent = money(quote.price);
        flashPrice(cell, quote.ticker, quote.price);
      }
    });
  });
  socket.addEventListener("close", (event) => {
    if (event.code === 1008) {
      window.location.assign("/login");
      return;
    }
    window.setTimeout(connectStream, 5000);
  });
}

async function start() {
  const now = new Date();
  $("#pageEyebrow").textContent = now.toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "long" }).toUpperCase();
  const initialPage = location.hash.slice(1);
  if (pageDetails[initialPage]) navigate(initialPage);
  try { await loadDashboard(); } catch (error) { showError(error.message); }
  connectStream();
  const historyButton = document.querySelector('[data-period="1y"]');
  if (historyButton) historyButton.click();
}

start();
