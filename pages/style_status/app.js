const bridge = window.AstrBotPluginPage;

async function init() {
  try {
    await bridge.ready();
    document.getElementById("refreshBtn").addEventListener("click", loadAll);
    document.getElementById("detail-overlay").addEventListener("click", closeDetail);
    document.getElementById("detail-close").addEventListener("click", closeDetail);
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
      // 如果 bridge 解包了响应，result 直接就是数组
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
      // 如果 bridge 解包了响应，result 直接就是 data 对象
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
    const universal = session.universal_count || 0;
    const contextual = session.contextual_count || 0;
    const specific = session.specific_count || 0;
    const history = session.history_count || 0;
    
    return `
      <div class="session-card" data-session-id="${escapeHtml(session.session_id)}">
        <div class="session-header">
          <div class="session-name">${escapeHtml(displayName)}</div>
          <div class="click-hint">点击查看详情 →</div>
        </div>
        <div class="session-stats">
          <span>通用 ${universal}</span>
          <span>情境 ${contextual}</span>
          <span>特定 ${specific}</span>
          <span>记录 ${history}</span>
        </div>
        <div class="session-previews">
          <div class="preview-section">
            <span class="preview-label">通用风格预览</span>
            ${session.universal_preview?.length
              ? session.universal_preview.map(p => `<span class="preview-tag">${escapeHtml(p)}</span>`).join("")
              : '<span class="preview-empty">无</span>'}
          </div>
          ${session.contextual_preview?.length ? `
          <div class="preview-section">
            <span class="preview-label">情境风格预览</span>
            ${session.contextual_preview.map(p => `<span class="preview-tag">${escapeHtml(p)}</span>`).join("")}
          </div>` : ""}
          ${session.specific_preview?.length ? `
          <div class="preview-section">
            <span class="preview-label">特定风格预览</span>
            ${session.specific_preview.map(p => `<span class="preview-tag">${escapeHtml(p)}</span>`).join("")}
          </div>` : ""}
        </div>
      </div>
    `;
  }).join("");
  
  // 为所有会话卡片添加点击事件
  container.querySelectorAll(".session-card").forEach(card => {
    card.addEventListener("click", () => {
      const sessionId = card.dataset.sessionId;
      showSessionDetail(sessionId);
    });
  });
}

// ==================== 会话详情 ====================

async function showSessionDetail(sessionId) {
  const detailPanel = document.getElementById("session-detail");
  const overlay = document.getElementById("detail-overlay");
  const title = document.getElementById("detail-title");
  const body = document.getElementById("detail-body");
  const loading = document.getElementById("detail-loading");
  
  // 显示弹窗
  overlay.classList.add("active");
  detailPanel.classList.add("active");
  loading.style.display = "block";
  body.style.display = "none";
  title.textContent = `加载中...`;
  
  try {
    const result = await bridge.apiGet("session_detail", { session_id: sessionId });
    // 处理可能解包的情况
    const data = result?.success ? result.data : (result?.data || result);
    
    title.textContent = `会话详情 — ${escapeHtml(data.display_name || data.session_id)}`;
    loading.style.display = "none";
    body.style.display = "block";
    renderSessionDetail(body, data);
  } catch (err) {
    console.error("加载会话详情失败:", err);
    loading.style.display = "none";
    body.style.display = "block";
    body.innerHTML = `<div class="error"><p>加载失败: ${escapeHtml(err.message)}</p></div>`;
  }
}

function renderSessionDetail(container, data) {
  const universal = data.universal || [];
  const contextual = data.contextual || [];
  const specific = data.specific || [];
  const history = data.history || [];
  
  let html = "";
  
  // 通用风格
  html += `
    <div class="detail-section">
      <h3 class="detail-section-title">通用风格 <span class="detail-count">${universal.length}</span></h3>
      ${universal.length
        ? `<div class="detail-list">${universal.map(t => `
          <div class="detail-item">
            <p class="detail-content">${escapeHtml(t.content || "")}</p>
            <p class="detail-meta">熟练度: ${t.proficiency ?? "?"} | 确认轮次: ${t.confirmed_rounds ?? "?"}</p>
          </div>`).join("")}</div>`
        : '<p class="detail-empty">暂无通用风格数据</p>'}
    </div>`;
  
  // 情境风格
  html += `
    <div class="detail-section">
      <h3 class="detail-section-title">情境风格 <span class="detail-count">${contextual.length}</span></h3>
      ${contextual.length
        ? `<div class="detail-list">${contextual.map(t => `
          <div class="detail-item">
            <p class="detail-content"><span class="detail-scene">场景:</span> ${escapeHtml(t.scene || "")}</p>
            <p class="detail-content"><span class="detail-behavior">回应:</span> ${escapeHtml(t.behavior || "")}</p>
          </div>`).join("")}</div>`
        : '<p class="detail-empty">暂无情境风格数据</p>'}
    </div>`;
  
  // 特定风格
  html += `
    <div class="detail-section">
      <h3 class="detail-section-title">特定风格 <span class="detail-count">${specific.length}</span></h3>
      ${specific.length
        ? `<div class="detail-list">${specific.map(t => `
          <div class="detail-item">
            <p class="detail-content">${escapeHtml(t.content || "")}</p>
            <p class="detail-meta">触发: ${t.trigger_count ?? "?"} 次 | 正则: ${escapeHtml(t.trigger_regex || "无")}</p>
          </div>`).join("")}</div>`
        : '<p class="detail-empty">暂无特定风格数据</p>'}
    </div>`;
  
  // 聊天记录
  html += `
    <div class="detail-section">
      <h3 class="detail-section-title">聊天记录 <span class="detail-count">${history.length}</span></h3>
      ${history.length
        ? `<div class="detail-list">${[...history].reverse().map(msg => `
          <div class="detail-msg">
            <p class="detail-msg-meta">${escapeHtml(msg.sender || "?")} · ${msg.timestamp ? new Date(msg.timestamp * 1000).toLocaleString("zh-CN") : "?"}</p>
            <p class="detail-msg-content">${escapeHtml(msg.content || "")}</p>
          </div>`).join("")}</div>`
        : '<p class="detail-empty">暂无聊天记录</p>'}
    </div>`;
  
  container.innerHTML = html;
}

function closeDetail() {
  document.getElementById("detail-overlay").classList.remove("active");
  document.getElementById("session-detail").classList.remove("active");
}

// ==================== 工具函数 ====================

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

init();
