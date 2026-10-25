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
  providerId,
  temperature,
  maxTokens,
  mode = 'chat',
  userId,
  conversationId,
  signal,
}) {
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  };
  // Provider 模式下 Key 保存在服务端，浏览器不发送任何 Key
  if (apiKey && !providerId) headers['X-WeCi-API-Key'] = apiKey;

  const body = {
    messages,
    model,
    api_base: apiBase,
    temperature,
    mode,
  };
  if (providerId) body.provider_id = providerId;
  if (typeof maxTokens === 'number') body.max_tokens = maxTokens;
  // Memory 隔离标识：user / workspace / conversation
  if (userId) body.user_id = userId;
  if (conversationId) body.conversation_id = conversationId;

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
   Provider 管理

   公开响应永远不包含 API Key，只包含 has_api_key。
   更新时 api_key 省略（undefined）= 不修改，传 "" = 清除。
   -------------------------------------------------------------------------- */

function providerJsonOptions(method, body) {
  return {
    method,
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  };
}

export async function fetchProviders() {
  const response = await fetch('/api/providers', {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  const data = await response.json();
  return Array.isArray(data.providers) ? data.providers : [];
}

export async function createProvider(payload) {
  const response = await fetch(
    '/api/providers',
    providerJsonOptions('POST', payload),
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function updateProvider(providerId, patch) {
  const response = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}`,
    providerJsonOptions('PATCH', patch),
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function deleteProvider(providerId) {
  const response = await fetch(`/api/providers/${encodeURIComponent(providerId)}`, {
    method: 'DELETE',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function createProviderModel(providerId, payload) {
  const response = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/models`,
    providerJsonOptions('POST', payload),
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function updateProviderModel(providerId, modelId, patch) {
  const response = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}`,
    providerJsonOptions('PATCH', patch),
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function deleteProviderModel(providerId, modelId) {
  const response = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}`,
    { method: 'DELETE', headers: { Accept: 'application/json' } },
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

/** 测试 Provider。始终返回 200 + {ok,message,latency_ms,models,provider_id,...} */
export async function testProvider(providerId, { model, apiKey } = {}) {
  const payload = {};
  if (model) payload.model = model;
  if (apiKey) payload.api_key = apiKey;
  const response = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/test`,
    providerJsonOptions('POST', payload),
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

/** 测试单个模型。始终返回 200 + {ok,message,latency_ms,...} */
export async function testProviderModel(providerId, modelId, { apiKey } = {}) {
  const payload = {};
  if (apiKey) payload.api_key = apiKey;
  const response = await fetch(
    `/api/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}/test`,
    providerJsonOptions('POST', payload),
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

/* --------------------------------------------------------------------------
   Agent Run 管理
   -------------------------------------------------------------------------- */

/** 查询某个会话的 Agent Run 历史（按时间倒序）。 */
export async function fetchRuns(conversationId, limit = 50) {
  const response = await fetch(
    `/api/agent/runs?conversation_id=${encodeURIComponent(conversationId)}&limit=${limit}`,
    { headers: { Accept: 'application/json' } },
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  const data = await response.json();
  return Array.isArray(data.runs) ? data.runs : [];
}

/** 查询单个 Run 的完整记录（含 tool_calls 与 events）。 */
export async function fetchRun(runId) {
  const response = await fetch(`/api/agent/runs/${encodeURIComponent(runId)}`, {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  const data = await response.json();
  return data.run || null;
}

/** 取消正在运行的 Run。返回 { ok, run_id, status }。 */
export async function cancelRun(runId) {
  const response = await fetch(`/api/agent/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
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

/* --------------------------------------------------------------------------
   长期记忆（Memory）
   -------------------------------------------------------------------------- */

function memoryQuery({ userId, workspaceId, conversationId, scope }) {
  const params = new URLSearchParams();
  if (userId) params.set('user_id', userId);
  if (workspaceId) params.set('workspace_id', workspaceId);
  if (conversationId) params.set('conversation_id', conversationId);
  if (scope) params.set('scope', scope);
  return params.toString();
}

export async function fetchMemories(options = {}) {
  const query = memoryQuery(options);
  const response = await fetch(`/api/memories${query ? `?${query}` : ''}`, {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  const data = await response.json();
  return Array.isArray(data.memories) ? data.memories : [];
}

export async function createMemory({ key, content, scope, userId, workspaceId, conversationId }) {
  const response = await fetch('/api/memories', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({
      key,
      content,
      scope: scope || 'user',
      user_id: userId || 'default',
      workspace_id: workspaceId || '',
      conversation_id: conversationId || '',
    }),
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function updateMemory(memoryId, { key, content }) {
  const response = await fetch(`/api/memories/${encodeURIComponent(memoryId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ key, content }),
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function deleteMemory(memoryId) {
  const response = await fetch(`/api/memories/${encodeURIComponent(memoryId)}`, {
    method: 'DELETE',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

/* --------------------------------------------------------------------------
   MCP Server
   -------------------------------------------------------------------------- */

export async function fetchMcpServers() {
  const response = await fetch('/api/mcp/servers', {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  const data = await response.json();
  return Array.isArray(data.servers) ? data.servers : [];
}

export async function createMcpServer({ name, command, args, env, enabled = true }) {
  const response = await fetch('/api/mcp/servers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ name, command, args: args || [], env: env || {}, enabled }),
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function updateMcpServer(serverId, patch) {
  const response = await fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function deleteMcpServer(serverId) {
  const response = await fetch(`/api/mcp/servers/${encodeURIComponent(serverId)}`, {
    method: 'DELETE',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function connectMcpServer(serverId) {
  const response = await fetch(
    `/api/mcp/servers/${encodeURIComponent(serverId)}/connect`,
    { method: 'POST', headers: { Accept: 'application/json' } },
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function disconnectMcpServer(serverId) {
  const response = await fetch(
    `/api/mcp/servers/${encodeURIComponent(serverId)}/disconnect`,
    { method: 'POST', headers: { Accept: 'application/json' } },
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

/* --------------------------------------------------------------------------
   统一权限 / 安全（Permission Manager）

   所有工具（内置 / 插件 / MCP）共用同一套授权；后端返回的数据已脱敏，
   不含任何 API Key。
   -------------------------------------------------------------------------- */

export async function fetchPermissions() {
  const response = await fetch('/api/permissions', {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function grantPermission({
  principal = 'global',
  permission,
  scope = 'always',
  runId = '',
}) {
  const body = { principal, permission, scope };
  if (runId) body.run_id = runId;
  const response = await fetch('/api/permissions/grant', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function denyPermission({ principal = 'global', permission }) {
  const response = await fetch('/api/permissions/deny', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ principal, permission }),
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function resetPermissions({ principal, permission } = {}) {
  const body = {};
  if (principal) body.principal = principal;
  if (permission) body.permission = permission;
  const response = await fetch('/api/permissions/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function fetchPermissionRequests() {
  const response = await fetch('/api/permissions/requests', {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  const data = await response.json();
  return Array.isArray(data.requests) ? data.requests : [];
}

/** 用户对权限请求的响应：once / run / deny。 */
export async function resolvePermissionRequest(requestId, decision) {
  const response = await fetch(
    `/api/permissions/requests/${encodeURIComponent(requestId)}/resolve`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ decision }),
    },
  );
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function fetchSecurityLog(limit = 50) {
  const response = await fetch(`/api/permissions/log?limit=${limit}`, {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  const data = await response.json();
  return Array.isArray(data.log) ? data.log : [];
}

/** 命令执行安全模式：Safe / Auto Review / Risky。 */
export async function fetchSecurity() {
  const response = await fetch('/api/security', {
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}

export async function setSecurityMode(mode) {
  const response = await fetch('/api/security/mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ mode }),
  });
  if (!response.ok) throw new Error(await readErrorDetail(response));
  return response.json();
}
