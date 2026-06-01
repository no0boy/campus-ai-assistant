# 校园 AI 助手 — 代码流程架构文档

## 文件结构速览

```
backend/
├── main.py                          # FastAPI 入口，启动初始化
├── config.py                        # 全局配置 + Agent 定义 + 模型定价
├── database.py                      # SQLAlchemy 数据模型（4 张表）
├── routes/
│   ├── auth.py                      # 登录/注册，JWT 签发与验证
│   ├── chat.py                      # 核心问答接口（普通/Agent/流式）
│   ├── documents.py                 # 文档上传/列表/删除/预览/统计
│   ├── stats.py                     # 运营看板数据 API
│   ├── usage.py                     # Token 消耗 + 缓存统计 API
│   ├── user.py                      # 用户画像 + 推荐引擎
│   └── settings.py                  # 模型配置热切换
├── services/
│   ├── rag_service.py               # ★ 核心：RAG全流程 + Agent + 缓存 + 压缩
│   ├── hybrid_search.py             # 混合检索：Dense + BM25 + RRF
│   └── token_tracker.py             # Token 估算 + 成本计算

frontend/
├── student/
│   ├── login.html                   # 登录页（两个大按钮）
│   ├── index.html                   # 聊天主页
│   ├── css/style.css
│   └── js/
│       ├── api.js                   # 前端 API 封装
│       └── chat.js                  # 聊天核心逻辑
├── admin/
│   ├── login.html
│   ├── index.html                   # 运营看板（Chart.js）
│   ├── documents.html               # 知识库管理
│   └── settings.html                # 模型配置
```

---

## 一、用户提问 → AI 回答 全流程

```
浏览器                                    后端
──────                                   ────
1. 用户输入"宿舍几点关门"
2. chat.js sendMessage()
   → apiChatAsk(question, ...)
                                        3. FastAPI 路由到 chat.py
                                           → POST /api/chat/ask

                                        4. 解析 JWT Token，获取当前用户

                                        5. ★ ask_with_agent(question, history)
                                           ├── classify_intent("宿舍几点关门")
                                           │   → 关键词匹配 → 命中 🏠 宿舍生活助手
                                           │
                                           ├── 保存当前模型/提示词状态
                                           ├── _init_llm("qwen3.6-plus")   # 切模型
                                           ├── 切换 system_prompt 为宿舍助手专属
                                           │
                                           ├── ask(question, history)
                                           │   ├── [查缓存] query_cache.get()
                                           │   ├── [压缩] compress_history() 超长则摘要
                                           │   ├── [检索] hybrid_search()
                                           │   │   ├── retrieve_context() 向量检索
                                           │   │   ├── bm25_search() 关键词检索
                                           │   │   └── RRF 融合排序 → Top-5
                                           │   ├── [优化] _optimize_sources() 截断去重
                                           │   ├── [追问] should_follow_up() 模糊则反问
                                           │   ├── [构建] SystemMessage + HumanMessage
                                           │   ├── [追踪] TokenTracker.count_prompt()
                                           │   ├── [调用] llm.invoke(messages) → answer
                                           │   ├── [追踪] TokenTracker.count_completion()
                                           │   └── [缓存] query_cache.set()
                                           │
                                           ├── 恢复原模型/提示词
                                           └── 返回 {answer, sources, agent}
                                        
                                        6. 保存对话到 Conversation 表
                                        7. 写入 UsageLog（Token/时间/成本）
                                        8. 累加文档 access_count
                                        9. 更新用户记忆 _update_memory()

3'. SSE流式：同上逻辑，但 ask_stream() 用 yield 逐token推送
   前端逐字渲染
```

---

## 二、RAG 检索管道（rag_service.py）

```
文档上传 → process_document()
  ├── parse_pdf() / parse_txt()        解析文本
  ├── text_splitter.split_text()       切片（500字/片，50字重叠）
  ├── _embed_batch(texts)              批量向量化（25条/批）
  └── collection.add()                 存入 ChromaDB

用户提问 → hybrid_search(question)
  ├── [Dense路] retrieve_context()
  │   ├── _embed_text(question)        问题向量化
  │   ├── collection.query()           ChromaDB 相似度检索
  │   └── 1/(1+distance) → score      余弦距离转相似度
  ├── [Sparse路] bm25_search()
  │   ├── _tokenize(question)          jieba 中文分词
  │   └── _bm25.get_scores()           BM25 关键词打分
  └── [RRF融合]
      ├── rrf = 1/(60+rank_d) + 1/(60+rank_s)
      └── 按 RRF 排序 → Top-5
```

---

## 三、多 Agent 架构

### Agent 定义（config.py AGENTS 字典）

```python
"宿舍生活助手": {
    "keywords": ["宿舍","熄灯","关门",...],   # 关键词匹配
    "emoji": "🏠",                           # 前端图标
    "model": "qwen3.6-plus",                 # 专属模型
    "prompt": "你是校园生活助手..."            # 专属提示词
}
```

### Agent 路由流程

```
ask_with_agent(question)
  → classify_intent(question)
    → 遍历 AGENTS，统计 question 中匹配的 keywords 数量
    → 返回最高匹配的 Agent（未匹配则用 校园总助手）

  → 保存当前模型/提示词状态
  → _init_llm(agent.model)     # 切换到 Agent 专属模型
  → system_prompt = agent.prompt
  → ask(question)               # 调 RAG 核心流程
  → 恢复原模型/提示词
  → 返回 result + {agent.name, agent.emoji}
```

### Agent Think-Act 循环（agent_ask）

```
agent_ask(question)
  for round in 1..3:
    ① Think: LLM 决定下一步
       → "SEARCH: 宿舍管理规定" 或 "ANSWER: 宿舍11点关门"
    ② Act: 执行决定
       → SEARCH → hybrid_search(keyword) → 加入上下文
       → ANSWER → 生成最终回答 → return
  超 3 轮 → 强制输出
```

---

## 四、Token 成本控制

### TokenTracker（token_tracker.py）

```python
tracker = TokenTracker(model_name, question)
tracker.count_prompt(messages)      # tiktoken 估算输入
tracker.count_completion(answer)    # tiktoken 估算输出
cost = estimate_cost(model, prompt_tokens, completion_tokens)
# cost = prompt/1000 * price_in + completion/1000 * price_out
```

价格表在 config.py MODEL_PRICING，覆盖 Qwen/Claude/DeepSeek/Gemini/Ollama 共 15 种。

### QueryCache（rag_service.py）

- OrderedDict LRU，最大 500 条
- Key = MD5(question + model)
- 24h 过期自动淘汰
- 命中 → 标记 cached=True → 写入 usage_log

### compress_history()

- 计算对话历史 token 数
- 超 3000 token → 用 qwen-turbo 生成摘要
- 保留最近 2 轮完整对话

---

## 五、数据库表结构

### users
```
id | username | password | role | grade | major | interests |
profile_complete | memory_summary | memory_updated_at | created_at
```

### conversations
```
id | user_id | conversation_id | question | answer | sources(JSON) |
feedback(0/1/-1) | created_at
```

### documents
```
id | title | file_path | file_type | chunk_count | access_count |
uploader_id | created_at
```

### usage_logs
```
id | user_id | username | question | question_hash | model_name |
prompt_tokens | completion_tokens | total_tokens | cost |
response_time_ms | search_method | source_count | cached | success |
error_msg | created_at
```

---

## 六、前端架构

### 学生端页面流

```
login.html → 两个大按钮 → quickLogin() → apiLogin() → 存 token → 跳转
    ↓
index.html (聊天页)
    ├── 左侧栏：对话列表 + 🎯为你推荐 + 💡热门推荐
    ├── 中间：聊天消息区
    └── 底部：输入框 + 🔧Agent开关 + 发送按钮

chat.js 核心函数调用链：
  sendMessage()
    ├── tryParseProfile()      画像采集
    ├── agentMode ? apiChatAgent() : apiChatAsk()
    │   └── SSE 流式解析 → onChunk 逐字渲染
    ├── updateAIMessage()      打字效果
    └── addSources()           引用来源显示
```

### 管理后台页面流

```
login.html → admin/admin123 → 运营看板 index.html
    ├── 6 卡片 + 4 图表 (Chart.js)
    └── 每 60s 自动刷新

documents.html（知识库管理）
    ├── 上传 → process_document() → 切片 → 向量化
    ├── 列表（含引用次数）→ 搜索 → 预览切片 → 删除
    └── 批量删除

settings.html（模型配置）
    └── 管理员切换模型/参数 → reload_llm() 热生效
```

---

## 七、安全设计

```
API Key 保护：
  config.py           → .gitignore 排除（本地）
  config.example.py   → GitHub 公开模板（空 Key）
  HF Space Secrets    → 云端加密注入（DASHSCOPE_API_KEY）
  api.txt             → 已删除

JWT Token：
  72h 过期 | HS256 签名 | Bearer 头传递

密码：
  SHA256(secret_key + password)，生产建议改 bcrypt
```
