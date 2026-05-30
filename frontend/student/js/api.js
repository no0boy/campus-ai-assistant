/**
 * API 请求封装
 * 统一管理所有后端接口请求，处理 Token、错误、超时
 * 后端地址：http://127.0.0.1:8000
 */

// 部署时自动同源，本地开发指向 localhost
const isLocal = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost'
const BASE_URL = isLocal ? 'http://127.0.0.1:8001' : ''

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
    const response = await fetch(url, config)

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
async function apiChatAsk(question, conversationId = null, stream = true, onChunk = null, abortSignal = null) {
  // 流式请求
  if (stream && onChunk) {
    const token = localStorage.getItem('token')
    const response = await fetch(BASE_URL + '/api/chat/ask', {
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

            if (payload.type === 'chunk') {
              fullAnswer += payload.text
              onChunk(payload.text, fullAnswer)
            } else if (payload.type === 'done') {
              finalData = payload
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
