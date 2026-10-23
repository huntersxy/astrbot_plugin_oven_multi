"use strict";

const $ = (id) => document.getElementById(id);
const state = { statusBusy: false, balanceBusy: false, balanceConfigured: false, tab: "overview", hasStatus: false, generation: 0 };

function unwrap(payload) {
  if (payload?.success === false || payload?.status === "error") throw new Error("API response failed");
  return payload && "success" in payload && "data" in payload ? payload.data : payload;
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
}

function renderFeatures(features) {
  const groups = [
    { name: "消息与回复", ids: ["bracket_matching", "repetition", "remove_blank_lines", "iam_thinking", "mention_parser"] },
    { name: "判读与文件", ids: ["active_reply", "jev", "file_reader"] },
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

for (const button of document.querySelectorAll("[data-tab]")) {
  button.addEventListener("click", () => selectTab(button.dataset.tab));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    selectTab(event.key === "Home" ? "overview" : event.key === "End" ? "balances" : state.tab === "overview" ? "balances" : "overview", true);
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
