const $ = (id) => document.getElementById(id);

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

async function load() {
  try {
    const res = await window.AstrBotPluginPage.apiGet("status");
    const data = (res && res.data) || {};
    renderFeatures(data.features || []);
    renderBalance(data.balance || []);
    renderStyle(data.style || {});
  } catch (err) {
    console.error("加载状态失败:", err);
  }
}

function renderFeatures(features) {
  $("features").innerHTML = features
    .map(
      (f) => `
      <li class="${f.enabled ? "on" : "off"}">
        <span class="dot"></span>
        <b>${esc(f.name)}</b>
        ${f.detail ? `<small>${esc(f.detail)}</small>` : ""}
      </li>`,
    )
    .join("");
}

function renderBalance(items) {
  $("balance").innerHTML = items.length
    ? items
        .map(
          (it) => `
          <div class="bal ${it.success ? "ok" : "err"}">
            <span>${esc(it.name)}</span>
            <b>${esc(it.value)}</b>
          </div>`,
        )
        .join("")
    : '<p class="empty">未配置余额查询</p>';
}

function renderStyle(sessions) {
  const entries = Object.values(sessions || {});
  $("style").innerHTML = entries.length
    ? entries
        .map(
          (s) => `
          <details class="session">
            <summary>
              <b>${esc(s.display_name || s.session_id)}</b>
              <span>通用 ${(s.universal || []).length} · 场景 ${(s.situational || []).length} · 记录 ${(s.history || []).length}</span>
            </summary>
            <div class="body">
              ${list("通用风格", s.universal || [], (t) => `
                <li>${esc(t.content || "")}<i>熟练度 ${t.proficiency ?? "?"}</i></li>`)}
              ${list("场景化表达", s.situational || [], (t) => `
                <li>${esc(t.content || "")}<i>${esc(t.context || "仅语境匹配时注入")}</i></li>`)}
              ${list("聊天记录", s.history || [], (m) => `
                <li><i>${esc(m.sender || "?")}</i>${esc(m.content || "")}</li>`)}
            </div>
          </details>`,
        )
        .join("")
    : '<p class="empty">暂无风格学习数据</p>';
}

function list(title, items, fn) {
  return `
    <h3>${title}（${items.length}）</h3>
    <ul class="sublist">${items.map(fn).join("") || '<li class="empty">无</li>'}</ul>`;
}

window.AstrBotPluginPage.ready().then(() => {
  $("refresh").addEventListener("click", load);
  load();
});
