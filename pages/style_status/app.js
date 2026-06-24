const bridge = window.AstrBotPluginPage;

async function init() {
  try {
    const ctx = await bridge.ready();
    console.log("[调试] bridge 就绪, context:", JSON.stringify(ctx, null, 2));
    document.getElementById("refreshBtn").addEventListener("click", loadAll);
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
    console.log("[调试] balance apiGet 返回:", JSON.stringify(result, null, 2));
    console.log("[调试] result.success:", result?.success, "result.data:", result?.data);
    
    if (result && result.success && result.data && result.data.length > 0) {
      renderBalance(result.data);
    } else if (Array.isArray(result) && result.length > 0) {
      // 如果 bridge 解包了响应，result 直接就是数组
      console.log("[调试] result 是数组，直接渲染");
      renderBalance(result);
    } else {
      console.warn("[调试] 条件不满足，显示错误提示");
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
    console.log("[调试] style_status apiGet 返回:", JSON.stringify(result, null, 2));
    
    if (result && result.success && Object.keys(result.data || {}).length > 0) {
      renderStyle(result.data);
    } else if (result && typeof result === "object" && !Array.isArray(result) && Object.keys(result).length > 0) {
      // 如果 bridge 解包了响应，result 直接就是 data 对象
      console.log("[调试] result 是裸对象，直接渲染");
      renderStyle(result);
    } else {
      console.warn("[调试] 风格学习条件不满足", result);
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
      <div class="session-card">
        <div class="session-header">
          <div class="session-name">${escapeHtml(displayName)}</div>
          <div class="session-stats">
            <span>通用 ${universal}</span>
            <span>情境 ${contextual}</span>
            <span>特定 ${specific}</span>
            <span>记录 ${history}</span>
          </div>
        </div>
      </div>
    `;
  }).join("");
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text || "";
  return div.innerHTML;
}

init();
