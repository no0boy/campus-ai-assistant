/**
 * AI 对话页核心逻辑
 * 管理：消息收发、流式渲染、多轮对话、Markdown 渲染、引用溯源、反馈
 */

// ========== 全局状态 ==========
let currentConversationId = null     // 当前对话 ID（null = 新对话）
let conversations = []               // 对话历史列表 [{id, title, messages:[]}]
let isWaiting = false
let currentAbortController = null
let lastQuestion = ''                // 上一个问题（用于重试）

// ========== 页面初始化 ==========
document.addEventListener('DOMContentLoaded', () => {
  checkAuth()
  initTheme()
  loadUserInfo()
  loadConversations()
  loadTrending()
  checkWelcome()
  loadRecommendations()
  autoResizeTextarea()
  wakeUpBackend()                    // 预热后端
  updateRemainingDisplay()           // 显示剩余次数
})

/** 登录态检查 — 没登录跳回登录页 */
function checkAuth() {
  if (!localStorage.getItem('token')) {
    window.location.href = 'login.html'
  }
}

/** 预热后端 — 页面加载时先 ping，唤醒可能休眠的服务 */
function wakeUpBackend() {
  fetch(BASE_URL + '/health').then(r => r.json()).then(d => {
    if (d.status === 'ok') console.log('[wake] 后端就绪')
  }).catch(() => {
    console.warn('[wake] 后端未响应，将在首次请求时自动重试')
  })
}

// 重试回调：api-v3.js 重试时调用，更新界面状态
window._onRetry = function(attempt, total, delayMs) {
  const msgs = document.querySelectorAll('.msg-bubble.ai .typing-dot')
  if (msgs.length > 0) {
    const msgDiv = msgs[msgs.length - 1].closest('.message')
    if (msgDiv) updateAIStatus(msgDiv, `正在唤醒服务器...（${attempt}/${total}，${Math.round(delayMs/1000)}秒后重试）`)
  }
}

// ========== 主题管理 ==========
function initTheme() {
  const saved = localStorage.getItem('theme') || 'light'
  document.documentElement.setAttribute('data-theme', saved)
}
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme')
  const next = current === 'dark' ? 'light' : 'dark'
  document.documentElement.setAttribute('data-theme', next)
  localStorage.setItem('theme', next)
}

// ========== 用户信息 ==========
function loadUserInfo() {
  try {
    const user = JSON.parse(localStorage.getItem('user') || '{}')
    document.getElementById('sidebarName').textContent = user.username || '用户'
    document.getElementById('sidebarAvatar').textContent = (user.username || 'U')[0].toUpperCase()
  } catch (e) { /* 忽略 */ }
}

// ========== 侧边栏 ==========
function openSidebar() {
  document.getElementById('sidebar').classList.add('open')
  document.getElementById('sidebarOverlay').classList.add('show')
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open')
  document.getElementById('sidebarOverlay').classList.remove('show')
}

// ========== 欢迎流程 & 用户画像 ==========

let userProfile = { grade: '', major: '', profile_complete: 0 }

async function checkWelcome() {
  try {
    const token = localStorage.getItem('token')
    const res = await fetch(BASE_URL + '/api/user/welcome', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` }
    })
    const data = await res.json()
    if (data.code !== 0) return

    userProfile.grade = data.data.grade || ''
    userProfile.major = data.data.major || ''
    userProfile.profile_complete = data.data.profile_complete || 0

    // 更新侧边栏显示年级专业
    const nameEl = document.getElementById('sidebarName')
    if (nameEl) {
      let label = JSON.parse(localStorage.getItem('user') || '{}').username || '用户'
      if (userProfile.grade && userProfile.major) {
        label += ` · ${userProfile.grade} ${userProfile.major}`
      }
      nameEl.textContent = label
    }

    if (data.data.is_new) {
      // 首次登录，显示欢迎引导
      appendMessage('ai', data.data.welcome_msg, [], false)
      document.getElementById('welcomeScreen')?.remove()
      scrollToBottom()
    } else if (data.data.welcome_msg) {
      // 老用户回来，显示记忆
      const welcomeScreen = document.getElementById('welcomeScreen')
      if (welcomeScreen) {
        welcomeScreen.querySelector('h2').textContent = `欢迎回来，${JSON.parse(localStorage.getItem('user') || '{}').username || ''}！`
        welcomeScreen.querySelector('p').textContent = data.data.welcome_msg
      }
    }
  } catch (e) {
    console.error('welcome check failed:', e)
  }
}

/** 从用户消息中解析年级和专业 */
function tryParseProfile(input) {
  if (userProfile.profile_complete) return false

  const text = input.trim()
  // 匹配年级
  const gradeMap = {
    '大一': '大一', '大二': '大二', '大三': '大三', '大四': '大四',
    '研一': '研一', '研二': '研二', '研三': '研三', '研究生': '研究生',
    '1': '大一', '2': '大二', '3': '大三', '4': '大四'
  }
  let grade = ''
  for (const [k, v] of Object.entries(gradeMap)) {
    if (text.includes(k)) { grade = v; break }
  }

  // 匹配专业（简单规则：包含"专业"或"技术"或"工程"等）
  const majorPatterns = ['软件', '计算机', '网络', '电子', '机械', '土木',
    '会计', '金融', '英语', '日语', '设计', '电商', '工商', '大数据', '人工智能']
  let major = userProfile.major || ''
  for (const p of majorPatterns) {
    if (text.includes(p)) { major = p + '技术'; break }
  }
  // 去掉重复的"技术技术"
  major = major.replace('技术技术', '技术')

  if (grade || major) {
    userProfile.grade = grade || userProfile.grade
    userProfile.major = major || userProfile.major
    saveProfile()
    return grade && major  // 两个都收集到才算完成
  }
  return false
}

async function saveProfile() {
  const allDone = userProfile.grade && userProfile.major
  try {
    const token = localStorage.getItem('token')
    await fetch(BASE_URL + '/api/user/profile', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({
        grade: userProfile.grade,
        major: userProfile.major,
        profile_complete: allDone ? 1 : 0
      })
    })
    if (allDone) {
      userProfile.profile_complete = 1
      loadRecommendations()  // 画像完成后刷新推荐
      const nameEl = document.getElementById('sidebarName')
      if (nameEl) {
        const username = JSON.parse(localStorage.getItem('user') || '{}').username || '用户'
        nameEl.textContent = `${username} · ${userProfile.grade} ${userProfile.major}`
      }
    }
  } catch (e) { /* ignore */ }
}

// ========== 对话管理 ==========

/** 加载个性化推荐 */
async function loadRecommendations() {
  const el = document.getElementById('recList')
  if (!el) return
  try {
    const token = localStorage.getItem('token')
    const res = await fetch(BASE_URL + '/api/user/recommendations', {
      headers: { Authorization: `Bearer ${token}` }
    })
    const data = await res.json()
    if (data.code === 0 && data.data.length > 0) {
      document.getElementById('recBox').style.display = 'block'
      el.innerHTML = data.data.map((r, i) => `
        <div class="trending-item" onclick="sendQuick('${escapeHtml(r.question)}')" title="${escapeHtml(r.question)}">
          ${i + 1}. ${escapeHtml(r.question)}
        </div>
      `).join('')
    } else {
      document.getElementById('recBox').style.display = 'none'
    }
  } catch(e) {
    document.getElementById('recBox').style.display = 'none'
  }
}

/** 加载热门推荐 */
async function loadTrending() {
  const el = document.getElementById('trendingList')
  if (!el) return
  try {
    const token = localStorage.getItem('token')
    const res = await fetch(BASE_URL + '/api/stats/dashboard', {
      headers: { Authorization: `Bearer ${token}` }
    })
    const data = await res.json()
    const hot = data.code === 0 ? (data.data?.hot_questions || []) : []
    if (hot.length > 0) {
      el.innerHTML = hot.slice(0, 6).map((q, i) => `
        <div class="trending-item" onclick="sendQuick('${escapeHtml(q.question)}')" title="${escapeHtml(q.question)}">
          ${i + 1}. ${escapeHtml(q.question)}
        </div>
      `).join('')
    } else {
      el.innerHTML = '<div style="font-size:11px;color:var(--text-light);text-align:center;padding:8px;">暂无数据</div>'
    }
  } catch(e) {
    el.innerHTML = '<div style="font-size:11px;color:var(--text-light);text-align:center;padding:8px;">暂无数据</div>'
  }
}

/** 加载本地对话历史（当前用 localStorage，后面改调 API） */
function loadConversations() {
  try {
    conversations = JSON.parse(localStorage.getItem('conversations') || '[]')
  } catch (e) {
    conversations = []
  }
  renderChatList()
}

/** 保存对话到 localStorage */
function saveConversations() {
  localStorage.setItem('conversations', JSON.stringify(conversations))
}

/** 渲染左侧对话列表 */
function renderChatList() {
  const el = document.getElementById('chatList')
  if (conversations.length === 0) {
    el.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-light);font-size:13px;">暂无历史对话</div>'
    return
  }
  el.innerHTML = conversations.map(c => `
    <div class="chat-list-item ${c.id === currentConversationId ? 'active' : ''}"
         onclick="switchConversation('${c.id}')">
      <span class="item-text">${escapeHtml(c.title || '新对话')}</span>
      <button class="item-del" onclick="deleteConversation(event, '${c.id}')">✕</button>
    </div>
  `).join('')
}

/** 删除对话 */
function deleteConversation(event, convId) {
  event.stopPropagation()  // 防止触发 switchConversation
  if (!confirm('确定删除这条对话吗？')) return

  conversations = conversations.filter(c => c.id !== convId)
  saveConversations()

  // 如果删除的是当前对话，回到欢迎页
  if (currentConversationId === convId) {
    newChat()
  }
  renderChatList()
}

/** 停止生成 */
function stopGeneration() {
  if (currentAbortController) {
    currentAbortController.abort()
  }
}

/** 切换停止按钮显示 */
function toggleStopBtn(show) {
  const sendBtn = document.getElementById('sendBtn')
  if (show) {
    sendBtn.textContent = '■'
    sendBtn.onclick = stopGeneration
    sendBtn.style.background = '#ef4444'
  } else {
    sendBtn.textContent = '➤'
    sendBtn.onclick = sendMessage
    sendBtn.style.background = ''
    sendBtn.disabled = false
  }
}

/** 新建对话 */
function newChat() {
  currentConversationId = null
  const messagesEl = document.getElementById('chatMessages')
  messagesEl.innerHTML = `
    <div class="welcome" id="welcomeScreen">
      <div class="welcome-icon">🎓</div>
      <h2>你好！我是校园AI知识平台</h2>
      <p>我可以帮你解答选课、奖助学金、宿舍规定、军训安排等校园相关问题。点击上方快捷问题或直接输入开始提问吧！</p>
    </div>
  `
  renderChatList()
  closeSidebar()
}

/** 切换到某条对话 */
function switchConversation(id) {
  currentConversationId = id
  const conv = conversations.find(c => c.id === id)
  if (!conv) return

  const messagesEl = document.getElementById('chatMessages')
  messagesEl.innerHTML = ''

  conv.messages.forEach(msg => {
    appendMessage(msg.role, msg.content, msg.sources, false)
  })

  renderChatList()
  scrollToBottom()
  closeSidebar()
}

// ========== 发送消息 ==========

/** 发送快捷问题 */
function sendQuick(question) {
  document.getElementById('userInput').value = question
  sendMessage()
}

/** 键盘事件：Enter 发送，Shift+Enter 换行 */
function handleKeyDown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

/** 发送消息主逻辑 */
async function sendMessage() {
  const input = document.getElementById('userInput')
  const question = input.value.trim()
  if (!question || isWaiting) return

  // 清空输入框
  input.value = ''
  input.style.height = 'auto'

  // 隐藏欢迎页
  const welcome = document.getElementById('welcomeScreen')
  if (welcome) welcome.remove()

  // 显示用户消息
  appendMessage('user', question)
  scrollToBottom()

  // 保存问题用于重试
  lastQuestion = question

  // 解析用户画像
  const profileWasIncomplete = !userProfile.profile_complete
  const justCompleted = tryParseProfile(question)
  if (justCompleted && profileWasIncomplete) {
    // 画像刚完成，AI 确认
    setTimeout(() => {
      appendMessage('ai',
        `太好了，已经记住你啦！🎉\n\n` +
        `**${userProfile.grade} ${userProfile.major}** — 我会帮你关注和你最相关的校园信息。\n\n` +
        `有什么想了解的尽管问我～`, [], false)
      scrollToBottom()
    }, 500)
    return
  }

  // ===== ① 优先查离线缓存 =====
  const cached = getCachedAnswer(question)
  if (cached) {
    const aiMsg = appendMessage('ai', '', [], true)
    const aiBubble = aiMsg.querySelector('.msg-bubble.ai')
    aiBubble.innerHTML = renderContent(cached.answer, 'ai')
    updateAIStatus(aiMsg, '⚡ 离线缓存（不计次数）')

    if (cached.sources && cached.sources.length > 0) addSources(aiMsg, cached.sources)
    addCacheActions(aiMsg)
    scrollToBottom()

    // 保存到对话
    saveMessage(question, cached.answer, cached.sources || [])
    if (!currentConversationId) {
      currentConversationId = 'conv_' + Date.now()
      const title = question.length > 20 ? question.slice(0, 20) + '...' : question
      conversations.unshift({ id: currentConversationId, title: title, messages: [] })
    }
    updateCurrentConv(question, cached.answer, cached.sources || [])
    renderChatList()
    updateRemainingDisplay()
    return
  }

  // ===== ② 检查提问次数 =====
  const qCount = getQuestionCount()
  const Q_LIMIT = 5
  if (qCount >= Q_LIMIT) {
    appendMessage('ai',
      '🚫 **今日提问次数已用完**（5/5）\n\n' +
      '> 💡 免费 Demo 每个设备限 5 次新问题。已问过的问题可从缓存中秒回，不计次数。\n\n' +
      '如需重置，请清除浏览器 localStorage 后刷新页面。', [], false)
    scrollToBottom()
    return
  }

  // ===== ③ 调用 Agent API =====
  // 显示加载状态：正在检索
  const aiMsg = appendMessage('ai', '', [], true)
  updateAIStatus(aiMsg, 'Agent 思考中...（剩余 ' + (Q_LIMIT - qCount) + ' 次）')
  updateRemainingDisplay()

  isWaiting = true
  currentAbortController = new AbortController()
  toggleStopBtn(true)  // 显示停止按钮

  try {
    // 统一使用 Agent Think-Act 模式
    let fullAnswer = ''
    let sources = []
    let searchMethod = 'agent'
    let llmAvailable = true

    const res = await apiChatAgent(question, currentConversationId, true, (chunk, answer) => {
      fullAnswer = answer
      updateAIMessage(aiMsg, fullAnswer, false)
      scrollToBottom()
    }, currentAbortController.signal, (agentName, agentEmoji) => {
      const roleEl = aiMsg.querySelector('.msg-role')
      if (roleEl) {
        roleEl.innerHTML = agentEmoji + ' <strong>' + agentName + '</strong> 回答中...'
        roleEl.style.color = 'var(--primary)'
      }
      const card = document.createElement('div')
      card.className = 'agent-card'
      card.innerHTML = `
        <div class="agent-card-icon">${agentEmoji || '🤖'}</div>
        <div class="agent-card-info">
          <div class="agent-card-name">${agentName}</div>
          <div class="agent-card-model">专属Agent · Think-Act 模式</div>
        </div>
      `
      const contentEl = aiMsg.querySelector('.msg-content')
      contentEl.insertBefore(card, contentEl.querySelector('.msg-sources'))
      updateAIStatus(aiMsg, 'Agent 检索中...（剩余 ' + (Q_LIMIT - qCount - 1) + ' 次）')
    })

    if (res && res.code === 0) {
      const d = res.data || res
      sources = d.sources || []
      searchMethod = d.search_method || '语义向量'
      llmAvailable = d.llm_available !== false

      const agentName = (d.agent && d.agent.name) ? (d.agent.emoji || '') + ' ' + d.agent.name : ''
      const agentModel = (d.agent && d.agent.model) ? d.agent.model : ''
      const sourceCount = sources.length

      // Agent 来源卡片
      if (agentName) {
        const agentCard = document.createElement('div')
        agentCard.className = 'agent-card'
        agentCard.innerHTML = `
          <div class="agent-card-icon">${d.agent.emoji || '🤖'}</div>
          <div class="agent-card-info">
            <div class="agent-card-name">${d.agent.name} 回答</div>
            <div class="agent-card-model">${agentModel} · ${searchMethod}检索 · ${sourceCount}条来源</div>
          </div>
        `
        aiMsg.querySelector('.msg-content').insertBefore(agentCard, aiMsg.querySelector('.msg-sources'))
      }

      let statusLine = (sourceCount > 0
        ? sourceCount + '条相关内容（' + searchMethod + '检索）' + (llmAvailable ? '  AI已生成回答' : '  已返回原始内容')
        : '知识库无匹配内容，AI直接回答')
      if (res.aborted) {
        statusLine += ' [已停止]'
        fullAnswer = (fullAnswer || d.answer || '') + '\n\n*[已停止生成]*'
      }
      updateAIStatus(aiMsg, statusLine)

      // 最终渲染 Markdown
      updateAIMessage(aiMsg, fullAnswer || d.answer, true)

      // 只有非空回答才展示来源和操作按钮
      const hasContent = (fullAnswer || d.answer) && (fullAnswer || d.answer).trim().length > 0
      if (hasContent && sourceCount > 0) addSources(aiMsg, sources)
      if (hasContent) addMessageActions(aiMsg)

      if (hasContent) {
        saveMessage(question, fullAnswer || d.answer, sources)
        // 写入离线缓存 — 下次同样问题秒回
        cacheAnswer(question, { answer: fullAnswer || d.answer, sources, search_method: searchMethod })
        // 新问题计数 +1
        incrementQuestionCount()
        updateRemainingDisplay()
        if (!currentConversationId) {
          currentConversationId = 'conv_' + Date.now()
          const title = question.length > 20 ? question.slice(0, 20) + '...' : question
          conversations.unshift({ id: currentConversationId, title: title, messages: [] })
        }
        updateCurrentConv(question, fullAnswer || d.answer, sources)
        renderChatList()
      }
    } else if (!(res && res.aborted)) {
      updateAIMessage(aiMsg, '抱歉，出了点问题：' + ((res && res.message) || '未知错误'), true)
    }
  } catch (err) {
    if (err && err.name === 'AbortError') {
      updateAIMessage(aiMsg, '*[已停止生成]*', true)
    } else {
      updateAIMessage(aiMsg,
        '抱歉，无法连接到服务器。\n\n> 💡 **提示**：免费部署的服务可能处于休眠状态，点击下方按钮重试。', true)
      addRetryButton(aiMsg)
    }
  } finally {
    isWaiting = false
    currentAbortController = null
    toggleStopBtn(false)
    document.getElementById('sendBtn').disabled = false
    document.getElementById('userInput').focus()
  }
}

// ========== 消息渲染 ==========

/**
 * 添加一条消息到聊天区
 * @param {string}  role      - 'user' | 'ai'
 * @param {string}  content   - 消息内容
 * @param {array}   sources   - 引用来源（仅 AI 消息）
 * @param {boolean} isLoading - 是否显示加载动画
 * @returns {HTMLElement}      - 消息 DOM 元素
 */
function appendMessage(role, content, sources = [], isLoading = false) {
  const messagesEl = document.getElementById('chatMessages')

  const msgDiv = document.createElement('div')
  msgDiv.className = 'message'
  msgDiv.setAttribute('data-role', role)

  const avatarEmoji = role === 'user' ? '👤' : '🤖'
  const avatarClass = role === 'user' ? 'user' : 'ai'
  const roleLabel = role === 'user' ? '你' : '校园AI知识平台'

  msgDiv.innerHTML = `
    <div class="msg-avatar ${avatarClass}">${avatarEmoji}</div>
    <div class="msg-content">
      <div class="msg-role">${roleLabel}</div>
      <div class="msg-bubble ${role}">
        ${isLoading
          ? '<div class="typing-dot"><span></span><span></span><span></span></div>'
          : renderContent(content, role)
        }
      </div>
      <div class="msg-sources" style="display:none;"></div>
      <div class="msg-actions" style="display:none;"></div>
    </div>
  `

  messagesEl.appendChild(msgDiv)
  return msgDiv
}

/**
 * 更新 AI 消息内容（流式打字效果）
 * @param {HTMLElement} msgDiv  - 消息 DOM
 * @param {string}      content - 新内容
 * @param {boolean}     final   - 是否为最终结果（true 则渲染 Markdown）
 */
function updateAIMessage(msgDiv, content, final = false) {
  const bubble = msgDiv.querySelector('.msg-bubble.ai')
  if (final) {
    bubble.innerHTML = renderContent(content, 'ai')
  } else {
    // 流式输出时，纯文本显示，不做 Markdown 渲染
    bubble.textContent = content
  }
}

/** 渲染消息内容（用户纯文本，AI 用 Markdown） */
function renderContent(content, role) {
  if (role === 'user') {
    return escapeHtml(content)
  }
  // AI 消息：Markdown 渲染
  try {
    return marked.parse(content)
  } catch (e) {
    return escapeHtml(content)
  }
}

/** 更新 AI 消息的状态文字（如"检索中..."） */
function updateAIStatus(msgDiv, statusText) {
  let statusEl = msgDiv.querySelector('.msg-status')
  if (!statusEl) {
    statusEl = document.createElement('div')
    statusEl.className = 'msg-status'
    const contentEl = msgDiv.querySelector('.msg-content')
    contentEl.insertBefore(statusEl, contentEl.firstChild)
  }
  statusEl.textContent = statusText
  statusEl.style.cssText = 'font-size:11px;color:var(--text-light);margin-bottom:4px;'
}

/** 添加引用来源（默认展开 + 每条来源可查看） */
function addSources(msgDiv, sources) {
  const sourcesEl = msgDiv.querySelector('.msg-sources')
  if (!sources || sources.length === 0) return

  sourcesEl.style.display = 'block'
  sourcesEl.innerHTML = `
    <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:6px;">
      📎 参考来源（${sources.length} 条）
    </div>
    ${sources.map((s, i) => `
      <div class="source-item" style="margin-bottom:6px;">
        <div style="font-weight:500;color:var(--primary);font-size:12px;">
          [${i + 1}] 《${escapeHtml(s.title || '未知文档')}》
          <span style="color:var(--text-light);font-weight:400;">匹配度 ${Math.round((s.score || 0) * 100)}%</span>
        </div>
        <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;line-height:1.5;">
          ${escapeHtml((s.content || '').slice(0, 300))}${(s.content || '').length > 300 ? '...' : ''}
        </div>
      </div>
    `).join('')}
  `
}

/** 添加消息操作按钮（复制 + 赞/踩） */
function addMessageActions(msgDiv) {
  const actionsEl = msgDiv.querySelector('.msg-actions')
  actionsEl.style.display = 'flex'

  const msgId = 'msg_' + Date.now()
  msgDiv.setAttribute('data-msg-id', msgId)

  actionsEl.innerHTML = `
    <button class="msg-action" onclick="copyMessage(this)" title="复制回答">📋</button>
    <button class="msg-action" onclick="feedbackMessage(this, 1)" title="有用">👍</button>
    <button class="msg-action" onclick="feedbackMessage(this, -1)" title="没用">👎</button>
  `
}

/** 添加重试按钮 */
function addRetryButton(msgDiv) {
  const actionsEl = msgDiv.querySelector('.msg-actions')
  actionsEl.style.display = 'flex'
  actionsEl.innerHTML = `
    <button class="msg-action" onclick="retryLastMessage(this)" title="重新发送"
            style="color:#fff;background:var(--primary);padding:6px 16px;border-radius:14px;font-size:13px;font-weight:500;">
      🔄 重试
    </button>
  `
}

/** 重试上一次失败的问题 */
function retryLastMessage(btn) {
  if (!lastQuestion || isWaiting) return
  // 移除失败的 AI 消息
  const msgDiv = btn.closest('.message')
  if (msgDiv) msgDiv.remove()
  // 重新发送
  document.getElementById('userInput').value = lastQuestion
  sendMessage()
}

// ========== 消息操作 ==========

/** 复制 AI 回答 */
function copyMessage(btn) {
  const msgDiv = btn.closest('.message')
  const bubble = msgDiv.querySelector('.msg-bubble.ai')
  const text = bubble.textContent

  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '✅'
    setTimeout(() => { btn.textContent = '📋' }, 1500)
  }).catch(() => {
    // 降级方案
    const textarea = document.createElement('textarea')
    textarea.value = text
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
    btn.textContent = '✅'
    setTimeout(() => { btn.textContent = '📋' }, 1500)
  })
}

/** 提交反馈（赞/踩） */
function feedbackMessage(btn, value) {
  const msgDiv = btn.closest('.message')
  const msgId = msgDiv.getAttribute('data-msg-id')

  // 清除同组其他按钮状态
  const actions = msgDiv.querySelectorAll('.msg-action')
  actions.forEach(a => a.classList.remove('active'))
  btn.classList.add('active')

  // 调用反馈 API（目前后端还没好，先存本地）
  console.log('反馈:', { msgId, value })
  // apiFeedback(currentConversationId, msgId, value)
}

// ========== 对话持久化 ==========

/** 保存消息到当前对话 */
function saveMessage(question, answer, sources) {
  // 当前由 localStorage 管理，后端好了之后改调 API
}

/** 更新当前对话的消息列表 */
function updateCurrentConv(question, answer, sources) {
  let conv = conversations.find(c => c.id === currentConversationId)
  if (!conv) {
    conv = { id: currentConversationId, title: '', messages: [] }
    conversations.unshift(conv)
  }
  conv.messages.push({ role: 'user', content: question })
  conv.messages.push({ role: 'ai', content: answer, sources })
  saveConversations()
}

// ========== 工具函数 ==========

/** 滚动到聊天底部 */
function scrollToBottom() {
  const el = document.getElementById('chatMessages')
  requestAnimationFrame(() => {
    el.scrollTop = el.scrollHeight
  })
}

/** HTML 转义 */
function escapeHtml(str) {
  const div = document.createElement('div')
  div.textContent = str
  return div.innerHTML
}

/** textarea 自动增高 */
function autoResizeTextarea() {
  const ta = document.getElementById('userInput')
  ta.addEventListener('input', () => {
    ta.style.height = 'auto'
    ta.style.height = Math.min(ta.scrollHeight, 150) + 'px'
  })
}

// ========== 提问次数管理（localStorage）==========

function getQuestionCount() {
  try {
    return parseInt(localStorage.getItem('qa_question_count') || '0', 10)
  } catch(e) { return 0 }
}

function incrementQuestionCount() {
  try {
    const count = getQuestionCount()
    localStorage.setItem('qa_question_count', String(count + 1))
    // 同时存个时间戳，24h 后自动重置
    const lastReset = parseInt(localStorage.getItem('qa_count_ts') || '0', 10)
    if (Date.now() - lastReset > 86400000) {
      // 超过 24 小时，重置计数
      localStorage.setItem('qa_question_count', '1')
      localStorage.setItem('qa_count_ts', String(Date.now()))
    } else if (!lastReset) {
      localStorage.setItem('qa_count_ts', String(Date.now()))
    }
    return true
  } catch(e) { return false }
}

/** 缓存消息的操作按钮 */
function addCacheActions(aiMsg) {
  const actionsEl = aiMsg.querySelector('.msg-actions')
  actionsEl.style.display = 'flex'
  actionsEl.innerHTML = `
    <button class="msg-action" onclick="copyMessage(this)" title="复制回答">📋</button>
    <span style="font-size:11px;color:var(--text-light);padding:4px 8px;">
      ⚡ 离线缓存 · 不计次数
    </span>
  `
}

/** 更新剩余次数显示 */
function updateRemainingDisplay() {
  const hint = document.querySelector('.input-hint')
  if (hint) {
    const remaining = Math.max(0, 5 - getQuestionCount())
    hint.textContent = 'Agent Think-Act 模式 · 剩余 ' + remaining + ' 次新问题 · 重复问题缓存秒回'
  }
}

// ========== 退出登录 ==========
function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  localStorage.removeItem('conversations')
  window.location.href = 'login.html'
}
