"""
RAG 核心服务
使用 OpenAI 兼容模式调用通义千问（Qwen API 完全兼容 OpenAI 格式）
文档解析 → 文本切片 → 向量化存入 ChromaDB → 检索 → 大模型生成
"""

import os
import hashlib
from collections import OrderedDict
from datetime import datetime, timedelta
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from PyPDF2 import PdfReader
import dashscope
from http import HTTPStatus
import config
from services.token_tracker import TokenTracker, count_tokens, hash_question
from services.hybrid_search import hybrid_search
from services.agent_tools import TOOLS, TOOL_DESCRIPTIONS


# ========== 初始化 ChromaDB 向量库（持久化模式） ==========
chroma_client = chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)
collection = chroma_client.get_or_create_collection(
    name="campus_knowledge",
    metadata={"description": "校园知识库 — 学生手册、规章制度等"}
)

# ========== 全局变量（支持动态重载） ==========
llm = None
current_system_prompt = ""
current_llm_model = ""
current_api_key = ""
current_api_base = ""


def _embed_text(text: str) -> list[float]:
    """
    文本向量化 — 使用 DashScope 原生 SDK
    直接用通义千问 Embedding API，不走 OpenAI 兼容层
    """
    dashscope.api_key = config.DASHSCOPE_API_KEY
    resp = dashscope.TextEmbedding.call(
        model=config.EMBEDDING_MODEL,
        input=text
    )
    if resp.status_code == HTTPStatus.OK:
        return resp.output["embeddings"][0]["embedding"]
    else:
        raise RuntimeError(f"Embedding 失败: {resp.code} - {resp.message}")


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """
    批量向量化 — DashScope 原生支持多文本一次调用
    将 N 次 API 调用合并为 ceil(N/25) 次，大幅提速
    """
    if not texts:
        return []

    dashscope.api_key = config.DASHSCOPE_API_KEY
    all_vectors = []

    # DashScope Embedding 每批最多 25 条
    batch_size = 25
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        resp = dashscope.TextEmbedding.call(
            model=config.EMBEDDING_MODEL,
            input=batch  # 传入列表，一次调一批
        )
        if resp.status_code == HTTPStatus.OK:
            batch_vectors = [e["embedding"] for e in resp.output["embeddings"]]
            all_vectors.extend(batch_vectors)
        else:
            # 批量失败则降级为逐条
            print(f"[WARN] 批量向量化失败: {resp.code}，降级逐条处理")
            for t in batch:
                all_vectors.append(_embed_text(t))

    return all_vectors


def _init_llm(model_name: str = None, api_key: str = None, api_base: str = None,
              temperature: float = None, max_tokens: int = None):
    """初始化大模型，无 API Key 时安全降级（不崩溃）"""
    global llm, current_llm_model, current_api_key, current_api_base
    current_llm_model = model_name or config.LLM_MODEL
    current_api_key = api_key or config.DASHSCOPE_API_KEY
    current_api_base = api_base or config.LLM_BASE_URL

    # 无有效 API Key 时安全降级
    if not current_api_key or len(current_api_key) < 4:
        llm = None
        print("[WARN] 未配置 API Key，AI 生成不可用，检索功能正常")
        return

    try:
        llm = ChatOpenAI(
            model=current_llm_model,
            api_key=current_api_key,
            base_url=current_api_base,
            temperature=temperature if temperature is not None else config.LLM_TEMPERATURE,
            max_tokens=max_tokens or config.LLM_MAX_TOKENS
        )
        print(f"[OK] LLM 已初始化: {current_llm_model}")
    except Exception as e:
        llm = None
        print(f"[WARN] LLM 初始化失败: {e}，检索功能正常")


def reload_llm(settings: dict):
    """
    动态重载大模型（管理员修改配置后调用）
    用新的 api_key 替换默认 key，后续 embedding 也会用新 key
    """
    global current_system_prompt
    config.DASHSCOPE_API_KEY = settings.get("api_key") or config.DASHSCOPE_API_KEY
    _init_llm(
        model_name=settings.get("model_name"),
        api_key=settings.get("api_key"),
        api_base=settings.get("api_base"),
        temperature=settings.get("temperature"),
        max_tokens=settings.get("max_tokens")
    )
    current_system_prompt = settings.get("system_prompt", "")
    print(f"[OK] 模型已切换 -> {settings.get('model_name')}")


# ========== 启动时初始化 ==========
_init_llm()

# ========== 文本切片器 ==========
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=config.CHUNK_SIZE,
    chunk_overlap=config.CHUNK_OVERLAP,
    separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]
)


# ==================== 问题缓存（LRU 内存缓存） ====================

class QueryCache:
    """
    LRU 内存缓存 — 相同问题 + 相同模型，零成本复用
    键 = MD5(问题原文 + 模型名)，确保精确匹配才命中
    """

    def __init__(self, max_size: int = 500, ttl_hours: int = 24):
        self.max_size = max_size
        self.ttl = timedelta(hours=ttl_hours)
        self._store: OrderedDict = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _make_key(self, question: str, model: str) -> str:
        raw = f"{question.strip()}|{model}"
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, question: str, model: str) -> dict | None:
        """查询缓存，命中返回结果，过期/未命中返回 None"""
        key = self._make_key(question, model)
        if key not in self._store:
            self.misses += 1
            return None

        entry = self._store[key]
        # 检查过期
        if datetime.now() - entry["cached_at"] > self.ttl:
            del self._store[key]
            self.misses += 1
            return None

        # LRU: 移到末尾
        self._store.move_to_end(key)
        self.hits += 1
        entry["cached"] = True
        return entry

    def set(self, question: str, model: str, result: dict):
        """写入缓存"""
        if not config.CACHE_ENABLED:
            return
        key = self._make_key(question, model)
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = {
            **result,
            "cached_at": datetime.now(),
            "cached": False,  # 写入时为 False，命中时改 True
        }
        # 淘汰最老的
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def clear(self):
        """清空缓存（知识库更新时调用）"""
        self._store.clear()
        self.hits = 0
        self.misses = 0

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total * 100, 1) if total > 0 else 0.0

    def stats(self) -> dict:
        return {
            "enabled": config.CACHE_ENABLED,
            "max_size": self.max_size,
            "current_size": self.size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hit_rate,
        }


# 全局缓存实例
query_cache = QueryCache(
    max_size=config.CACHE_MAX_SIZE,
    ttl_hours=config.CACHE_TTL_HOURS
)


# ==================== 文档处理 ====================

def parse_pdf(file_path: str) -> str:
    """解析 PDF 文件，返回纯文本"""
    reader = PdfReader(file_path)
    text_parts = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            text_parts.append(t)
    return "\n".join(text_parts)


def parse_txt(file_path: str) -> str:
    """解析 TXT 文件"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def process_document(file_path: str, file_type: str, doc_title: str) -> int:
    """
    处理上传的文档：解析 → 切片 → 向量化 → 存入 ChromaDB
    返回切片数量
    """
    # 1. 解析文本
    if file_type == "pdf":
        text = parse_pdf(file_path)
    elif file_type == "txt":
        text = parse_txt(file_path)
    else:
        raise ValueError(f"不支持的文件类型: {file_type}")

    if not text or len(text.strip()) < 10:
        raise ValueError("文档内容为空或太短")

    # 2. 文本切片
    chunks = text_splitter.split_text(text)

    # 3. 生成唯一 ID（用于后续删除）
    doc_prefix = doc_title.replace(" ", "_").replace(".", "_")

    # 4. 批量向量化（每批 25 条，大幅减少 API 调用次数）
    if len(chunks) <= 25:
        # 一次批量调用
        vectors = _embed_batch(chunks)
        for i, vector in enumerate(vectors):
            collection.add(
                ids=[f"{doc_prefix}_chunk_{i}"],
                embeddings=[vector],
                documents=[chunks[i]],
                metadatas=[{
                    "doc_title": doc_title,
                    "chunk_index": i,
                    "total_chunks": len(chunks)
                }]
            )
    else:
        # 分批处理
        batch_size = 25
        for batch_start in range(0, len(chunks), batch_size):
            batch_end = min(batch_start + batch_size, len(chunks))
            batch = chunks[batch_start:batch_end]
            vectors = _embed_batch(batch)
            for j, vector in enumerate(vectors):
                i = batch_start + j
                collection.add(
                    ids=[f"{doc_prefix}_chunk_{i}"],
                    embeddings=[vector],
                    documents=[chunks[i]],
                    metadatas=[{
                        "doc_title": doc_title,
                        "chunk_index": i,
                        "total_chunks": len(chunks)
                    }]
                )

    return len(chunks)


def delete_document_vectors(doc_title: str):
    """根据文档标题删除 ChromaDB 中对应向量"""
    doc_prefix = doc_title.replace(" ", "_").replace(".", "_")
    try:
        results = collection.get()
        ids_to_delete = [id for id in results["ids"] if id.startswith(doc_prefix)]
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
    except Exception as e:
        print(f"删除向量失败: {e}")


# ==================== RAG 检索 ====================

def retrieve_context(question: str, top_k: int = None) -> list[dict]:
    """
    检索相关文档片段
    返回: [{content, title, score}, ...]
    """
    if top_k is None:
        top_k = config.TOP_K_RETRIEVAL

    # 1. 将问题向量化（DashScope 原生接口）
    question_vector = _embed_text(question)

    # 2. ChromaDB 相似度检索
    results = collection.query(
        query_embeddings=[question_vector],
        n_results=top_k,
        include=["documents", "metadatas", "distances"]
    )

    sources = []
    if results["documents"] and results["documents"][0]:
        for i in range(len(results["documents"][0])):
            doc_content = results["documents"][0][i]
            metadata = results["metadatas"][0][i] if results["metadatas"] else {}
            distance = results["distances"][0][i] if results["distances"] else 0

            # distance 越小越相似（余弦距离），转为相似度分数
            similarity = 1 / (1 + distance)

            sources.append({
                "content": doc_content,
                "title": metadata.get("doc_title", "未知文档"),
                "score": round(similarity, 3)
            })

    # 3. 过滤低相似度结果
    sources = [s for s in sources if s["score"] >= config.SIMILARITY_THRESHOLD]

    return sources


def get_all_documents_info() -> list[dict]:
    """获取知识库中所有文档的基本信息"""
    try:
        results = collection.get(include=["metadatas"])
        if not results["metadatas"]:
            return []

        doc_map = {}
        for meta in results["metadatas"]:
            title = meta.get("doc_title", "未知")
            if title not in doc_map:
                doc_map[title] = 0
            doc_map[title] += 1

        return [{"title": k, "chunk_count": v} for k, v in doc_map.items()]
    except Exception:
        return []


# ==================== AI 问答生成 ====================

SYSTEM_PROMPT = """你是一个专业的大学校园AI助手，专门为学生解答校园相关问题。你的知识来源于已上传的校园官方文档。

回答规则：
1. 优先使用下面提供的「参考知识」内容回答；如果参考知识不包含相关信息，诚实地告诉用户你目前无法回答这个问题，建议咨询辅导员或查看学校官网
2. 回答要简洁清晰、条理分明，使用友好、亲和力的语气
3. 如果问题涉及敏感内容或违法信息，礼貌地拒绝
4. 回答末尾可以标注信息来源
5. 禁止编造任何不存在于参考知识中的规章制度"""


# ==================== 对话上下文压缩 ====================

COMPRESS_PROMPT = """请将以下对话历史压缩为一段简洁摘要（不超过 {max_tokens} 字）。
只保留关键事实、重要结论和用户偏好，省略过程性描述和寒暄。

对话历史：
{history}

摘要："""


def compress_history(conversation_history: list[dict]) -> list[dict]:
    """
    将长对话历史压缩为摘要 + 最近几轮
    - 估算历史 token 数
    - 未超限 → 直接返回原始历史
    - 超限 → 用便宜模型压缩早期对话，保留最近 N 轮完整
    """
    if not conversation_history or len(conversation_history) <= config.KEEP_RECENT_ROUNDS * 2:
        return conversation_history

    # 估算总 token 数
    full_text = "\n".join([
        f"{'用户' if m.get('role') == 'user' else 'AI'}: {m.get('content', '')}"
        for m in conversation_history
    ])
    total_tokens = count_tokens(full_text)

    if total_tokens < config.MAX_HISTORY_TOKENS:
        return conversation_history  # 无需压缩

    # 分割：最近 N 轮保留，更早的压缩
    split_idx = len(conversation_history) - (config.KEEP_RECENT_ROUNDS * 2)
    old_history = conversation_history[:split_idx]
    recent_history = conversation_history[split_idx:]

    # 用便宜模型生成摘要
    old_text = "\n".join([
        f"{'用户' if m.get('role') == 'user' else 'AI'}: {m.get('content', '')[:300]}"
        for m in old_history
    ])[:4000]  # 最多取 4000 字符

    compress_prompt = COMPRESS_PROMPT.format(
        max_tokens=config.SUMMARY_MAX_TOKENS,
        history=old_text
    )

    try:
        summary_llm = ChatOpenAI(
            model=config.SUMMARY_MODEL,
            api_key=current_api_key or config.DASHSCOPE_API_KEY,
            base_url=current_api_base or config.LLM_BASE_URL,
            temperature=0.3,
            max_tokens=config.SUMMARY_MAX_TOKENS
        )
        summary_msg = summary_llm.invoke([HumanMessage(content=compress_prompt)])
        summary = f"[历史摘要] {summary_msg.content}"
    except Exception:
        # 降级：直接截断早期对话
        summary = f"[历史摘要] 共 {len(old_history)} 条历史消息，因对话过长已省略。"

    # 构建压缩后的历史
    compressed = [{"role": "system", "content": summary}]
    compressed.extend(recent_history)
    return compressed


# ==================== 上下文优化 ====================

def _optimize_sources(sources: list[dict]) -> list[dict]:
    """
    优化检索片段：截断过长内容 + 去除重叠
    """
    if not sources:
        return sources

    optimized = []
    seen_content = set()

    for s in sources:
        content = s.get("content", "")

        # 截断过长内容
        if len(content) > config.MAX_SOURCE_CHARS:
            # 智能截断：尽量在句号处断
            trunc_point = content.rfind("。", 0, config.MAX_SOURCE_CHARS)
            if trunc_point < config.MAX_SOURCE_CHARS // 2:
                trunc_point = config.MAX_SOURCE_CHARS
            content = content[:trunc_point + 1] + "..."

        # 去重：跳过与已选中片段高度重叠的内容
        if config.TRIM_OVERLAP_SOURCES:
            # 用前 80 字符做粗略去重
            fingerprint = content[:80].strip()
            if fingerprint in seen_content:
                continue
            seen_content.add(fingerprint)

        optimized.append({**s, "content": content})

    return optimized


# ==================== AI 追问机制 ====================

# 模糊/需要追问的关键词
VAGUE_PATTERNS = ["那个", "这个", "它", "怎么办", "怎么样", "好不好", "行不行", "能不能", "是什么", "什么意思"]

FOLLOWUP_PROMPT = """你是校园助手。用户问了一个知识库中找不到精确匹配的问题。

用户问题：{question}

知识库中已有的文档主题：奖学金评定、宿舍管理、社团管理、选课指南、学生手册（学籍/考勤/考试/处分）

请基于你的常识，生成一个友好的追问来澄清用户意图（不超过30字）："""


def should_follow_up(question: str, sources: list[dict]) -> bool:
    """判断是否需要 AI 追问"""
    if len(question.strip()) < 6:
        return True
    if not sources:
        return True
    if all(s.get("score", 0) < 0.5 for s in sources):
        return True
    if any(kw in question for kw in VAGUE_PATTERNS):
        return True
    return False


def generate_followup(question: str, sources: list[dict]) -> str:
    """生成追问——用 LLM 常识，不依赖检索结果标题"""
    fp = FOLLOWUP_PROMPT.format(question=question)

    try:
        fu_llm = ChatOpenAI(
            model="qwen-turbo",
            api_key=current_api_key or config.DASHSCOPE_API_KEY,
            base_url=current_api_base or config.LLM_BASE_URL,
            temperature=0.5,
            max_tokens=80
        )
        resp = fu_llm.invoke([HumanMessage(content=fp)])
        return "🤔 " + resp.content.strip()
    except Exception:
        return "🤔 不太确定你想了解什么，能再说具体一点吗？"


# ==================== Agent 路由 ====================

def classify_intent(question: str) -> dict:
    """
    根据问题关键词匹配最合适的 Agent
    返回: {name, emoji, prompt, doc_prefix}
    """
    q = question.lower()

    # 精确匹配关键词
    best_agent = None
    best_score = 0
    for name, cfg in config.AGENTS.items():
        score = sum(1 for kw in cfg.get("keywords", []) if kw in q)
        if score > best_score:
            best_score = score
            best_agent = {"name": name, **cfg}

    # 没匹配到用总助手
    if not best_agent or best_score == 0:
        cfg = config.AGENTS["校园总助手"]
        return {"name": "校园总助手", **cfg}

    return best_agent


def ask_with_agent(question: str, conversation_history: list[dict] = None) -> dict:
    """
    Agent 路由版问答：
    1. 意图分类 → 匹配 Agent
    2. 切换专属模型 + Prompt
    3. 调 RAG 生成 → 恢复原模型
    """
    agent = classify_intent(question)

    # 保存当前模型/ Prompt 状态
    global current_system_prompt, current_llm_model
    saved_prompt = current_system_prompt
    saved_model = current_llm_model

    # 切换 Agent 专属模型 + Prompt
    agent_model = agent.get("model", saved_model)
    if agent_model != current_llm_model:
        _init_llm(model_name=agent_model)
    current_system_prompt = agent["prompt"]

    try:
        result = ask(question, conversation_history)
    finally:
        # 恢复原模型
        if agent_model != saved_model:
            _init_llm(model_name=saved_model)
        current_system_prompt = saved_prompt

    result["agent"] = {"name": agent["name"], "emoji": agent["emoji"], "model": agent_model}
    return result


def ask_stream_with_agent(question: str, conversation_history: list[dict] = None):
    """
    Agent 路由版流式问答
    切换模型 → 专属 Prompt → RAG 流式 → 恢复
    """
    agent = classify_intent(question)

    # 先发 agent 信息（含模型名）
    yield {"type": "agent", "name": agent["name"], "emoji": agent["emoji"], "model": agent.get("model", "")}

    global current_system_prompt, current_llm_model
    saved_prompt = current_system_prompt
    saved_model = current_llm_model

    # 切换 Agent 专属模型
    agent_model = agent.get("model", saved_model)
    if agent_model != current_llm_model:
        _init_llm(model_name=agent_model)
    current_system_prompt = agent["prompt"]

    try:
        for event in ask_stream(question, conversation_history):
            if event["type"] == "done":
                event["agent"] = {"name": agent["name"], "emoji": agent["emoji"], "model": agent_model}
            yield event
    finally:
        if agent_model != saved_model:
            _init_llm(model_name=saved_model)
        current_system_prompt = saved_prompt


# ==================== Think-Act Agent 循环 ====================

AGENT_THINK_PROMPT = """你是校园助手，可以通过多步检索和外部工具来回答用户问题。

【用户问题】：{question}

【你已经检索到的信息】：
{context}

【知识库文档】：奖学金评定细则、学生手册（宿舍/考试/处分/军训/助学贷款）、社团管理办法、选课指南

【外部工具】：
{tools}

请决定下一步（输出一行）：
- SEARCH: <关键词>   → 查知识库
- TOOL: 天气查询 城市名   → 查天气
- TOOL: 网页搜索 关键词   → 搜网页
- ANSWER: <回答>   → 信息够了，生成回答

注意：最多 5 步。"""


def agent_ask(question: str, conversation_history: list[dict] = None) -> dict:
    """
    Think-Act Agent 主循环
    LLM 自主决策：查什么、查几次、何时回答
    """
    max_rounds = 5
    context_parts = []
    search_method = "agent"
    sources_all = []

    # LLM 不可用时直接降级
    if not llm:
        from services.rag_service import ask
        result = ask(question, conversation_history)
        result["search_method"] = "agent-fallback"
        return result

    for round_num in range(max_rounds):
        # ====== Think: LLM 决定下一步 ======
        ctx_text = "\n".join(context_parts) if context_parts else "（尚未检索）"
        think_prompt = AGENT_THINK_PROMPT.format(
            question=question, context=ctx_text, tools=TOOL_DESCRIPTIONS
        )

        try:
            from langchain_core.messages import HumanMessage
            think_msg = llm.invoke([HumanMessage(content=think_prompt)])
            think_result = think_msg.content.strip()
        except Exception:
            think_result = "ANSWER: 抱歉，服务暂时不可用。"

        # ====== Act: 执行 LLM 的决定 ======
        if think_result.startswith("TOOL:") or think_result.startswith("工具:"):
            # 调用外部工具
            tool_text = think_result.split(":", 1)[1].strip() if ":" in think_result else ""
            tool_result = "工具执行失败"

            for tool_name, tool_info in TOOLS.items():
                if tool_name in tool_text:
                    arg = tool_text.replace(tool_name, "").strip()
                    try:
                        tool_result = tool_info["fn"](arg) if arg else tool_info["fn"]()
                    except Exception as e:
                        tool_result = f"工具错误：{e}"
                    break

            context_parts.append(f"🔧 {tool_text} → {tool_result}")

        elif think_result.startswith("SEARCH:") or think_result.startswith("搜索:") or think_result.startswith("search:"):
            keyword = think_result.split(":", 1)[1].strip() if ":" in think_result else question

            # 检索知识库
            srcs = hybrid_search(keyword)
            if not srcs:
                srcs = keyword_search(keyword)

            sources_all.extend(srcs)
            snippets = [f"[{s.get('title','')}] {s.get('content','')[:200]}" for s in srcs[:3]]
            context_parts.append(f"第{round_num + 1}次检索({keyword})：\n" + "\n---\n".join(snippets))

            # 去重
            context_parts = list(dict.fromkeys(context_parts))

        elif think_result.startswith("ANSWER:") or think_result.startswith("回答:"):
            answer = think_result.split(":", 1)[1].strip() if ":" in think_result else think_result

            # 用查到的上下文让 LLM 生成更流式的回答
            try:
                if context_parts:
                    final_prompt = f"基于以下检索到的信息，回答用户问题。\n\n检索信息：\n" + "\n".join(context_parts[-2:]) + f"\n\n用户问题：{question}\n\n请简洁回答："
                    final_msg = llm.invoke([HumanMessage(content=final_prompt)])
                    answer = final_msg.content.strip()
            except Exception:
                pass

            return {
                "answer": answer,
                "sources": sources_all,
                "search_method": f"agent-{round_num + 1}步",
                "llm_available": True,
                "usage": {"rounds": round_num + 1},
                "source_count": len(sources_all),
                "error_msg": "",
            }
        else:
            # LLM 没按格式输出 → 直接当回答
            context_parts.append(think_result)

    # 超过最大轮数 → 强出结果
    if context_parts:
        try:
            final_prompt = f"基于检索信息回答：\n" + "\n".join(context_parts[-2:]) + f"\n\n问题：{question}"
            final_msg = llm.invoke([HumanMessage(content=final_prompt)])
            answer = final_msg.content.strip()
        except Exception:
            answer = "抱歉，处理超时，请换个方式提问。"
    else:
        answer = "抱歉，未找到相关信息。"

    return {
        "answer": answer,
        "sources": sources_all,
        "search_method": f"agent-{max_rounds}步(max)",
        "llm_available": True,
        "usage": {"rounds": max_rounds},
        "source_count": len(sources_all),
        "error_msg": "",
    }


def agent_ask_stream(question: str, conversation_history: list[dict] = None):
    """Agent 流式版 — 先推心跳避免前端超时"""
    import concurrent.futures

    # 先推心跳，防止前端超时断开
    yield {"type": "chunk", "text": "🧠 Agent 思考中...\n"}
    yield {"type": "meta", "sources": [], "search_method": "agent"}

    # 用线程执行 agent_ask，避免阻塞
    result = None
    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(agent_ask, question, conversation_history)
            # 心跳：每 2 秒发一个点
            import time
            dots = 0
            while not future.done():
                time.sleep(2)
                dots += 1
                if dots <= 5:
                    yield {"type": "chunk", "text": "."}
            result = future.result(timeout=30)
    except concurrent.futures.TimeoutError:
        result = {"answer": "⏰ Agent 思考超时，请换个简单的问题试试。", "sources": [], "search_method": "agent-timeout"}
    except Exception as e:
        result = {"answer": f"Agent 执行失败：{str(e)[:100]}", "sources": [], "search_method": "agent-error"}

    # 发送最终结果
    answer = result.get("answer", "抱歉，处理失败。")
    # 如果已经在上面发了部分内容，这里补发完整的
    sources = result.get("sources", [])
    yield {"type": "meta", "sources": sources, "search_method": result.get("search_method", "agent")}
    yield {"type": "chunk", "text": "\n" + answer}
    yield {"type": "done", "source_count": len(sources), "llm_error": ""}


def keyword_search(question: str) -> list[dict]:
    """
    纯关键词检索（embedding 挂了时的兜底方案）
    不依赖任何 API，直接遍历 ChromaDB 文档做字符串匹配
    """
    try:
        results = collection.get(include=["documents", "metadatas"])
        if not results["documents"]:
            return []

        # 分词（简单按空格和标点分词）
        import re
        keywords = [w for w in re.split(r'[\s，。！？；、\n]+', question) if len(w) >= 2]

        matches = []
        for i, doc in enumerate(results["documents"]):
            score = sum(1 for kw in keywords if kw in doc)
            if score > 0:
                metadata = results["metadatas"][i] if results["metadatas"] else {}
                matches.append({
                    "content": doc[:500],  # 截断过长内容
                    "title": metadata.get("doc_title", "未知文档"),
                    "score": round(score / len(keywords), 3) if keywords else 0
                })

        # 按匹配度排序，取前3
        matches.sort(key=lambda x: x["score"], reverse=True)
        return matches[:3]
    except Exception:
        return []


def ask(question: str, conversation_history: list[dict] = None) -> dict:
    """
    RAG 问答主流程（v2 — 缓存 + 压缩 + 上下文优化）
    0. 查缓存 → 命中直接返回
    1. 对话压缩 → 超长历史自动摘要
    2. 检索 → 向量检索（关键词兜底）
    3. 上下文优化 → 截断 + 去重
    4. 构建 Prompt → 调 LLM → 写缓存 → 返回
    """
    model_name = current_llm_model or config.LLM_MODEL

    # ====== 0. 查缓存 ======
    cached = query_cache.get(question, model_name)
    if cached:
        tracker = TokenTracker(model_name=model_name, question=question)
        tracker.cached = True
        tracker.count_prompt_text(question)
        tracker.count_completion(cached["answer"])
        return {
            "answer": cached["answer"],
            "sources": cached["sources"],
            "is_fallback": cached.get("is_fallback", len(cached.get("sources", [])) == 0),
            "search_method": cached.get("search_method", "cache"),
            "llm_available": True,
            "usage": tracker.to_dict(),
            "source_count": len(cached.get("sources", [])),
            "error_msg": "",
        }

    # ====== 1. 对话压缩 ======
    if conversation_history:
        conversation_history = compress_history(conversation_history)

    tracker = TokenTracker(model_name=model_name, question=question)

    search_method = "vector"

    # ====== 2. 检索 ======
    try:
        sources = hybrid_search(question)
        if sources and any(s.get("source") == "hybrid" for s in sources):
            search_method = "hybrid"
        elif sources and any(s.get("source") == "bm25" for s in sources):
            search_method = "bm25_vector"
        else:
            search_method = "vector"
    except Exception:
        sources = keyword_search(question)
        search_method = "keyword"

    # ====== 3. 上下文优化 ======
    sources = _optimize_sources(sources)

    # ====== 追问检测 ======
    max_score = max([s.get("score", 0) for s in sources]) if sources else 0
    if should_follow_up(question, sources) and max_score < 0.6:
        followup = generate_followup(question, sources)
        tracker.count_completion(followup)
        return {
            "answer": followup, "sources": sources,
            "is_fallback": False, "search_method": search_method,
            "llm_available": True, "usage": tracker.to_dict(),
            "source_count": len(sources), "error_msg": "",
            "is_followup": True,
        }

    # ====== 4. 构建 messages ======
    prompt_to_use = current_system_prompt if current_system_prompt else SYSTEM_PROMPT

    if sources:
        # 精简的 Prompt 模板
        context_text = "\n\n---\n\n".join([
            f"【参考 {i+1}】《{s['title']}》\n{s['content']}"
            for i, s in enumerate(sources)
        ])
        user_prompt = f"参考以下知识回答问题。\n\n{context_text}\n\n问题：{question}\n\n回答："
    else:
        user_prompt = f"知识库无相关内容，请根据通用知识回答。\n\n问题：{question}\n\n回答："

    messages = [SystemMessage(content=prompt_to_use)]

    # 注入对话历史（已压缩）
    if conversation_history:
        for msg in conversation_history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "system":
                # 压缩摘要作为系统消息注入
                messages.insert(1, SystemMessage(content=content))
            else:
                messages.append(HumanMessage(content=content))

    messages.append(HumanMessage(content=user_prompt))

    # 估算 prompt tokens
    tracker.count_prompt(messages)

    # ====== 5. 调用大模型 ======
    llm_available = True
    answer = ""
    error_msg = ""
    try:
        response = llm.invoke(messages)
        answer = response.content
        tracker.count_completion(answer)
    except Exception as e:
        llm_available = False
        error_msg = str(e)[:500]
        if sources:
            # 降级但专业：将检索结果结构化展示
            answer = "📚 **以下是根据知识库检索到的相关内容**（" + search_method + "检索 · " + str(len(sources)) + "条结果）\n\n"
            answer += "> ⚠️ AI 生成服务暂不可用，以下为知识库原文摘要。配置 API Key 后可启用 AI 智能回答。\n\n"
            answer += "---\n\n"
            for i, s in enumerate(sources):
                content = s['content'][:300]
                if len(s['content']) > 300:
                    content += "..."
                answer += "**🔖 " + s['title'] + "**（相关度 " + str(s['score']) + "）\n\n" + content + "\n\n---\n\n"
        else:
            answer = "😕 知识库中暂未找到相关内容。\n\n建议：\n- 尝试更换关键词提问\n- 联系管理员补充相关知识文档\n- 访问学校官网获取最新信息"
        tracker.count_completion(answer)

    is_fallback = len(sources) == 0

    result = {
        "answer": answer,
        "sources": sources,
        "is_fallback": is_fallback,
        "search_method": search_method,
        "llm_available": llm_available,
        "usage": tracker.to_dict(),
        "source_count": len(sources),
        "error_msg": error_msg,
    }

    # ====== 6. 写缓存 ======
    if llm_available and answer:
        query_cache.set(question, model_name, {
            "answer": answer,
            "sources": sources,
            "is_fallback": is_fallback,
            "search_method": search_method,
        })

    return result


def ask_stream(question: str, conversation_history: list[dict] = None):
    """
    RAG 流式问答 v2 — 缓存 + 压缩 + 上下文优化
    逐 token 返回，支持 SSE 推送
    """
    model_name = current_llm_model or config.LLM_MODEL

    # ====== 0. 查缓存 ======
    cached = query_cache.get(question, model_name)
    if cached:
        tracker = TokenTracker(model_name=model_name, question=question)
        tracker.cached = True
        tracker.count_prompt_text(question)

        # 先发元数据
        yield {
            "type": "meta",
            "sources": cached.get("sources", []),
            "search_method": cached.get("search_method", "cache")
        }

        # 模拟流式输出缓存内容（分段推送）
        answer = cached["answer"]
        chunk_size = 50
        for i in range(0, len(answer), chunk_size):
            yield {"type": "chunk", "text": answer[i:i + chunk_size]}

        tracker.count_completion(answer)
        yield {
            "type": "done",
            "usage": tracker.to_dict(),
            "source_count": len(cached.get("sources", [])),
            "llm_error": "",
        }
        return

    # ====== 1. 对话压缩 ======
    if conversation_history:
        conversation_history = compress_history(conversation_history)

    # 初始化 Token 追踪器
    tracker = TokenTracker(model_name=model_name, question=question)

    search_method = "vector"

    # ====== 2. 检索（混合检索：Dense + BM25 + RRF 融合）======
    try:
        sources = hybrid_search(question)
        if sources and any(s.get("source") == "hybrid" for s in sources):
            search_method = "hybrid"
        elif sources and any(s.get("source") == "bm25" for s in sources):
            search_method = "bm25_vector"
        else:
            search_method = "vector"
    except Exception:
        sources = keyword_search(question)
        search_method = "keyword"

    # ====== 3. 上下文优化 ======
    sources = _optimize_sources(sources)

    # ====== 追问检测：低质量检索结果时生成追问 ======
    max_score = max([s.get("score", 0) for s in sources]) if sources else 0
    if should_follow_up(question, sources) and max_score < 0.6:
        followup = generate_followup(question, sources)
        tracker.count_completion(followup)
        yield {"type": "meta", "sources": sources, "search_method": search_method}
        yield {"type": "chunk", "text": followup}
        yield {"type": "done", "usage": tracker.to_dict(), "source_count": len(sources),
               "llm_error": "", "is_followup": True}
        return

    # ====== 4. 构建 messages ======
    prompt_to_use = current_system_prompt if current_system_prompt else SYSTEM_PROMPT

    if sources:
        context_text = "\n\n---\n\n".join([
            f"【参考 {i+1}】《{s['title']}》\n{s['content']}"
            for i, s in enumerate(sources)
        ])
        user_prompt = f"参考以下知识回答问题。\n\n{context_text}\n\n问题：{question}\n\n回答："
    else:
        user_prompt = f"知识库无相关内容，请根据通用知识回答。\n\n问题：{question}\n\n回答："

    # 先发检索元数据
    yield {"type": "meta", "sources": sources, "search_method": search_method}

    messages = [SystemMessage(content=prompt_to_use)]
    if conversation_history:
        for msg in conversation_history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "system":
                messages.insert(1, SystemMessage(content=content))
            else:
                messages.append(HumanMessage(content=content))
    messages.append(HumanMessage(content=user_prompt))

    # 估算 prompt tokens
    tracker.count_prompt(messages)

    # ====== 5. 流式调用大模型 ======
    full_text = ""
    llm_error = ""
    try:
        for chunk in llm.stream(messages):
            if chunk.content:
                full_text += chunk.content
                yield {"type": "chunk", "text": chunk.content}
    except Exception as e:
        llm_error = str(e)[:500]
        if sources:
            header = "\n\n📚 **以下是根据知识库检索到的相关内容**（" + search_method + "检索 · " + str(len(sources)) + "条结果）\n\n> ⚠️ AI 生成服务暂不可用，以下为知识库原文。配置 API Key 后可启用 AI 智能回答。\n\n---\n\n"
            full_text += header
            yield {"type": "chunk", "text": header}
            for i, s in enumerate(sources):
                content = s['content'][:300]
                if len(s['content']) > 300:
                    content += "..."
                snippet = "**🔖 " + s['title'] + "**（相关度 " + str(s['score']) + "）\n\n" + content + "\n\n---\n\n"
                full_text += snippet
                yield {"type": "chunk", "text": snippet}
        else:
            err_text = "\n\n😕 知识库中暂未找到相关内容。\n\n建议：\n- 尝试更换关键词提问\n- 联系管理员补充相关知识文档"
            full_text += err_text
            yield {"type": "error", "msg": str(e)}

    # 估算输出 tokens
    tracker.count_completion(full_text)
    usage = tracker.to_dict()

    # ====== 6. 写缓存 ======
    if not llm_error and full_text:
        query_cache.set(question, model_name, {
            "answer": full_text,
            "sources": sources,
            "is_fallback": len(sources) == 0,
            "search_method": search_method,
        })

    # ====== 7. 完成 ======
    yield {
        "type": "done",
        "usage": usage,
        "source_count": len(sources),
        "llm_error": llm_error,
    }
