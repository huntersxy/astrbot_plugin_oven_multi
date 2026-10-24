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
};
const TAB_ORDER = ["overview", "balances", "settings"];

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
}

function renderFeatures(features) {
  const groups = [
    { name: "消息与回复", ids: ["bracket_matching", "repetition", "remove_blank_lines", "iam_thinking", "mention_parser", "segment_reply"] },
    { name: "判读与文件", ids: ["jev", "file_reader"] },
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
  spawn_failed: "启动失败",
  stopped: "已停止",
};

function renderBiliCard(data) {
  const card = $("bili-card");
  card.hidden = false;
  const proxy = data.proxy || {};
  const wire = data.wire;
  const stateText = BILI_STATE_TEXT[proxy.state] || proxy.state || "未知";
  let detail = `${proxy.mode === "external" ? "外部" : "托管"} · ${proxy.url || ""}`;
  if (proxy.detail) detail += ` · ${proxy.detail}`;
  if (wire) detail += ` · 已接管 ${wire.providers.join("、")}`;
  else if (data.enabled) detail += " · 未接管";
  $("bili-state").textContent = `上下文压缩：${stateText}`;
  $("bili-detail").textContent = detail;
  $("bili-wire").hidden = !(data.enabled && !wire);
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
  const label = kind === "wire" ? "接管" : "恢复";
  const button = $(kind === "wire" ? "bili-wire" : "bili-restore");
  button.disabled = true;
  try {
    const data = unwrap(
      await window.AstrBotPluginPage.apiPost(`billion/${kind}`, { confirm: true }),
    );
    $("announcer").textContent = data.message || "操作完成";
    await loadBiliCard();
    loadStatus();
  } catch (error) {
    setError("settings-error", error.message || "操作失败，请稍后重试。");
  } finally {
    state.biliBusy = false;
    button.disabled = false;
    button.textContent = label;
    button.dataset.armed = "";
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
  const row = node("li", "setting-row");
  const body = node("div");
  body.append(node("span", "setting-label", field.label));
  if (field.hint) body.append(node("span", "setting-hint", field.hint));
  const input = settingInput(field);
  input.classList.add("setting-input");
  row.append(body, input);
  return row;
}

function renderSettings(groups) {
  const container = $("settings-groups");
  container.replaceChildren();
  for (const group of groups) {
    const section = node("section", "feature-section settings-group");
    section.append(node("h3", "", group.name));
    if (group.note) section.append(node("p", "group-note", group.note));
    const list = node("ul");
    for (const field of group.fields) list.append(settingRow(field));
    section.append(list);
    container.append(section);
  }
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
