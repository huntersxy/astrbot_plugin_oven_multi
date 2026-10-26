"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  statusBusy: false,
  balanceBusy: false,
  balanceConfigured: false,
  tab: "overview",
  hasStatus: false,
  generation: 0,
  settingsLoaded: false,
  settingsBusy: false,
  settingsDirty: false,
  settingsRevision: null,
  biliBusy: false,
  settingsCategory: "消息与回复",
  compressionBusy: false,
  compressionLoaded: false,
  compressionPage: 1,
  compressionQuery: "",
  compressionDetail: 0,
};
const TAB_ORDER = ["overview", "compression", "balances", "settings"];

function unwrap(payload) {
  if (payload?.success === false || payload?.status === "error") {
    const error = new Error(payload?.message || "API response failed");
    error.stale = Boolean(payload?.stale);
    throw error;
  }
  return payload && ("success" in payload || "status" in payload) && "data" in payload ? payload.data : payload;
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = String(text ?? "");
  return element;
}

function statusLabel(enabled, text) {
  const label = node("span", enabled ? "status on" : "status", text || (enabled ? "已开启" : "已关闭"));
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  icon.setAttribute("viewBox", "0 0 16 16");
  icon.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", enabled ? "M3 8l3 3 7-7" : "M4 8h8");
  icon.append(path);
  label.prepend(icon);
  return label;
}

function selectTab(name, focus = false) {
  state.tab = name;
  for (const button of document.querySelectorAll("[data-tab]")) {
    const selected = button.dataset.tab === name;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    $("panel-" + button.dataset.tab).hidden = !selected;
    if (selected && focus) button.focus();
  }
  if (name === "settings" && !state.settingsLoaded) loadSettings();
  if (name === "compression" && !state.compressionLoaded) loadCompression();
}

function renderFeatures(features) {
  const groups = [
    { name: "消息与回复", ids: ["bracket_matching", "repetition", "remove_blank_lines", "iam_thinking", "mention_parser", "segment_reply"] },
    { name: "判读、文件与通知", ids: ["jev", "file_reader", "douyin"] },
  ];
  const container = $("feature-groups");
  container.replaceChildren();
  const known = new Set(groups.flatMap((group) => group.ids));
  if (features.some((feature) => !known.has(feature.id))) groups.push({ name: "其他功能", ids: features.filter((feature) => !known.has(feature.id)).map((feature) => feature.id) });
  for (const group of groups) {
    const rows = features.filter((feature) => group.ids.includes(feature.id));
    if (!rows.length) continue;
    const section = node("section", "feature-section");
    section.append(node("h3", "", group.name));
    const list = node("ul");
    for (const feature of rows) {
      const row = node("li", "feature-row");
      const body = node("div");
      body.append(node("span", "feature-name", feature.name), node("span", "feature-detail", feature.detail));
      row.append(body, statusLabel(Boolean(feature.enabled)));
      list.append(row);
    }
    section.append(list);
    container.append(section);
  }
  const enabled = features.filter((feature) => feature.enabled).length;
  $("feature-summary").textContent = `${enabled} / ${features.length} 已开启`;
  $("feature-empty").hidden = features.length > 0;
}

function setError(id, message) {
  $(id).textContent = message || "";
  $(id).hidden = !message;
}

async function loadStatus() {
  if (state.statusBusy) return;
  state.statusBusy = true;
  state.generation += 1;
  $("query-balances").disabled = true;
  $("refresh").disabled = true;
  $("panel-overview").setAttribute("aria-busy", "true");
  setError("status-error", "");
  try {
    const data = unwrap(await window.AstrBotPluginPage.apiGet("status"));
    if (!data || !Array.isArray(data.features)) throw new Error("Invalid status response");
    renderFeatures(data.features);
    $("notices").replaceChildren(...(data.notices || []).map((message) => node("p", "message", message)));
    state.balanceConfigured = Boolean(data.balance_configured);
    if (!state.balanceConfigured) {
      $("balance-results").replaceChildren();
      $("balance-empty").hidden = false;
      $("balance-time").textContent = "尚无查询结果";
    }
    $("query-balances").disabled = !state.balanceConfigured || state.balanceBusy;
    $("balance-empty-copy").textContent = state.balanceConfigured
      ? "点击「查询余额」读取服务结果。成功结果短暂缓存 30 秒，避免重复请求。"
      : "尚未配置余额服务。请在 AstrBot 插件配置的「余额查询」中添加服务。";
    $("version").textContent = data.plugin?.version ? `v${data.plugin.version.replace(/^v/, "")}` : "Oven Multi";
    $("status-time").textContent = `状态更新于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
    $("announcer").textContent = "功能状态已更新";
    state.hasStatus = true;
  } catch (error) {
    setError("status-error", state.hasStatus
      ? "状态刷新失败，已保留上次结果。请确认插件已启用，再点击「刷新状态」。"
      : "暂时无法读取状态。请确认插件已启用，再点击「刷新状态」重试。");
    if (!state.hasStatus) $("feature-summary").textContent = "连接失败";
    console.warn("Oven Multi status request failed", error?.name);
  } finally {
    state.statusBusy = false;
    $("refresh").disabled = false;
    $("query-balances").disabled = !state.balanceConfigured || state.balanceBusy;
    $("feature-loading").hidden = true;
    $("panel-overview").setAttribute("aria-busy", "false");
  }
}

async function loadBalances() {
  if (state.statusBusy || state.balanceBusy || !state.balanceConfigured) return;
  state.balanceBusy = true;
  const generation = state.generation;
  $("query-balances").disabled = true;
  $("query-balances").querySelector("span").textContent = "正在查询…";
  $("panel-balances").setAttribute("aria-busy", "true");
  setError("balance-error", "");
  try {
    const data = unwrap(await window.AstrBotPluginPage.apiGet("balance"));
    if (generation !== state.generation) return;
    if (!data || !Array.isArray(data.items)) throw new Error("Invalid balance response");
    const rows = data.items.map((item) => {
      const row = node("div", "balance-row");
      row.append(node("h3", "", item.name), node("p", item.success ? "" : "failed", item.value), statusLabel(Boolean(item.success), item.success ? "查询成功" : "需检查"));
      return row;
    });
    $("balance-results").replaceChildren(...rows);
    $("balance-empty").hidden = rows.length > 0;
    $("balance-empty-copy").textContent = "没有返回服务结果，请检查余额查询配置中的 services。";
    const time = data.checked_at ? new Date(data.checked_at * 1000) : null;
    $("balance-time").textContent = time ? `最近查询于 ${time.toLocaleTimeString("zh-CN", { hour12: false })} · 成功结果缓存 ${Number(data.cache_seconds) || 30} 秒` : "尚无查询结果";
    $("announcer").textContent = `余额查询完成，返回 ${rows.length} 个服务结果`;
  } catch (error) {
    if (generation !== state.generation) return;
    setError("balance-error", "余额查询失败，已保留上次结果。请检查服务配置或稍后重试。");
    console.warn("Oven Multi balance request failed", error?.name);
  } finally {
    state.balanceBusy = false;
    $("query-balances").disabled = state.statusBusy || !state.balanceConfigured;
    $("query-balances").querySelector("span").textContent = "查询余额";
    $("panel-balances").setAttribute("aria-busy", "false");
  }
}

async function loadSettings() {
  if (state.settingsBusy) return;
  state.settingsBusy = true;
  try {
    const data = unwrap(await window.AstrBotPluginPage.apiGet("settings"));
    if (!data || !Array.isArray(data.groups)) throw new Error("Invalid settings response");
    state.settingsRevision = data.revision;
    state.settingsDirty = false;
    renderSettings(data.groups);
    $("settings-empty").hidden = data.groups.some((group) => group.fields.length > 0);
    state.settingsLoaded = true;
    setError("settings-error", "");
    loadBiliCard();
  } catch (error) {
    setError("settings-error", "暂时无法读取设置。请确认插件已启用，稍后重试。");
  } finally {
    state.settingsBusy = false;
    $("settings-loading").hidden = true;
    $("save-settings").disabled = !state.settingsDirty;
    updateSettingsFooter();
  }
}

const BILI_STATE_TEXT = {
  disabled: "未启用",
  reused: "复用已有代理",
  external_ok: "外部代理正常",
  external_down: "外部代理不可达",
  spawned: "代理运行中",
  degraded: "守护降级（重启失败）",
  no_node: "缺少 Node.js（需 ≥20）",
  install_failed: "自动安装失败",
  installing: "正在安装",
  starting: "正在启动",
  spawn_failed: "启动失败",
  stopped: "已停止",
};

function renderBiliCard(data) {
  const card = $("bili-card");
  card.hidden = state.settingsCategory !== "上下文压缩";
  const proxy = data.proxy || {};
  const wire = data.wire;
  const stateText = BILI_STATE_TEXT[proxy.state] || proxy.state || "未知";
  let detail = `${proxy.mode === "external" ? "外部" : "托管"} · ${proxy.url || ""}`;
  if (proxy.detail) detail += ` · ${proxy.detail}`;
  if (wire) detail += ` · 已接管 ${wire.providers.join("、")}`;
  else if (data.enabled) detail += " · 未接管";
  $("bili-state").textContent = `上下文压缩：${stateText}`;
  $("bili-detail").textContent = detail;
  const busy = Boolean(data.busy || proxy.busy);
  $("bili-install").hidden = !(data.enabled && proxy.mode !== "external" && !proxy.healthy);
  $("bili-install").disabled = busy;
  $("bili-install").textContent = busy ? "安装／启动中…" : "安装／重试";
  $("bili-wire").hidden = !(data.enabled && !wire);
  $("bili-wire").disabled = busy || !proxy.healthy;
  $("bili-restore").hidden = !wire;
  const doctor = $("bili-doctor");
  doctor.replaceChildren(
    ...(data.doctor || []).map((notice) => {
      const li = node("li", "bili-notice", notice);
      return li;
    }),
  );
}

async function loadBiliCard() {
  try {
    const data = unwrap(await window.AstrBotPluginPage.apiGet("billion/status"));
    if (!data || typeof data !== "object") throw new Error("Invalid bili response");
    renderBiliCard(data);
  } catch (error) {
    console.warn("Oven Multi bili status request failed", error?.name);
  }
}

async function biliAction(kind) {
  if (state.biliBusy) return;
  state.biliBusy = true;
  const label = { wire: "接管", restore: "恢复", install: "安装／重试" }[kind];
  const button = $(`bili-${kind}`);
  button.disabled = true;
  try {
    const data = unwrap(
      await window.AstrBotPluginPage.apiPost(`billion/${kind}`, { confirm: true }),
    );
    $("announcer").textContent = data.message || "操作完成";
    loadStatus();
  } catch (error) {
    setError("settings-error", error.message || "操作失败，请稍后重试。");
  } finally {
    state.biliBusy = false;
    button.disabled = false;
    button.textContent = label;
    button.dataset.armed = "";
    await loadBiliCard();
  }
}

function armBiliButton(kind) {
  const button = $(kind === "wire" ? "bili-wire" : "bili-restore");
  if (button.dataset.armed === "1") {
    biliAction(kind);
    return;
  }
  button.dataset.armed = "1";
  button.textContent = "再次点击确认";
  setTimeout(() => {
    if (button.dataset.armed === "1") {
      button.dataset.armed = "";
      button.textContent = kind === "wire" ? "接管" : "恢复";
    }
  }, 5000);
}

function quantity(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  const scale = abs >= 1e9 ? [1e9, "B"] : abs >= 1e6 ? [1e6, "M"] : abs >= 1e3 ? [1e3, "K"] : [1, ""];
  return (value / scale[0]).toLocaleString("zh-CN", { maximumFractionDigits: scale[0] === 1 ? 0 : 2 }) + scale[1];
}

function percentage(value) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) + "%" : "—";
}

function metric(label, value, note = "") {
  const group = node("div", "metric");
  const number = node("dd", "metric-value", value);
  group.append(node("dt", "", label), number);
  if (note) group.append(node("dd", "metric-note", note));
  return group;
}

function cells(values) {
  const row = node("tr");
  values.forEach(value => row.append(node("td", "", value)));
  return row;
}

function ago(raw) {
  const time = typeof raw === "number" ? raw : Date.parse(raw);
  if (!Number.isFinite(time)) return "—";
  const seconds = Math.max(0, (Date.now() - time) / 1000);
  return seconds < 60 ? "刚刚" : seconds < 3600 ? Math.floor(seconds / 60) + " 分钟前" : seconds < 86400 ? Math.floor(seconds / 3600) + " 小时前" : Math.floor(seconds / 86400) + " 天前";
}

function renderCompression(data) {
  const o = data.overview || {};
  $("compression-content").hidden = false;
  $("compression-empty").hidden = true;
  $("compression-metrics").replaceChildren(
    metric("会话总数", quantity(o.sessions), `${quantity(o.live)} 个运行中，其余为历史会话`),
    metric("请求总数", quantity(o.requests), "引擎记录的全部请求"),
    metric("压缩累计节省", quantity(o.grossSavedTotal), "逐次请求累计的理论节省 token"),
    metric("净节省 token", o.hasFoldData ? quantity(o.netSavedTotal) : "—", o.hasFoldData ? `已扣除回填 ${quantity(o.repayTotal)} 与摘要 ${quantity(o.summaryCostTotal)}` : "暂无可核算的压缩账本"),
    metric("缓存命中率", percentage(o.hitPct), "按实际 usage 加权统计"),
    metric("输入 token", quantity(o.inputTokens)),
    metric("缓存命中 token", quantity(o.cachedTokens)),
    metric("输出 token", quantity(o.outputTokens)),
  );
  const miss = (label, value) => `${label} ${quantity(value)}${o.missInputTotal > 0 && typeof value === "number" ? `（${percentage(value / o.missInputTotal * 100)}）` : ""}`;
  $("compression-misses").textContent = "未命中构成：" + [miss("新增内容", o.missNewTotal), miss("压缩回填", o.missCompTotal), miss("TTL／淘汰／未知", o.missTtlTotal)].join("；") + (o.savedEstimated > 0 ? `。另有历史估算 ${quantity(o.savedEstimated)}，不计入净节省。` : "。");
  const runtime = data.runtime || {};
  $("compression-runtime").textContent = runtime.stale ? "运行版本与磁盘不同，等待代理重启" : "代理运行信息";
  $("compression-system").replaceChildren(
    metric("运行版本", runtime.version || "—", runtime.commit || ""),
    metric("磁盘版本", runtime.diskVersion || "—"),
    metric("进行中请求", quantity(runtime.inFlight)),
    metric("MITM 盲隧道", quantity(runtime.blindTunnels), "CONNECT 后未解密 TLS 的连接数"),
  );
  const protocolRows = (o.byProtocol || []).map(p => cells([p.protocol, quantity(p.sessions), quantity(p.requests), quantity(p.inputTokens), quantity(p.cachedTokens), percentage(p.hitPct), quantity(p.savedNet), quantity(p.folds)]));
  if (!protocolRows.length) protocolRows.push(cells(["暂无协议记录"]));
  $("compression-protocols").replaceChildren(...protocolRows);
  const rows = (data.sessions || []).map(s => {
    const row = node("tr");
    const first = node("td", "session-name");
    const button = node("button", "session-link", s.title || "未命名会话");
    button.type = "button";
    button.addEventListener("click", () => loadCompressionDetail(s.id));
    first.append(button, node("span", "session-id", s.id), node("span", "setting-hint", `${s.live && !s.restored ? "运行中" : "历史"}　${s.protocol || "未知协议"}　${s.clientHint || ""}　${s.upstream || ""}`));
    row.append(first);
    const values = [quantity(s.requests), quantity(s.contextBest?.tokens ?? s.contextTokens), quantity(s.inputTokens), percentage(s.cacheHitPct), quantity(s.netSaved ?? s.tokensSaved), quantity(s.foldCount), `${quantity(s.modelSwitches)} / ${quantity(s.keySwitches)}`, quantity(s.blocks), ago(s.lastSeen)];
    values.forEach(value => row.append(node("td", "", value)));
    row.children[2].title = s.contextBest?.kind || s.contextTokensSource || "引擎提供的当前上下文";
    row.children[4].title = `未命中：新增 ${percentage(s.missDropNew)}；压缩 ${percentage(s.missDropComp)}；TTL／未知 ${percentage(s.missDropTtl)}`;
    row.children[5].title = s.netSaved == null ? "历史本地估算；暂无净节省账本" : "已扣除回填与摘要成本";
    row.children[7].title = `模型变更相关未命中 ${quantity(s.switchMissedTokens)}；密钥变更 ${quantity(s.keySwitchMissedTokens)}；提示词变更 ${quantity(s.promptSwitches)} 次`;
    return row;
  });
  if (!rows.length) rows.push(cells([state.compressionQuery ? "没有匹配的会话，请更换关键词。" : "暂无会话记录，真实请求经过代理后会显示在这里。"]));
  $("compression-sessions").replaceChildren(...rows);
  const pages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 30)));
  $("compression-page").textContent = `共 ${data.total || 0} 个会话，第 ${data.page || 1} / ${pages} 页`;
  $("compression-prev").disabled = data.page <= 1;
  $("compression-next").disabled = data.page >= pages;
  $("compression-time").textContent = `数据更新于 ${new Date(data.checked_at * 1000).toLocaleTimeString("zh-CN", { hour12: false })}。当前页面每 10 秒刷新；缺少测量值时显示「—」。`;
}

async function loadCompression() {
  if (state.compressionBusy) { state.compressionQueued = true; return; }
  state.compressionBusy = true;
  const page = state.compressionPage, query = state.compressionQuery;
  $("refresh-compression").disabled = true;
  $("panel-compression").setAttribute("aria-busy", "true");
  setError("compression-error", "");
  try {
    const data = unwrap(await window.AstrBotPluginPage.apiGet("billion/dashboard", {page, q:query}));
    if (page !== state.compressionPage || query !== state.compressionQuery) return;
    if (!data || typeof data.available !== "boolean") throw new Error("Invalid compression response");
    if (!data.available) {
      $("compression-empty").hidden = false;
      $("compression-empty").textContent = data.reason || "压缩统计暂不可用，请检查代理连接后重试。";
      $("compression-content").hidden = true;
      return;
    }
    renderCompression(data);
    state.compressionLoaded = true;
    if (data.stale) setError("compression-error", (data.reason || "暂时无法刷新") + " 已保留上次数据。");
    if (state.compressionSession) await loadCompressionDetail(state.compressionSession, true);
  } catch (error) {
    setError("compression-error", state.compressionLoaded ? "压缩统计刷新失败，已保留上次数据。请检查代理后重试。" : "无法读取压缩统计，请检查压缩代理是否运行，再点击「刷新数据」。");
  } finally {
    state.compressionBusy = false;
    $("compression-loading").hidden = true;
    $("refresh-compression").disabled = false;
    $("panel-compression").setAttribute("aria-busy", "false");
    if (state.compressionQueued) { state.compressionQueued = false; loadCompression(); }
  }
}

async function loadCompressionDetail(id, refresh = false) {
  state.compressionSession = id;
  const ticket = ++state.compressionDetail;
  $("compression-detail").hidden = false;
  if (!refresh) {
    $("compression-detail-state").textContent = "正在读取会话详情…";
    $("compression-detail-metrics").replaceChildren();
    $("compression-detail").scrollIntoView?.({ behavior: "instant", block: "nearest" });
  }
  try {
    const data = unwrap(await window.AstrBotPluginPage.apiGet("billion/dashboard", {session:id}));
    if (ticket !== state.compressionDetail) return;
    if (!data.available || !data.session) throw new Error(data.reason || "会话详情不可用，请刷新后重试。");
    const s = data.session;
    $("compression-detail-title").textContent = s.title || "未命名会话";
    const checked = data.checked_at ? new Date(data.checked_at * 1000).toLocaleTimeString("zh-CN", {hour12:false}) : "未报告";
    $("compression-detail-state").textContent = `${s.id}。详情采集于 ${checked}；上下文来源：${s.contextBest?.kind || s.contextTokensSource || "未报告"}。${data.stale ? "当前为上次读取结果。" : ""}`;
    $("compression-detail-metrics").replaceChildren(
      metric("当前上下文", quantity(s.contextBest?.tokens ?? s.contextTokens), `窗口上限 ${quantity(s.contextWindow)}`),
      metric("最近计费输入", quantity(s.lastInputTokens), "与引擎估算的当前上下文分别显示"),
      metric("系统提示 token", quantity(s.systemPromptTokens)),
      metric("缓存命中率", percentage(s.cacheHitPct), `命中 ${quantity(s.cachedTokens)} / 输入 ${quantity(s.inputTokens)}`),
      metric("节省 token", quantity(s.netSaved ?? s.tokensSaved), s.netSaved == null ? "本地估算，尚无净节省账本" : "净节省，含回填与摘要成本"),
      metric("压缩次数", quantity(s.foldCount)),
      metric("未命中：新增内容", quantity(s.newContent), percentage(s.missDropNew)),
      metric("未命中：压缩回填", quantity(s.compRepay), percentage(s.missDropComp)),
      metric("未命中：TTL／未知", quantity(s.ttlRepay), percentage(s.missDropTtl)),
      metric("模型切换", quantity(s.modelSwitches), `相关未命中 ${quantity(s.switchMissedTokens)}`),
      metric("密钥切换", quantity(s.keySwitches), `相关未命中 ${quantity(s.keySwitchMissedTokens)}`),
      metric("提示词变更", quantity(s.promptSwitches), `相关未命中 ${quantity(s.promptSwitchMissedTokens)}`),
      metric("内容块", quantity(s.blocks)),
      metric("内容检索", quantity(s.retrieveCalls), `命中 ${quantity(s.retrieveHits)}；未命中 ${quantity(s.retrieveMisses)}`),
    );
  } catch (error) {
    if (ticket === state.compressionDetail) $("compression-detail-state").textContent = refresh ? "详情刷新失败，已保留上次详情。" : error.message || "会话详情读取失败，请重试。";
  }
}

function settingInput(field) {
  if (field.type === "bool") {
    const input = node("input");
    input.type = "checkbox";
    input.checked = Boolean(field.effective_value ?? field.value);
    input.disabled = Boolean(field.readonly);
    input.dataset.key = field.key;
    return input;
  }
  if (field.type === "enum") {
    const select = node("select");
    select.dataset.key = field.key;
    for (const option of field.options || []) {
      const item = node("option", "", option);
      item.value = option;
      if (option === field.value) item.selected = true;
      select.append(item);
    }
    return select;
  }
  if (field.type === "pair") {
    const wrap = node("span", "pair-inputs");
    const values = Array.isArray(field.value) && field.value.length === 2 ? field.value : ["", ""];
    values.forEach((value, index) => {
      const input = node("input");
      input.type = "number";
      input.value = value;
      input.dataset.key = field.key;
      input.dataset.pair = String(index);
      input.min = field.min ?? 0;
      input.max = field.max ?? "";
      input.step = field.step ?? "any";
      input.setAttribute("aria-label", `${field.label} ${index === 0 ? "最小" : "最大"}值`);
      wrap.append(input);
    });
    return wrap;
  }
  if (field.type === "list") {
    const area = node("textarea");
    const items = Array.isArray(field.value) ? field.value : [];
    area.value = items.map((item) => String(item ?? "")).join("\n");
    area.dataset.key = field.key;
    area.rows = 3;
    area.spellcheck = false;
    return area;
  }
  const input = node("input");
  input.value = field.value ?? "";
  input.dataset.key = field.key;
  if (field.type === "int" || field.type === "float") {
    input.type = "number";
    input.min = field.min ?? "";
    input.max = field.max ?? "";
    input.step = field.step ?? (field.type === "int" ? 1 : "any");
  } else {
    input.type = "text";
    input.maxLength = field.max_chars || 100;
    input.spellcheck = false;
  }
  return input;
}

function settingRow(field) {
  const row = node("li", "setting-row" + (field.type === "bool" ? " setting-toggle" : "") + (field.type === "list" ? " setting-wide" : ""));
  const body = node("div");
  const label = node("label", "setting-label", field.label);
  const id = "setting-" + field.key.replaceAll(".", "-");
  label.setAttribute("for", id);
  body.append(label);
  if (field.hint) {
    const hint = node("span", "setting-hint", field.hint);
    hint.id = id + "-hint";
    body.append(hint);
  }
  const input = settingInput(field);
  input.id = id;
  if (field.hint) input.setAttribute("aria-describedby", id + "-hint");
  input.classList.add("setting-input");
  row.append(body, input);
  return row;
}

const SETTINGS_SECTIONS = {
  bracket_matching: "括号补全", repetition: "复读与打断", remove_blank_lines: "空行整理",
  iam_thinking: "思考表情", mention_parser: "成员提及", segment_reply: "分段与节奏",
  jev: "Jev 判读", file_reader: "文件与检索", bili: "压缩代理", blacklist: "使用范围", douyin: "抖音直播通知",
};
const COMMON_SETTINGS = new Set([
  "repetition.repeat_threshold", "remove_blank_lines.max_consecutive_newlines",
  "segment_reply.min_length", "segment_reply.max_length", "segment_reply.delay_type",
  "segment_reply.fixed_delay_seconds", "segment_reply.random_delay_range",
  "jev.active_reply_enabled", "jev.inject_on_mention", "jev.active_reply_threshold", "jev.active_interval_sec", "bili.mode", "bili.auto_follow", "bili.provider_ids",
  "file_reader.preread.enabled", "file_reader.tool.enabled", "file_reader.rag.enabled",
  "douyin.check_interval", "douyin.notify_on_live", "douyin.notify_on_offline", "douyin.notify_with_cover",
]);

function selectSettingsCategory(name, focus = false) {
  state.settingsCategory = name;
  $("bili-card").hidden = name !== "上下文压缩";
  for (const button of $("settings-nav").children) {
    const selected = button.dataset.category === name;
    button.setAttribute("aria-current", selected ? "page" : "false");
    if (selected && focus) button.focus();
  }
  for (const section of $("settings-groups").children) section.hidden = section.dataset.category !== name;
}

function renderSettings(groups) {
  const container = $("settings-groups");
  container.replaceChildren();
  $("settings-nav").replaceChildren();
  for (const group of groups) {
    const button = node("button", "category-button", group.name);
    button.type = "button";
    button.dataset.category = group.name;
    button.addEventListener("click", () => selectSettingsCategory(group.name));
    $("settings-nav").append(button);
    const section = node("section", "settings-group");
    section.dataset.category = group.name;
    section.append(node("h3", "", group.name));
    if (group.note) section.append(node("p", "group-note", group.note));
    const parts = new Map();
    for (const field of group.fields) {
      const prefix = field.key.includes(".") ? field.key.split(".")[0] : "blacklist";
      if (!parts.has(prefix)) parts.set(prefix, []);
      parts.get(prefix).push(field);
    }
    if (parts.size > 1) section.classList.add("settings-group-multi");
    for (const [prefix, fields] of parts) {
      const block = node("section", "settings-block");
      block.append(node("h4", "", SETTINGS_SECTIONS[prefix] || group.name));
      const common = node("ul", "settings-fields");
      const advanced = node("ul", "settings-fields");
      for (const field of fields) {
        const primary = field.key.endsWith(".enabled") && field.key.split(".").length === 2;
        const target = primary || prefix === "blacklist" || COMMON_SETTINGS.has(field.key) ? common : advanced;
        target.append(settingRow(field));
      }
      block.append(common);
      if (advanced.children.length) {
        const details = node("details", "advanced-settings");
        details.append(node("summary", "", `高级参数（${advanced.children.length}）`), advanced);
        block.append(details);
      }
      section.append(block);
    }
    container.append(section);
  }
  selectSettingsCategory(groups.some(g => g.name === state.settingsCategory) ? state.settingsCategory : groups[0]?.name);
}

function updateSettingsFooter() {
  $("settings-state").textContent = state.settingsDirty ? "有未保存的修改" : "与当前配置一致";
}

function collectSettings() {
  const container = $("settings-groups");
  const values = {};
  for (const input of container.querySelectorAll("input[data-key]:not([data-pair]), select[data-key]")) {
    if (input.disabled) continue;
    if (input.type === "checkbox") values[input.dataset.key] = input.checked;
    else if (input.type === "number") values[input.dataset.key] = input.value === "" ? null : Number(input.value);
    else values[input.dataset.key] = input.value;
  }
  for (const area of container.querySelectorAll("textarea[data-key]")) {
    values[area.dataset.key] = area.value
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line !== "");
  }
  const pairs = {};
  for (const input of container.querySelectorAll("input[data-pair]")) {
    (pairs[input.dataset.key] ??= [0, 0])[Number(input.dataset.pair)] =
      input.value === "" ? null : Number(input.value);
  }
  Object.assign(values, pairs);
  return values;
}

async function saveSettings() {
  if (state.settingsBusy || !state.settingsDirty || !state.settingsLoaded) return;
  state.settingsBusy = true;
  $("settings-groups").inert = true;
  $("save-settings").disabled = true;
  $("save-settings").querySelector("span").textContent = "保存中…";
  setError("settings-error", "");
  try {
    const data = unwrap(
      await window.AstrBotPluginPage.apiPost("settings/save", {
        revision: state.settingsRevision,
        values: collectSettings(),
      }),
    );
    state.settingsRevision = data.revision;
    state.settingsDirty = false;
    updateSettingsFooter();
    $("announcer").textContent = "设置已保存";
    loadStatus();
  } catch (error) {
    setError("settings-error", error.message || "保存失败，请稍后重试。");
    if (error.stale) {
      const reload = node("button", "button secondary reload-link", "重新读取设置");
      reload.type = "button";
      reload.addEventListener("click", () => loadSettings());
      $("settings-error").append(reload);
    }
  } finally {
    state.settingsBusy = false;
    $("settings-groups").inert = false;
    $("save-settings").querySelector("span").textContent = "保存修改";
    $("save-settings").disabled = !state.settingsDirty;
  }
}

$("settings-groups").addEventListener("input", () => {
  if (!state.settingsDirty) {
    state.settingsDirty = true;
    updateSettingsFooter();
  }
  $("save-settings").disabled = state.settingsBusy;
});
$("bili-wire").addEventListener("click", () => armBiliButton("wire"));
$("bili-install").addEventListener("click", () => biliAction("install"));
$("bili-restore").addEventListener("click", () => armBiliButton("restore"));
$("save-settings").addEventListener("click", saveSettings);
window.addEventListener("beforeunload", (event) => {
  if (state.settingsDirty) event.preventDefault();
});

for (const button of document.querySelectorAll("[data-tab]")) {
  button.addEventListener("click", () => selectTab(button.dataset.tab));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const index = TAB_ORDER.indexOf(state.tab);
    if (event.key === "Home") selectTab(TAB_ORDER[0], true);
    else if (event.key === "End") selectTab(TAB_ORDER[TAB_ORDER.length - 1], true);
    else if (event.key === "ArrowRight") selectTab(TAB_ORDER[(index + 1) % TAB_ORDER.length], true);
    else selectTab(TAB_ORDER[(index - 1 + TAB_ORDER.length) % TAB_ORDER.length], true);
  });
}
$("refresh").addEventListener("click", loadStatus);
$("query-balances").addEventListener("click", loadBalances);
$("refresh-compression").addEventListener("click", loadCompression);
$("compression-prev").addEventListener("click", () => { state.compressionPage = Math.max(1, state.compressionPage - 1); loadCompression(); });
$("compression-next").addEventListener("click", () => { state.compressionPage += 1; loadCompression(); });
let compressionSearchTimer;
$("compression-search").addEventListener("input", event => {
  clearTimeout(compressionSearchTimer);
  compressionSearchTimer = setTimeout(() => {
    state.compressionQuery = event.target.value.trim();
    state.compressionPage = 1;
    loadCompression();
  }, 350);
});
$("close-compression-detail").addEventListener("click", () => { state.compressionSession = ""; state.compressionDetail += 1; $("compression-detail").hidden = true; });
if (typeof setInterval === "function") setInterval(() => {
  if (state.tab === "compression" && document.visibilityState !== "hidden") loadCompression();
}, 10000);
if (typeof setInterval === "function") setInterval(() => {
  if (state.tab === "settings" && state.settingsCategory === "上下文压缩" && document.visibilityState !== "hidden") loadBiliCard();
}, 3000);

async function start() {
  try {
    if (!window.AstrBotPluginPage) throw new Error("Bridge unavailable");
    await window.AstrBotPluginPage.ready();
    await loadStatus();
  } catch (error) {
    $("feature-loading").hidden = true;
    $("panel-overview").setAttribute("aria-busy", "false");
    $("feature-summary").textContent = "未连接";
    setError("status-error", "未能连接 AstrBot。请从插件卡片的「状态总览」打开此页面，或刷新宿主页面后重试。");
    console.warn("Oven Multi page bridge is unavailable", error?.name);
  }
}
start();
