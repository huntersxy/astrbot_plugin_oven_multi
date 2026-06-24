const bridge = window.AstrBotPluginPage;

async function init() {
  try {
    await bridge.ready();
    document.getElementById("refreshBtn").addEventListener("click", loadAll);
    // 事件委托：点击会话卡片头部展开/折叠
    document.getElementById("style-list").addEventListener("click", e => {
      const header = e.target.closest(".session-card-header");
      if (header) toggleSession(header);
    });
    await loadAll();
  } catch (err) {
    console.error("初始化失败:", err);
  }
}

async function loadAll() {
  await Promise.all([loadBalance(), loadStyle()]);
}

// ==================== 余额查询 ====================

async function loadBalance() {
  const loading = document.getElementById("balance-loading");
  const content = document.getElementById("balance-content");
  const error = document.getElementById("balance-error");
  
  content.style.display = "block";
  error.style.display = "none";
  
  try {
    const result = await bridge.apiGet("balance");
    if (result && result.success && result.data && result.data.length > 0) {
      renderBalance(result.data);
    } else if (Array.isArray(result) && result.length > 0) {
      renderBalance(result);
    } else {
      error.style.display = "block";
    }
  } catch (err) {
    error.style.display = "block";
    console.error("余额查询失败:", err);
  }
  loading.style.display = "none";
}

function renderBalance(data) {
  const container = document.getElementById("balance-list");
  container.innerHTML = data.map(item => `
    <div class="balance-card ${item.success ? '' : 'error'}">
      <div class="balance-name">${escapeHtml(item.name)}</div>
      <div class="balance-value">${escapeHtml(item.value)}</div>
    </div>
  `).join("");
}

// ==================== 风格学习 ====================

async function loadStyle() {
  const loading = document.getElementById("style-loading");
  const content = document.getElementById("style-content");
  const empty = document.getElementById("style-empty");
  
  content.style.display = "block";
  empty.style.display = "none";
  
  try {
    const result = await bridge.apiGet("style_status");
    if (result && result.success && Object.keys(result.data || {}).length > 0) {
      renderStyle(result.data);
    } else if (result && typeof result === "object" && !Array.isArray(result) && Object.keys(result).length > 0) {
      renderStyle(result);
    } else {
      empty.style.display = "block";
    }
  } catch (err) {
    console.error("风格数据加载失败:", err);
  }
  loading.style.display = "none";
}

function renderStyle(data) {
  const container = document.getElementById("style-list");
  const entries = Object.values(data);
  
  container.innerHTML = entries.map(session => {
    const displayName = session.display_name || session.session_id;
    const universal = session.universal || [];
    const contextual = session.contextual || [];
    const specific = session.specific || [];
    const history = session.history || [];
    
    return `
      <div class="session-card">
        <div class="session-card-header">
          <div class="session-header-left">
            <div class="session-name">${escapeHtml(displayName)}</div>
            <div class="session-stats">
              <span>通用 ${universal.length}</span>
              <span>情境 ${contextual.length}</span>
              <span>特定 ${specific.length}</span>
              <span>记录 ${history.length}</span>
            </div>
          </div>
          <span class="expand-icon">▶</span>
        </div>
        <div class="session-card-body" style="display: none;">
          ${renderSection("通用风格", universal, item => `
            <div class="detail-item">
              <p class="detail-content">${escapeHtml(item.content || "")}</p>
              <p class="detail-meta">熟练度: ${item.proficiency ?? "?"} | 确认轮次: ${item.confirmed_rounds ?? "?"}</p>
            </div>`)}
          ${renderSection("情境风格", contextual, item => `
            <div class="detail-item">
              <p class="detail-content"><span class="detail-scene">场景:</span> ${escapeHtml(item.scene || "")}</p>
              <p class="detail-content"><span class="detail-behavior">回应:</span> ${escapeHtml(item.behavior || "")}</p>
            </div>`)}
          ${renderSection("特定风格", specific, item => `
            <div class="detail-item">
              <p class="detail-content">${escapeHtml(item.content || "")}</p>
              <p class="detail-meta">触发: ${item.trigger_count ?? "?"} 次 | 正则: ${escapeHtml(item.trigger_regex || "无")}</p>
            </div>`)}
          ${renderSection("聊天记录", history, msg => `
            <div class="detail-msg">
              <p class="detail-msg-meta">${escapeHtml(msg.sender || "?")} · ${msg.timestamp ? new Date(msg.timestamp * 1000).toLocaleString("zh-CN") : "?"}</p>
              <p class="detail-msg-content">${escapeHtml(msg.content || "")}</p>
            </div>`, true)}
        </div>
      </div>
    `;
  }).join("");
}

function renderSection(title, items, renderFn, reverseOrder) {
  const data = reverseOrder && items.length ? [...items].reverse() : items;
  return `
    <div class="detail-section">
      <h3 class="detail-section-title">${title} <span class="detail-count">${items.length}</span></h3>
      ${data.length
        ? `<div class="detail-list">${data.map(renderFn).join("")}</div>`
        : '<p class="detail-empty">无</p>'}
    </div>`;
}

function toggleSession(headerEl) {
  const card = headerEl.closest(".session-card");
  const body = card.querySelector(".session-card-body");
  const icon = headerEl.querySelector(".expand-icon");
  
  if (body.style.display === "none") {
    body.style.display = "block";
    icon.textContent = "▼";
    card.classList.add("expanded");
  } else {
    body.style.display = "none";
    icon.textContent = "▶";
    card.classList.remove("expanded");
  }
}

// ==================== 工具函数 ====================

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

init();
