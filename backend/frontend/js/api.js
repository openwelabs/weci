/* ==========================================================================
   WeCi · 后端 API 封装

   只负责与本地 FastAPI 后端通信：
     - GET  /api/config            读取服务端默认配置
     - POST /api/chat              SSE 流式对话（async generator）
     - POST /api/test-connection   测试模型服务连通性
   ========================================================================== */

async function readErrorDetail(response) {
  try {
    const data = await response.json();
    if (data && typeof data.detail === 'string') return data.detail;
    if (data && Array.isArray(data.detail) && data.detail.length) {
      return data.detail.map((item) => item.msg || '参数错误').join('；');
    }
    if (data && typeof data.message === 'string') return data.message;
  } catch {
    /* 非 JSON 响应 */
  }
  return `请求失败（HTTP ${response.status}）`;
}

export async function fetchServerConfig() {
  const response = await fetch('/api/config', {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function parseSseBlock(block) {
  const events = [];
  block.split(/\r?\n/).forEach((line) => {
    if (!line.startsWith('data:')) return;
    const data = line.slice(5).trim();
    if (!data) return;
    try {
      const event = JSON.parse(data);
      if (event && typeof event === 'object') events.push(event);
    } catch {
      /* 忽略无法解析的行 */
    }
  });
  return events;
}

/**
 * 流式对话。以 async generator 逐条产出后端标准化事件：
 *   {type:'start'} / {type:'delta', text} / {type:'error', message} / {type:'done'}
 * HTTP 层错误直接抛出 Error；调用方负责中断（AbortSignal）与展示。
 */
export async function* streamChat({
  messages,
  model,
  apiBase,
  apiKey,
  temperature,
  maxTokens,
  mode = 'chat',
  signal,
}) {
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  };
  if (apiKey) headers['X-WeCi-API-Key'] = apiKey;

  const body = {
    messages,
    model,
    api_base: apiBase,
    temperature,
    mode,
  };
  if (typeof maxTokens === 'number') body.max_tokens = maxTokens;

  const response = await fetch('/api/chat', {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  if (!response.body) {
    throw new Error('当前浏览器不支持流式响应。');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf('\n\n');
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        for (const event of parseSseBlock(block)) yield event;
        boundary = buffer.indexOf('\n\n');
      }
    }
    if (buffer.trim()) {
      for (const event of parseSseBlock(buffer)) yield event;
    }
  } finally {
    try {
      reader.cancel();
    } catch {
      /* 忽略 */
    }
  }
}

/**
 * 测试模型服务连通性。后端始终返回 200 + 结构化结果：
 *   { ok, message, latency_ms, models: string[] | null }
 */
export async function testConnection({ apiBase, model, apiKey }) {
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  };
  if (apiKey) headers['X-WeCi-API-Key'] = apiKey;

  const response = await fetch('/api/test-connection', {
    method: 'POST',
    headers,
    body: JSON.stringify({ api_base: apiBase, model }),
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  return response.json();
}

/* --------------------------------------------------------------------------
   插件管理
   -------------------------------------------------------------------------- */

export async function fetchPlugins() {
  const response = await fetch('/api/plugins', { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  return Array.isArray(data.plugins) ? data.plugins : [];
}

export async function enablePlugin(pluginId) {
  const response = await fetch(`/api/plugins/${encodeURIComponent(pluginId)}/enable`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function disablePlugin(pluginId) {
  const response = await fetch(`/api/plugins/${encodeURIComponent(pluginId)}/disable`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function removePlugin(pluginId) {
  const response = await fetch(`/api/plugins/${encodeURIComponent(pluginId)}`, {
    method: 'DELETE',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}
