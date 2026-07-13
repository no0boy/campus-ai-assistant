/**
 * API 请求封装
 * 统一管理所有后端接口请求，处理 Token、错误、超时
 * 后端地址：http://127.0.0.1:8000
 */

// 部署时自动同源，本地开发自动适配端口
const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost'
// 一体部署时前后端同端口，直接空 BASE_URL 即可
const BASE_URL = ''

// ==================== 重试 & 超时工具 ====================

/**
 * 带重试的 fetch 封装
 * 自动重试网络错误（最多 retries 次），每次间隔递增
 */
async function fetchWithRetry(url, options = {}, retries = 5) {
  for (let i = 0; i <= retries; i++) {
    // 如果用户点了停止，不重试
    if (options.signal && options.signal.aborted) throw new DOMException('Aborted', 'AbortError')
    try {
      const response = await fetch(url, options)
      return response
    } catch (error) {
      // 用户主动停止 → 不重试
      if (error.name === 'AbortError') throw error
      // 最后一次重试仍失败 → 抛出
      if (i === retries) throw error
      // 等待递增延迟：2s → 5s → 8s → 12s → 16s（总计约 43s，覆盖 HF 冷启动）
      const delays = [2, 5, 8, 12, 16]
      const delay = (delays[i] || 3) * 1000
      console.warn(`[fetch] 请求失败，${delay/1000}s 后重试 (${i+1}/${retries})...`, error.message)
      // 通知外部
      if (window._onRetry) window._onRetry(i + 1, retries, delay)
      // 可被 abort 中断的等待
      await new Promise((r, reject) => {
        const t = setTimeout(r, delay)
        if (options.signal) {
          options.signal.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('Aborted', 'AbortError')) }, { once: true })
        }
      })
    }
  }
}

/**
 * 通用请求函数
 * @param {string} path   - 接口路径，如 '/api/chat/ask'
 * @param {object} options - 可选配置
 * @returns {Promise}       - 返回响应数据 或 错误对象
 */
async function request(path, options = {}) {
  const url = BASE_URL + path
  const token = localStorage.getItem('token')

  const config = {
    headers: {
      'Content-Type': 'application/json',
      ...(token && { Authorization: `Bearer ${token}` })  // 如果有 token 就带上
    },
    ...options
  }

  try {
    const response = await fetchWithRetry(url, config)

    // 401 未登录，跳转登录页
    if (response.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = 'login.html'
      return null
    }

    const data = await response.json()
    return data
  } catch (error) {
    console.error('请求失败:', error)
    return { code: -1, message: '网络异常，请稍后再试' }
  }
}

// ==================== 认证模块 ====================

/** 学生登录 */
function apiLogin(username, password) {
  return request('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password })
  })
}

/** 学生注册 */
function apiRegister(username, password) {
  return request('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password })
  })
}

// ==================== AI 对话模块 ====================

/**
 * AI 问答（核心接口，支持流式）
 * @param {string}  question       - 用户问题
 * @param {string}  conversationId - 对话ID（多轮对话用）
 * @param {boolean} stream         - 是否流式输出
 * @param {function} onChunk       - 流式回调：每次收到文字块时调用
 */
async function apiChatAsk(question, conversationId = null, stream = true, onChunk = null, abortSignal = null, onAgent = null) {
  // 流式请求
  if (stream && onChunk) {
    const token = localStorage.getItem('token')
    const response = await fetchWithRetry(BASE_URL + '/api/chat/ask', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({
        question,
        conversation_id: conversationId,
        stream: true
      }),
      signal: abortSignal  // 支持中断
    })

    // 逐行读取 SSE 流（支持中断）
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let fullAnswer = ''
    let finalData = null
    let aborted = false

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        const text = decoder.decode(value, { stream: true })
        const lines = text.split('\n')

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue

          try {
            const payload = JSON.parse(line.slice(6))

            if (payload.type === 'agent') {
              finalData = finalData || {}
              finalData.agent = { name: payload.name, emoji: payload.emoji, model: payload.model || '' }
              if (typeof onAgent === 'function') {
                onAgent(payload.name, payload.emoji, payload.model || '')
              }
            } else if (payload.type === 'chunk') {
              fullAnswer += payload.text
              onChunk(payload.text, fullAnswer)
            } else if (payload.type === 'done') {
              finalData = { ...finalData, ...payload }
            }
          } catch (e) {
            const text = line.slice(6)
            if (text && text !== '[DONE]') {
              fullAnswer += text
              onChunk(text, fullAnswer)
            }
          }
        }
      }
    } catch (e) {
      if (e.name === 'AbortError') {
        aborted = true
      } else {
        throw e
      }
    }

    return {
      code: 0,
      aborted: aborted,
      data: {
        answer: fullAnswer,
        sources: finalData ? finalData.sources || [] : [],
        search_method: finalData ? finalData.search_method || '语义向量' : '语义向量',
        llm_available: finalData ? finalData.llm_available !== false : true
      }
    }
  }

  // 普通请求
  return request('/api/chat/ask', {
    method: 'POST',
    body: JSON.stringify({
      question,
      conversation_id: conversationId,
      stream: false
    })
  })
}

/** 获取对话历史列表 */
function apiGetHistory(userId) {
  return request(`/api/chat/history?user_id=${userId}`)
}

/** 获取某条对话详情 */
function apiGetConversation(conversationId) {
  return request(`/api/chat/history/${conversationId}`)
}

/** Agent Think-Act 模式（流式） */
async function apiChatAgent(question, conversationId, stream, onChunk, abortSignal, onAgent) {
  const token = localStorage.getItem('token')
  const response = await fetchWithRetry(BASE_URL + '/api/chat/agent/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ question, stream: true }),
    signal: abortSignal
  })

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let fullAnswer = '', finalData = null

  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break
      const text = decoder.decode(value, { stream: true })
      for (const line of text.split('\n')) {
        if (!line.startsWith('data: ')) continue
        try {
          const payload = JSON.parse(line.slice(6))
          if (payload.type === 'chunk') { fullAnswer += payload.text; onChunk(payload.text, fullAnswer) }
          else if (payload.type === 'done') { finalData = payload }
        } catch(e) {}
      }
    }
  } catch(e) { if (e.name === 'AbortError') return { code: 0, aborted: true } }

  return {
    code: 0,
    data: {
      answer: fullAnswer,
      sources: finalData?.sources || [],
      search_method: finalData?.search_method || 'agent',
    }
  }
}

/** 提交反馈（赞/踩） */
function apiFeedback(conversationId, messageId, feedback) {
  return request('/api/chat/feedback', {
    method: 'POST',
    body: JSON.stringify({
      conversation_id: conversationId,
      message_id: messageId,
      feedback  // 1 赞 / -1 踩 / 0 取消
    })
  })
}

// ==================== Agent Think-Act 模式 ====================

async function apiChatAgent(question, conversationId, stream, onChunk, abortSignal, onAgent) {
  const token = localStorage.getItem('token')
  const response = await fetchWithRetry(BASE_URL + '/api/chat/agent/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify({ question, stream: true }),
    signal: abortSignal
  })
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let fullAnswer = '', finalData = null

  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break
      for (const line of decoder.decode(value, { stream: true }).split('\n')) {
        if (!line.startsWith('data: ')) continue
        try {
          const p = JSON.parse(line.slice(6))
          if (p.type === 'chunk') { fullAnswer += p.text; onChunk(p.text, fullAnswer) }
          else if (p.type === 'done') { finalData = p }
        } catch(e) {}
      }
    }
  } catch(e) { if (e.name === 'AbortError') return { code: 0, aborted: true } }

  return { code: 0, data: { answer: fullAnswer, sources: finalData?.sources || [], search_method: finalData?.search_method || 'agent' } }
}

// ==================== 文档模块 ====================

/** 获取知识库文档列表（学生端只读） */
function apiGetDocuments() {
  return request('/api/documents')
}

// ==================== 用户模块 ====================

/** 获取当前用户信息 */
function apiGetProfile() {
  return request('/api/user/profile')
}

/** 更新用户信息 */
function apiUpdateProfile(data) {
  return request('/api/user/profile', {
    method: 'PUT',
    body: JSON.stringify(data)
  })
}
