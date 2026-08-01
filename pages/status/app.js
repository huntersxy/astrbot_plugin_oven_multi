const $ = (id) => document.getElementById(id);

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function escAttr(text) {
  return esc(text).replace(/"/g, "&quot;");
}

// AstrBot bridge 会把接口响应解包一层：直接返回 response.data.data。
// 这里兼容「信封 {success,data}」与「已解包数据」两种形态。
function unwrap(payload) {
  if (payload && typeof payload === "object" && "success" in payload && "data" in payload) {
    return payload.data ?? {};
  }
  return payload || {};
}

async function load() {
  try {
    const data = unwrap(await window.AstrBotPluginPage.apiGet("status"));
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
          <details class="session" data-sid="${escAttr(s.session_id)}">
            <summary>
              <b>${esc(s.display_name || s.session_id)}</b>
              <span>通用 ${(s.universal || []).length} · 场景 ${(s.situational || []).length} · 记录 ${(s.history || []).length}</span>
            </summary>
            <div class="body">
              <div class="session-actions">
                <button class="ghost danger" data-act="clear_session" data-sid="${escAttr(s.session_id)}">删除会话</button>
              </div>
              ${list("通用风格", s.universal || [], (t) => `
                <li>${esc(t.content || "")}<i>熟练度 ${t.proficiency ?? "?"}</i>
                  <button class="del" data-act="delete_trait" data-sid="${escAttr(s.session_id)}" data-content="${escAttr(t.content || "")}" title="删除该条">×</button>
                </li>`)}
              ${list("场景化表达", s.situational || [], (t) => `
                <li>${esc(t.content || "")}<i>${esc(t.context || "仅语境匹配时注入")}</i>
                  <button class="del" data-act="delete_trait" data-sid="${escAttr(s.session_id)}" data-content="${escAttr(t.content || "")}" title="删除该条">×</button>
                </li>`)}
              ${list("聊天记录", s.history || [], (m) => `
                <li><i>${esc(m.sender || "?")}</i>${esc(m.content || "")}</li>`)}
            </div>
          </details>`,
        )
        .join("")
    : '<p class="empty">暂无风格学习数据</p>';
}

async function deleteTrait(sessionId, content) {
  try {
    await window.AstrBotPluginPage.apiPost("style/manage", {
      action: "delete_trait",
      session_id: sessionId,
      content,
    });
    await load();
  } catch (err) {
    console.error("删除风格失败:", err);
  }
}

async function clearSession(sessionId) {
  if (!confirm("确认删除该会话的所有学习风格？")) return;
  try {
    await window.AstrBotPluginPage.apiPost("style/manage", {
      action: "clear_session",
      session_id: sessionId,
    });
    await load();
  } catch (err) {
    console.error("删除会话风格失败:", err);
  }
}

async function clearAll() {
  if (!confirm("确认删除所有会话的学习风格？此操作不可恢复。")) return;
  try {
    await window.AstrBotPluginPage.apiPost("style/manage", { action: "clear_all" });
    await load();
  } catch (err) {
    console.error("删除全部风格失败:", err);
  }
}

function list(title, items, fn) {
  return `
    <h3>${title}（${items.length}）</h3>
    <ul class="sublist">${items.map(fn).join("") || '<li class="empty">无</li>'}</ul>`;
}

window.AstrBotPluginPage.ready().then(() => {
  $("refresh").addEventListener("click", load);
  $("clearAll").addEventListener("click", clearAll);
  // 事件委托：单条删除 / 会话删除
  $("style").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    e.stopPropagation();
    const act = btn.dataset.act;
    const sid = btn.dataset.sid;
    if (act === "delete_trait") deleteTrait(sid, btn.dataset.content);
    else if (act === "clear_session") clearSession(sid);
  });
  load();
});
