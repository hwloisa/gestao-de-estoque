/* ==========================================================================
   Controle de estoque · Creche Carolina Guilhermina Schnoor Bohianza
   JavaScript puro (sem dependências)
   ========================================================================== */

(() => {
  "use strict";

  const state = {
    items: [],
    categories: [],
    dashboard: null,
    units: [],
    view: "painel",
    movementSector: "todos",
    movementSearch: "",
    reportSector: "todos",
    filters: { search: "", sector: "", onlyLow: false },
    loaded: false,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const numberFormatter = new Intl.NumberFormat("pt-BR", {
    maximumFractionDigits: 3,
  });

  function formatQty(value) {
    const number = Number(value);
    return numberFormatter.format(Number.isFinite(number) ? number : 0);
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function parseDate(value) {
    if (!value) return new Date();
    return new Date(String(value).replace(" ", "T"));
  }

  function timeAgo(value) {
    const date = parseDate(value);
    const seconds = (Date.now() - date.getTime()) / 1000;
    if (!Number.isFinite(seconds) || seconds < 60) return "agora";
    if (seconds < 3600) return `há ${Math.floor(seconds / 60)} min`;
    if (seconds < 86400) return `há ${Math.floor(seconds / 3600)} h`;
    if (seconds < 86400 * 7) return `há ${Math.floor(seconds / 86400)} d`;
    return date.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  }

  function plural(count, singular, pluralForm) {
    return `${count} ${count === 1 ? singular : pluralForm}`;
  }

  // -------------------------------------------------------------------------
  // API
  // -------------------------------------------------------------------------

  const csrfToken = $('meta[name="csrf-token"]')?.getAttribute("content") || "";

  let redirectingToLogin = false;

  function redirectToLogin(reason = "expirou=1") {
    if (redirectingToLogin) return;
    redirectingToLogin = true;
    window.location.href = `/login?${reason}`;
  }

  async function api(path, { method = "GET", body, skipAuthRedirect = false } = {}) {
    const httpMethod = String(method).toUpperCase();
    const options = { method: httpMethod, headers: {} };

    if (httpMethod !== "GET" && csrfToken) {
      options.headers["X-CSRF-Token"] = csrfToken;
    }

    if (body instanceof FormData) {
      options.body = body;
    } else if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(path, options);
    } catch {
      throw new Error("Não foi possível conectar ao servidor. Verifique se o Flask está em execução.");
    }

    let data = null;
    if ((response.headers.get("content-type") || "").includes("application/json")) {
      data = await response.json().catch(() => null);
    }

    if (!response.ok) {
      if (response.status === 401 && !skipAuthRedirect) {
        redirectToLogin("expirou=1");
        throw new Error("Sua sessão expirou. Entre novamente para continuar.");
      }
      if (response.status === 403 && data?.code === "csrf") {
        throw new Error("Sessão inválida. Recarregue a página e tente novamente.");
      }
      throw new Error(data?.error || `Erro ${response.status} ao processar a solicitação.`);
    }
    return data;
  }

  async function downloadFile(path, fallbackName) {
    let response;
    try {
      response = await fetch(path);
    } catch {
      throw new Error("Não foi possível conectar ao servidor. Verifique se o Flask está em execução.");
    }

    if (!response.ok) {
      if (response.status === 401) {
        redirectToLogin("expirou=1");
        throw new Error("Sua sessão expirou. Entre novamente para continuar.");
      }
      let message = `Erro ${response.status} ao gerar o arquivo.`;
      if ((response.headers.get("content-type") || "").includes("application/json")) {
        const data = await response.json().catch(() => null);
        if (data?.error) message = data.error;
      }
      throw new Error(message);
    }

    const disposition = response.headers.get("content-disposition") || "";
    const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
    const filename = match ? decodeURIComponent(match[1]) : fallbackName;

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function logout() {
    try {
      await api("/api/logout", { method: "POST", skipAuthRedirect: true });
    } catch {
      // Segue para o login mesmo que o servidor não responda.
    }
    window.location.href = "/login?saiu=1";
  }

  // -------------------------------------------------------------------------
  // Toasts
  // -------------------------------------------------------------------------

  function toast(message, tone = "success") {
    const stack = $("#toast-stack");
    if (!stack) return;

    const icons = {
      success: "i-check",
      error: "i-alert",
      info: "i-info",
    };

    const element = document.createElement("div");
    element.className = `toast ${tone}`;
    element.innerHTML = `
      <span class="toast-icon"><svg class="icon"><use href="#${icons[tone] || icons.info}"/></svg></span>
      <div class="toast-body"><p>${escapeHtml(message)}</p></div>
      <button type="button" class="toast-close" aria-label="Fechar aviso">
        <svg class="icon"><use href="#i-x"/></svg>
      </button>
    `;

    const remove = () => {
      if (element.classList.contains("is-leaving")) return;
      element.classList.add("is-leaving");
      window.setTimeout(() => element.remove(), 220);
    };

    element.querySelector(".toast-close").addEventListener("click", remove);
    stack.appendChild(element);
    window.setTimeout(remove, tone === "error" ? 6000 : 4200);
  }

  // -------------------------------------------------------------------------
  // Carregamento e navegação
  // -------------------------------------------------------------------------

  async function loadData() {
    try {
      const data = await api("/api/bootstrap");
      state.items = data.items || [];
      state.categories = data.categories || [];
      state.dashboard = data.dashboard || null;
      state.units = data.units || [];
      state.loaded = true;
      renderAll();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function setView(view, { updateHash = true, scroll = true } = {}) {
    const allowed = ["painel", "movimentacoes", "itens", "relatorios"];
    if (!allowed.includes(view)) view = "painel";
    state.view = view;

    $$(".view").forEach((element) => {
      element.classList.toggle("is-active", element.id === `view-${view}`);
    });
    $$(".nav-link").forEach((element) => {
      element.classList.toggle("is-active", element.dataset.view === view);
    });

    closeSidebar();

    if (updateHash && window.location.hash !== `#${view}`) {
      window.history.replaceState(null, "", `#${view}`);
    }

    if (view === "painel") renderDashboard();
    if (view === "movimentacoes") renderMovementGrid();
    if (view === "itens") renderItems();
    if (view === "relatorios") renderReports();

    if (scroll) window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function openSidebar() {
    $("#sidebar")?.classList.add("is-open");
    const overlay = $("#nav-overlay");
    if (overlay) overlay.hidden = false;
    $("#menu-btn")?.setAttribute("aria-expanded", "true");
    document.body.style.overflow = "hidden";
  }

  function closeSidebar() {
    $("#sidebar")?.classList.remove("is-open");
    const overlay = $("#nav-overlay");
    if (overlay) overlay.hidden = true;
    $("#menu-btn")?.setAttribute("aria-expanded", "false");
    document.body.style.overflow = "";
  }

  function renderAll() {
    renderStats();
    renderChart();
    renderMonth();
    renderAlerts();
    renderHistory();
    renderSidebarAlert();
    renderMovementGrid();
    renderItems();
    renderReports();
  }

  // -------------------------------------------------------------------------
  // Painel
  // -------------------------------------------------------------------------

  function renderDashboard() {
    renderStats();
    renderChart();
    renderMonth();
    renderAlerts();
    renderHistory();
    renderSidebarAlert();
  }

  function renderStats() {
    const dashboard = state.dashboard;
    if (!dashboard) return;
    const totals = dashboard.totals;

    $("#stat-items").textContent = formatQty(totals.items);
    $("#stat-items-foot").textContent = `em ${plural(totals.categories, "categoria", "categorias")}`;
    $("#stat-stock").textContent = formatQty(totals.stock);
    $("#stat-out").textContent = formatQty(totals.out_of_stock);
    $("#stat-low").textContent = formatQty(totals.low_stock);
    $("#stat-out-foot").textContent = totals.out_of_stock
      ? "necessitam de compra imediata"
      : "nenhum item zerado";

    const badge = $("#nav-badge");
    if (badge) {
      badge.textContent = totals.low_stock;
      badge.hidden = totals.low_stock === 0;
    }
  }

  function renderChart() {
    const chart = state.dashboard?.chart || [];
    const container = $("#chart");
    if (!container) return;

    const max = Math.max(1, ...chart.flatMap((day) => [day.entradas, day.saidas]));

    container.innerHTML = chart.map((day) => {
      const inHeight = day.entradas ? Math.max(4, (day.entradas / max) * 100) : 3;
      const outHeight = day.saidas ? Math.max(4, (day.saidas / max) * 100) : 3;
      const inClass = day.entradas ? "bar bar-in" : "bar bar-in is-zero";
      const outClass = day.saidas ? "bar bar-out" : "bar bar-out is-zero";
      return `
        <div class="chart-col${day.is_today ? " is-today" : ""}">
          <div class="chart-bars">
            <div class="${inClass}" style="height:${inHeight}%" title="Entradas: ${formatQty(day.entradas)}"></div>
            <div class="${outClass}" style="height:${outHeight}%" title="Saídas: ${formatQty(day.saidas)}"></div>
          </div>
          <span class="chart-label${day.is_today ? " is-today" : ""}">${escapeHtml(day.label)}</span>
        </div>
      `;
    }).join("");
  }

  function renderMonth() {
    const month = state.dashboard?.month;
    if (!month) return;

    $("#month-label").textContent = month.label;
    $("#month-in-qty").textContent = formatQty(month.entradas.quantity);
    $("#month-in-count").textContent = plural(month.entradas.count, "registro", "registros");
    $("#month-out-qty").textContent = formatQty(month.saidas.quantity);
    $("#month-out-count").textContent = plural(month.saidas.count, "registro", "registros");

    const balance = $("#month-balance");
    const value = Number(month.balance) || 0;
    balance.textContent = `${value > 0 ? "+" : value < 0 ? "−" : ""}${formatQty(Math.abs(value))}`;
    balance.closest(".month-balance")?.classList.toggle("is-negative", value < 0);
  }

  function statusBadge(item) {
    if (item.is_out) {
      return '<span class="badge badge-danger"><span class="badge-dot"></span>Em falta</span>';
    }
    if (item.is_low) {
      return '<span class="badge badge-warning"><span class="badge-dot"></span>Repor</span>';
    }
    return '<span class="badge badge-success"><span class="badge-dot"></span>Em dia</span>';
  }

  function renderAlerts() {
    const alerts = state.dashboard?.alerts || [];
    const container = $("#alerts-body");
    if (!container) return;

    const subtitle = $("#alerts-sub");
    if (subtitle) {
      subtitle.textContent = alerts.length
        ? `${plural(alerts.length, "item precisa", "itens precisam")} de reposição`
        : "Itens abaixo da previsão mensal";
    }

    if (!alerts.length) {
      container.innerHTML = `
        <div class="alert-empty">
          <svg class="icon"><use href="#i-check"/></svg>
          <p><strong>Estoque saudável!</strong><br>Nenhum item abaixo da previsão mensal.</p>
        </div>
      `;
      return;
    }

    container.innerHTML = alerts.slice(0, 8).map((item) => `
      <div class="alert-item">
        <div class="alert-info">
          <div class="alert-title-row">
            <strong>${escapeHtml(item.name)}</strong>
            ${item.is_out
              ? '<span class="badge badge-danger">Em falta</span>'
              : '<span class="badge badge-warning">Repor</span>'}
          </div>
          <p class="alert-meta">${escapeHtml(item.sector_label)} · previsão ${formatQty(item.monthly_forecast)} ${escapeHtml(item.unit_short)}/mês</p>
          <div class="meter">
            <span class="meter-fill is-low" style="width:${Math.max(0, Math.min(item.percent, 100))}%"></span>
          </div>
          <p class="alert-stock">${formatQty(item.quantity)} de ${formatQty(item.monthly_forecast)} ${escapeHtml(item.unit_short)} <span>·</span> faltam ${formatQty(item.deficit)}</p>
        </div>
        <button type="button" class="btn btn-soft-danger btn-xs" data-action="restock" data-id="${item.id}">Repor</button>
      </div>
    `).join("");
  }

  function renderHistory() {
    const recent = state.dashboard?.recent || [];
    const container = $("#history-body");
    if (!container) return;

    if (!recent.length) {
      container.innerHTML = `
        <div class="alert-empty">
          <svg class="icon"><use href="#i-clock"/></svg>
          <p>Nenhuma movimentação registrada ainda.</p>
        </div>
      `;
      return;
    }

    container.innerHTML = recent.map((movement) => `
      <div class="history-item">
        <span class="history-dot ${movement.type}">
          <svg class="icon"><use href="#${movement.type === "entrada" ? "i-in" : "i-out"}"/></svg>
        </span>
        <div class="history-text">
          <strong>${escapeHtml(movement.item_name)}</strong>
          <span>${movement.type === "entrada" ? "Entrada" : "Saída"} de ${formatQty(movement.quantity)} ${escapeHtml(movement.unit_short)} · ${escapeHtml(movement.sector_label)}</span>
        </div>
        <span class="history-time">${timeAgo(movement.created_at)}</span>
      </div>
    `).join("");
  }

  function renderSidebarAlert() {
    const container = $("#sidebar-alert");
    if (!container) return;
    const alerts = state.dashboard?.alerts || [];

    if (!alerts.length) {
      container.innerHTML = `
        <div class="sidebar-alert sidebar-alert-ok">
          <span class="sidebar-alert-icon"><svg class="icon"><use href="#i-check"/></svg></span>
          <p><strong>Tudo em dia.</strong><br>Nenhum item precisa de reposição.</p>
        </div>
      `;
      return;
    }

    container.innerHTML = `
      <div class="sidebar-alert">
        <span class="sidebar-alert-icon"><svg class="icon"><use href="#i-alert"/></svg></span>
        <p><strong>${plural(alerts.length, "item precisa", "itens precisam")}</strong><br>de reposição neste momento.</p>
        <button type="button" class="btn btn-soft btn-sm" data-action="show-alerts">
          Ver alertas <svg class="icon"><use href="#i-chevron"/></svg>
        </button>
      </div>
    `;
  }

  // -------------------------------------------------------------------------
  // Movimentações
  // -------------------------------------------------------------------------

  function renderMovementGrid() {
    const grid = $("#movement-grid");
    const empty = $("#movement-empty");
    if (!grid) return;

    const sector = state.movementSector;
    const search = state.movementSearch.trim().toLowerCase();

    const items = state.items.filter((item) => {
      if (sector !== "todos" && item.sector !== sector) return false;
      if (search) {
        const haystack = `${item.name} ${item.sector_label}`.toLowerCase();
        if (!haystack.includes(search)) return false;
      }
      return true;
    });

    const count = $("#movement-count");
    if (count) count.textContent = plural(items.length, "item", "itens");

    if (!items.length) {
      grid.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    grid.innerHTML = items.map((item) => `
      <article class="move-card${item.is_low ? " is-low" : ""}">
        <div class="move-card-head">
          <div>
            <h3>${escapeHtml(item.name)}</h3>
            <p class="move-meta-top">${escapeHtml(item.sector_label)}</p>
          </div>
          <div class="move-qty">
            <strong>${formatQty(item.quantity)}</strong>
            <small>${escapeHtml(item.unit_short)}</small>
          </div>
        </div>

        <div class="meter">
          <span class="meter-fill${item.is_low ? " is-low" : ""}" style="width:${Math.max(0, Math.min(item.percent, 100))}%"></span>
        </div>

        <div class="move-meta">
          <span>Previsão: ${formatQty(item.monthly_forecast)} ${escapeHtml(item.unit_short)}</span>
          ${statusBadge(item)}
        </div>

        <div class="stepper">
          <button type="button" class="step-minus" data-action="move" data-id="${item.id}" data-type="saida"
            aria-label="Registrar saída de 1 unidade de ${escapeHtml(item.name)}" ${item.quantity <= 0 ? "disabled" : ""}>
            <svg class="icon"><use href="#i-minus"/></svg>
          </button>
          <span class="stepper-label">Ajuste rápido</span>
          <button type="button" class="step-plus" data-action="move" data-id="${item.id}" data-type="entrada"
            aria-label="Registrar entrada de 1 unidade de ${escapeHtml(item.name)}">
            <svg class="icon"><use href="#i-plus"/></svg>
          </button>
        </div>

        <button type="button" class="detail-btn" data-action="detail-movement" data-id="${item.id}">
          <svg class="icon"><use href="#i-edit"/></svg> Registrar com observação
        </button>
      </article>
    `).join("");
  }

  async function quickMove(itemId, type, button) {
    const item = state.items.find((entry) => entry.id === itemId);
    if (!item) return;

    if (type === "saida" && item.quantity <= 0) {
      toast("Este item está sem estoque.", "error");
      return;
    }

    const card = button?.closest(".move-card");
    card?.classList.add("is-busy");

    try {
      await api("/api/movements", {
        method: "POST",
        body: { item_id: itemId, type, quantity: 1 },
      });
      await loadData();
      toast(
        `${type === "entrada" ? "+1" : "−1"} ${item.name} · ${type === "entrada" ? "entrada" : "saída"} registrada`,
        type === "entrada" ? "success" : "info"
      );
    } catch (error) {
      toast(error.message, "error");
      card?.classList.remove("is-busy");
    }
  }

  // -------------------------------------------------------------------------
  // Itens
  // -------------------------------------------------------------------------

  function renderItems() {
    const list = $("#items-list");
    const empty = $("#items-empty");
    if (!list) return;

    const filters = state.filters;
    const search = filters.search.trim().toLowerCase();

    const items = state.items.filter((item) => {
      if (filters.sector && item.sector !== filters.sector) return false;
      if (filters.onlyLow && !item.is_low) return false;
      if (search) {
        const haystack = `${item.name} ${item.sector_label}`.toLowerCase();
        if (!haystack.includes(search)) return false;
      }
      return true;
    });

    const count = $("#items-count");
    if (count) count.textContent = plural(items.length, "item encontrado", "itens encontrados");

    if (!items.length) {
      list.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    list.innerHTML = items.map((item) => `
      <div class="item-row${item.is_low ? " is-low" : ""}">
        <div class="cell cell-name">
          <span class="cell-label">Item</span>
          <div class="item-name">
            <strong>${escapeHtml(item.name)}</strong>
            <span class="unit-chip">${escapeHtml(item.unit_short)}</span>
          </div>
        </div>

        <div class="cell">
          <span class="cell-label">Setor</span>
          <span class="sector-pill is-${item.sector}">
            <svg class="icon"><use href="#${item.sector === "limpeza" ? "i-drop" : "i-book"}"/></svg>
            ${escapeHtml(item.sector_label)}
          </span>
        </div>

        <div class="cell cell-num">
          <span class="cell-label">Previsão</span>
          ${formatQty(item.monthly_forecast)} <small>${escapeHtml(item.unit_short)}</small>
        </div>

        <div class="cell cell-stock">
          <span class="cell-label">Estoque</span>
          <strong>${formatQty(item.quantity)}</strong> <small>${escapeHtml(item.unit_short)}</small>
        </div>

        <div class="cell">
          <span class="cell-label">Status</span>
          ${statusBadge(item)}
        </div>

        <div class="cell cell-actions">
          <button type="button" class="icon-btn" data-action="detail-movement" data-id="${item.id}" aria-label="Movimentar ${escapeHtml(item.name)}" title="Movimentar">
            <svg class="icon"><use href="#i-swap"/></svg>
          </button>
          <button type="button" class="icon-btn" data-action="edit-item" data-id="${item.id}" aria-label="Editar ${escapeHtml(item.name)}" title="Editar">
            <svg class="icon"><use href="#i-edit"/></svg>
          </button>
          <button type="button" class="icon-btn is-danger" data-action="delete-item" data-id="${item.id}" aria-label="Excluir ${escapeHtml(item.name)}" title="Excluir">
            <svg class="icon"><use href="#i-trash"/></svg>
          </button>
        </div>
      </div>
    `).join("");
  }

  function showAlerts() {
    state.filters = { search: "", sector: "", onlyLow: true };
    const search = $("#item-search");
    const sector = $("#item-sector");
    const onlyLow = $("#item-only-low");
    if (search) search.value = "";
    if (sector) sector.value = "";
    if (onlyLow) onlyLow.checked = true;
    setView("itens");
  }

  function clearItemFilters() {
    state.filters = { search: "", sector: "", onlyLow: false };
    const search = $("#item-search");
    const sector = $("#item-sector");
    const onlyLow = $("#item-only-low");
    if (search) search.value = "";
    if (sector) sector.value = "";
    if (onlyLow) onlyLow.checked = false;
    renderItems();
  }

  // -------------------------------------------------------------------------
  // Relatórios
  // -------------------------------------------------------------------------

  function reportItems() {
    if (state.reportSector === "todos") return state.items;
    return state.items.filter((item) => item.sector === state.reportSector);
  }

  function renderReports() {
    const list = $("#report-list");
    const empty = $("#report-empty");
    if (!list) return;

    const items = reportItems();
    const count = $("#report-count");
    if (count && state.reportSector !== "todos") {
      count.textContent = `${plural(items.length, "item", "itens")} · ${state.reportSector === "limpeza" ? "Limpeza" : "Pedagógico"}`;
    } else if (count) {
      const pedagogico = state.items.filter((item) => item.sector === "pedagogico").length;
      const limpeza = state.items.filter((item) => item.sector === "limpeza").length;
      count.textContent = `${plural(items.length, "item no relatório", "itens no relatório")} · Pedagógico ${pedagogico} · Limpeza ${limpeza}`;
    }

    if (!items.length) {
      list.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    list.innerHTML = items.map((item, index) => `
      <div class="report-row">
        <span class="report-index" data-label="N">${index + 1}</span>
        <span class="report-material" data-label="Materiais">${escapeHtml(item.name)}</span>
        <span data-label="Setor"><span class="sector-pill is-${item.sector}">${escapeHtml(item.sector_label)}</span></span>
        <span data-label="Como solicitar (unidade)">${escapeHtml(item.unit)}</span>
        <span class="report-num" data-label="Previsão mensal">${formatQty(item.monthly_forecast)}</span>
        <span class="report-num" data-label="Estoque"><strong>${formatQty(item.quantity)}</strong></span>
      </div>
    `).join("");
  }

  async function exportReport() {
    if (!state.loaded) {
      try {
        await loadData();
      } catch (error) {
        // Se a atualização falhar, segue com a exportação normal.
      }
    }

    const items = reportItems();
    if (state.loaded && !items.length) {
      toast(
        state.reportSector === "todos"
          ? "Não há itens cadastrados para exportar. Importe uma planilha ou cadastre um item."
          : "Não há itens neste setor para exportar. Importe uma planilha ou selecione outro setor.",
        "error"
      );
      return;
    }

    const button = $('[data-action="export-report"]');
    setButtonLoading(button, true, "Gerando...");
    try {
      const sector = state.reportSector !== "todos" ? `?sector=${encodeURIComponent(state.reportSector)}` : "";
      await downloadFile(`/api/reports/export${sector}`, "estoque-creche.xlsx");
      toast("Planilha exportada com sucesso.");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setButtonLoading(button, false);
    }
  }

  async function downloadTemplate() {
    const button = $('[data-action="download-template"]');
    setButtonLoading(button, true, "Gerando...");
    try {
      await downloadFile("/api/reports/template", "modelo-estoque.xlsx");
      toast("Modelo de planilha baixado.");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      setButtonLoading(button, false);
    }
  }

  function renderImportResult(data) {
    const container = $("#import-result");
    if (!container) return;
    container.hidden = false;

    const parts = [];
    if (data.created) parts.push(plural(data.created, "item cadastrado", "itens cadastrados"));
    if (data.updated) parts.push(plural(data.updated, "item atualizado", "itens atualizados"));
    if (data.skipped) parts.push(plural(data.skipped, "linha ignorada", "linhas ignoradas"));

    const bySector = data.by_sector || {};
    const sectorParts = [];
    if (bySector.pedagogico) sectorParts.push(`Pedagógico: ${bySector.pedagogico}`);
    if (bySector.limpeza) sectorParts.push(`Limpeza: ${bySector.limpeza}`);

    const items = data.items || [];
    const errors = data.errors || [];
    const warnings = data.warnings || [];
    container.className = `import-result${data.total ? " is-success" : " is-warning"}`;
    container.innerHTML = `
      <p class="import-result-title">
        <svg class="icon"><use href="#${data.total ? "i-check" : "i-alert"}"/></svg>
        ${data.total ? "Importação concluída" : "Nenhum item importado"}
      </p>
      <p class="import-result-summary">${parts.length ? parts.join(" · ") : "Nenhuma linha válida encontrada."}</p>
      ${sectorParts.length ? `<p class="import-result-note">Por setor: ${sectorParts.join(" · ")}</p>` : ""}
      ${items.length ? `
        <ul class="import-items">
          ${items.map((entry) => `
            <li>
              <span class="import-item-name">${escapeHtml(entry.name)}</span>
              <span class="import-item-meta">
                ${escapeHtml(entry.sector_label || "")}
                · ${entry.action === "created" ? "novo" : "atualizado"}
              </span>
            </li>
          `).join("")}
        </ul>
      ` : ""}
      ${data.items_truncated ? '<p class="import-result-note">Mostrando os 200 primeiros itens.</p>' : ""}
      ${warnings.length ? `
        <ul class="import-errors import-warnings">
          ${warnings.map((warning) => {
            const location = [
              warning.sheet ? `Aba ${warning.sheet}` : "",
              warning.line ? `Linha ${warning.line}` : "",
              warning.item ? `Item: ${warning.item}` : "",
            ].filter(Boolean).join(" · ");
            return `<li><strong>${escapeHtml(location)}:</strong> ${escapeHtml(warning.message)}</li>`;
          }).join("")}
        </ul>
      ` : ""}
      ${data.warnings_truncated ? '<p class="import-result-note">Mostrando os 50 primeiros avisos.</p>' : ""}
      ${errors.length ? `
        <ul class="import-errors">
          ${errors.map((error) => {
            const location = [
              error.sheet ? `Aba ${error.sheet}` : "",
              error.line ? `Linha ${error.line}` : "",
              error.item ? `Item: ${error.item}` : "",
            ].filter(Boolean).join(" · ");
            return `<li><strong>${escapeHtml(location)}:</strong> ${escapeHtml(error.message)}</li>`;
          }).join("")}
        </ul>
      ` : ""}
      ${data.errors_truncated ? '<p class="import-result-note">Mostrando os 50 primeiros avisos.</p>' : ""}
    `;
  }

  async function importReport() {
    const input = $("#import-file");
    const button = $("#import-submit");
    const file = input?.files?.[0];

    if (!file) {
      toast("Selecione uma planilha .xlsx para importar.", "error");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".xlsx")) {
      toast("O arquivo deve estar no formato .xlsx (Excel).", "error");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);
    const sector = $("#import-sector")?.value;
    if (sector) formData.append("sector", sector);

    setButtonLoading(button, true, "Importando...");
    try {
      const data = await api("/api/reports/import", { method: "POST", body: formData });
      renderImportResult(data);
      await loadData();
      if (data.total) {
        toast(`${plural(data.total, "item importado", "itens importados")} com sucesso.`);
      } else {
        toast("Nenhum item foi importado. Confira os avisos.", "error");
      }
    } catch (error) {
      const container = $("#import-result");
      if (container) {
        container.hidden = false;
        container.className = "import-result is-error";
        container.innerHTML = `
          <p class="import-result-title">
            <svg class="icon"><use href="#i-alert"/></svg>
            Falha na importação
          </p>
          <p class="import-result-summary">${escapeHtml(error.message)}</p>
        `;
      }
      toast(error.message, "error");
    } finally {
      setButtonLoading(button, false);
    }
  }

  // -------------------------------------------------------------------------
  // Diálogos e formulários
  // -------------------------------------------------------------------------

  function setSegment(selector, value) {
    $$(`${selector} button[data-value]`).forEach((button) => {
      button.classList.toggle("is-active", button.dataset.value === value);
    });
  }

  function buildUnitOptions(select, selectedUnit) {
    if (!select) return;
    const units = state.units.length ? state.units : [{ label: "Unidade", short: "un" }];
    select.innerHTML = units.map((unit) =>
      `<option value="${escapeHtml(unit.label)}">${escapeHtml(unit.label)}</option>`
    ).join("");

    if (selectedUnit) {
      if (!units.some((unit) => unit.label === selectedUnit)) {
        const option = document.createElement("option");
        option.value = selectedUnit;
        option.textContent = selectedUnit;
        select.appendChild(option);
      }
      select.value = selectedUnit;
    }
  }

  function showFormError(element, message) {
    if (!element) return;
    element.textContent = message;
    element.hidden = false;
  }

  function setButtonLoading(button, loading, label) {
    if (!button) return;
    if (loading) {
      if (!button.dataset.originalHtml) button.dataset.originalHtml = button.innerHTML;
      button.textContent = label || "Salvando...";
      button.disabled = true;
    } else {
      if (button.dataset.originalHtml) {
        button.innerHTML = button.dataset.originalHtml;
        delete button.dataset.originalHtml;
      }
      button.disabled = false;
    }
  }

  function numericValue(selector) {
    const input = $(selector);
    if (!input) return 0;
    const number = Number(String(input.value).replace(",", "."));
    return Number.isFinite(number) ? number : 0;
  }

  function openItemDialog(item = null) {
    const dialog = $("#dialog-item");
    $("#form-item").reset();
    $("#item-error").hidden = true;
    $("#item-id").value = item ? item.id : "";
    $("#item-dialog-title").textContent = item ? "Editar item" : "Novo item";
    $("#item-submit").textContent = item ? "Salvar alterações" : "Salvar item";
    $("#field-initial").hidden = Boolean(item);

    const sector = item?.sector === "limpeza" ? "limpeza" : "pedagogico";
    $("#item-category-sector").value = sector;
    setSegment("#item-category-group", sector);

    buildUnitOptions($("#item-unit"), item?.unit);

    $("#item-name").value = item?.name || "";
    $("#item-forecast").value = item ? item.monthly_forecast : 0;
    $("#item-initial").value = 0;

    dialog.showModal();
    window.setTimeout(() => $("#item-name").focus(), 60);
  }

  function openMovementDialog(item, type = "entrada", quantity = 1) {
    if (!item) return;
    const dialog = $("#dialog-movement");
    $("#form-movement").reset();
    $("#movement-error").hidden = true;
    $("#movement-item-id").value = item.id;
    $("#movement-item-name").textContent = item.name;
    $("#movement-stock").textContent = `${formatQty(item.quantity)} ${item.unit_short}`;
    $("#movement-type").value = type;
    setSegment("#movement-type-group", type);
    $("#movement-quantity").value = quantity;
    $("#movement-note").value = "";
    dialog.showModal();
    window.setTimeout(() => $("#movement-quantity").select(), 60);
  }

  function categoryIdForSector(sector) {
    const category = state.categories.find((entry) => entry.sector === sector);
    return category ? category.id : 0;
  }

  async function handleItemSubmit(event) {
    event.preventDefault();
    const id = Number($("#item-id").value || 0);
    const name = $("#item-name").value.trim();
    const sector = $("#item-category-sector").value;
    const categoryId = categoryIdForSector(sector);
    const unit = $("#item-unit").value;
    const forecast = numericValue("#item-forecast");
    const initial = numericValue("#item-initial");
    const errorElement = $("#item-error");
    const submitButton = $("#item-submit");

    if (!name) return showFormError(errorElement, "Informe o nome do item.");
    if (!["pedagogico", "limpeza"].includes(sector) || !categoryId) {
      return showFormError(errorElement, "Selecione a categoria do item.");
    }
    if (!unit) return showFormError(errorElement, "Selecione a unidade de medida.");
    if (forecast < 0) return showFormError(errorElement, "A previsão mensal não pode ser negativa.");
    if (!id && initial < 0) return showFormError(errorElement, "O saldo inicial não pode ser negativo.");

    const payload = {
      name,
      category_id: categoryId,
      unit,
      monthly_forecast: forecast,
    };
    if (!id) payload.initial_quantity = initial;

    setButtonLoading(submitButton, true, "Salvando...");
    try {
      if (id) {
        await api(`/api/items/${id}`, { method: "PUT", body: payload });
      } else {
        await api("/api/items", { method: "POST", body: payload });
      }
      $("#dialog-item").close();
      await loadData();
      toast(id ? "Item atualizado com sucesso." : "Item cadastrado com sucesso.");
    } catch (error) {
      showFormError(errorElement, error.message);
    } finally {
      setButtonLoading(submitButton, false);
    }
  }

  async function handleMovementSubmit(event) {
    event.preventDefault();
    const itemId = Number($("#movement-item-id").value || 0);
    const item = state.items.find((entry) => entry.id === itemId);
    const type = $("#movement-type").value;
    const quantity = numericValue("#movement-quantity");
    const note = $("#movement-note").value.trim();
    const errorElement = $("#movement-error");
    const submitButton = $("#movement-submit");

    if (!item) return showFormError(errorElement, "Item não encontrado.");
    if (!["entrada", "saida"].includes(type)) {
      return showFormError(errorElement, "Selecione o tipo de movimentação.");
    }
    if (!(quantity > 0)) return showFormError(errorElement, "Informe uma quantidade maior que zero.");
    if (type === "saida" && quantity > item.quantity) {
      return showFormError(
        errorElement,
        `Estoque insuficiente: restam ${formatQty(item.quantity)} ${item.unit_short}.`
      );
    }

    setButtonLoading(submitButton, true, "Registrando...");
    try {
      await api("/api/movements", {
        method: "POST",
        body: { item_id: itemId, type, quantity, note },
      });
      $("#dialog-movement").close();
      await loadData();
      toast(
        `${type === "entrada" ? "Entrada" : "Saída"} de ${formatQty(quantity)} ${item.unit_short} registrada em ${item.name}.`,
        type === "entrada" ? "success" : "info"
      );
    } catch (error) {
      showFormError(errorElement, error.message);
    } finally {
      setButtonLoading(submitButton, false);
    }
  }

  // -------------------------------------------------------------------------
  // Exclusões
  // -------------------------------------------------------------------------

  function confirmAction({ title, message, confirmLabel = "Excluir" }) {
    return new Promise((resolve) => {
      const dialog = $("#dialog-confirm");
      $("#confirm-title").textContent = title;
      $("#confirm-message").textContent = message;
      $("#confirm-button").textContent = confirmLabel;
      dialog.returnValue = "";
      dialog.showModal();

      const handleClose = () => {
        dialog.removeEventListener("close", handleClose);
        resolve(dialog.returnValue === "confirm");
      };
      dialog.addEventListener("close", handleClose);
    });
  }

  async function deleteItem(item) {
    const confirmed = await confirmAction({
      title: "Excluir item?",
      message: `O item “${item.name}” e todo o seu histórico de movimentações serão removidos. Esta ação não pode ser desfeita.`,
      confirmLabel: "Excluir item",
    });
    if (!confirmed) return;

    try {
      await api(`/api/items/${item.id}`, { method: "DELETE" });
      await loadData();
      toast("Item excluído.");
    } catch (error) {
      toast(error.message, "error");
    }
  }

  // -------------------------------------------------------------------------
  // Eventos
  // -------------------------------------------------------------------------

  function handleDocumentClick(event) {
    const closeButton = event.target.closest("[data-close]");
    if (closeButton) {
      closeButton.closest("dialog")?.close();
      return;
    }

    const navButton = event.target.closest("[data-view]");
    if (navButton) {
      setView(navButton.dataset.view);
      return;
    }

    const actionElement = event.target.closest("[data-action]");
    if (!actionElement) return;

    const action = actionElement.dataset.action;
    const id = Number(actionElement.dataset.id || 0);

    switch (action) {
      case "go-movements":
        setView("movimentacoes");
        break;
      case "go-items":
        setView("itens");
        break;
      case "show-alerts":
        showAlerts();
        break;
      case "new-item":
        openItemDialog();
        break;
      case "edit-item": {
        const item = state.items.find((entry) => entry.id === id);
        if (item) openItemDialog(item);
        break;
      }
      case "delete-item": {
        const item = state.items.find((entry) => entry.id === id);
        if (item) deleteItem(item);
        break;
      }
      case "export-report":
        exportReport();
        break;
      case "download-template":
        downloadTemplate();
        break;
      case "import-report":
        importReport();
        break;
      case "move":
        quickMove(id, actionElement.dataset.type, actionElement);
        break;
      case "detail-movement": {
        const item = state.items.find((entry) => entry.id === id);
        if (item) openMovementDialog(item);
        break;
      }
      case "restock": {
        const item = state.items.find((entry) => entry.id === id);
        if (item) openMovementDialog(item, "entrada", Math.max(item.deficit, 1));
        break;
      }
      case "logout":
        logout();
        break;
      default:
        break;
    }
  }

  function bindEvents() {
    document.addEventListener("click", handleDocumentClick);

    $("#menu-btn")?.addEventListener("click", () => {
      if ($("#sidebar")?.classList.contains("is-open")) closeSidebar();
      else openSidebar();
    });

    $("#nav-overlay")?.addEventListener("click", closeSidebar);

    const userMenu = $("#user-menu");
    const userButton = $("#user-button");
    const userDropdown = $("#user-dropdown");

    const closeUserMenu = () => {
      if (!userDropdown || userDropdown.hidden) return;
      userDropdown.hidden = true;
      userButton?.setAttribute("aria-expanded", "false");
    };

    userButton?.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!userDropdown) return;
      const willOpen = userDropdown.hidden;
      userDropdown.hidden = !willOpen;
      userButton.setAttribute("aria-expanded", willOpen ? "true" : "false");
    });

    userDropdown?.addEventListener("click", (event) => {
      if (event.target.closest("[data-action]")) closeUserMenu();
    });

    document.addEventListener("click", (event) => {
      if (!userMenu || userMenu.contains(event.target)) return;
      closeUserMenu();
    });

    $("#sector-switch")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-sector]");
      if (!button) return;
      state.movementSector = button.dataset.sector;
      $$("#sector-switch button").forEach((element) => {
        element.classList.toggle("is-active", element === button);
      });
      renderMovementGrid();
    });

    $("#movement-search")?.addEventListener("input", (event) => {
      state.movementSearch = event.target.value;
      renderMovementGrid();
    });

    $("#item-search")?.addEventListener("input", (event) => {
      state.filters.search = event.target.value;
      renderItems();
    });

    $("#item-sector")?.addEventListener("change", (event) => {
      state.filters.sector = event.target.value;
      renderItems();
    });

    $("#item-only-low")?.addEventListener("change", (event) => {
      state.filters.onlyLow = event.target.checked;
      renderItems();
    });

    $("#clear-item-filters")?.addEventListener("click", clearItemFilters);

    $("#item-category-group")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-value]");
      if (!button) return;
      $("#item-category-sector").value = button.dataset.value;
      setSegment("#item-category-group", button.dataset.value);
    });

    $("#report-sector")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-sector]");
      if (!button) return;
      state.reportSector = button.dataset.sector;
      $$("#report-sector button").forEach((element) => {
        element.classList.toggle("is-active", element === button);
      });
      renderReports();
    });

    $("#import-file")?.addEventListener("change", (event) => {
      const file = event.target.files?.[0];
      const name = $("#import-file-name");
      if (name) name.textContent = file ? file.name : "Nenhum arquivo selecionado";
    });

    $("#movement-type-group")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-value]");
      if (!button) return;
      $("#movement-type").value = button.dataset.value;
      setSegment("#movement-type-group", button.dataset.value);
    });

    $("#form-item")?.addEventListener("submit", handleItemSubmit);
    $("#form-movement")?.addEventListener("submit", handleMovementSubmit);

    ["#form-item", "#form-movement"].forEach((selector) => {
      $(selector)?.addEventListener("input", (event) => {
        const errorElement = event.currentTarget.querySelector(".form-error");
        if (errorElement) errorElement.hidden = true;
      });
    });

    $("#confirm-button")?.addEventListener("click", () => {
      const dialog = $("#dialog-confirm");
      dialog.returnValue = "confirm";
      dialog.close();
    });

    $("#confirm-cancel")?.addEventListener("click", () => {
      const dialog = $("#dialog-confirm");
      dialog.returnValue = "cancel";
      dialog.close();
    });

    $$("dialog.modal").forEach((dialog) => {
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) dialog.close();
      });
    });

    window.addEventListener("hashchange", () => {
      const view = (window.location.hash || "").replace("#", "");
      setView(view, { updateHash: false });
    });

    window.addEventListener("resize", () => {
      if (window.innerWidth > 1024) closeSidebar();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      closeSidebar();
      closeUserMenu();
    });
  }

  // -------------------------------------------------------------------------
  // Inicialização
  // -------------------------------------------------------------------------

  function init() {
    const now = new Date();
    const dateLabel = now.toLocaleDateString("pt-BR", {
      weekday: "long",
      day: "2-digit",
      month: "long",
    });

    const todayElement = $("#today-label");
    if (todayElement) {
      todayElement.textContent = dateLabel.charAt(0).toUpperCase() + dateLabel.slice(1);
    }
    const yearElement = $("#year-label");
    if (yearElement) yearElement.textContent = now.getFullYear();

    bindEvents();

    const initialView = (window.location.hash || "").replace("#", "");
    setView(initialView || "painel", { updateHash: false, scroll: false });

    loadData();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();


