"""
RAG 核心服务
使用 OpenAI 兼容模式调用通义千问（Qwen API 完全兼容 OpenAI 格式）
文档解析 → 文本切片 → 向量化存入 ChromaDB → 检索 → 大模型生成
"""

import os
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from PyPDF2 import PdfReader
import dashscope
from http import HTTPStatus
import config


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
    """批量向量化"""
    return [_embed_text(t) for t in texts]


def _init_llm(model_name: str = None, api_key: str = None, api_base: str = None,
              temperature: float = None, max_tokens: int = None):
    """初始化大模型"""
    global llm, current_llm_model, current_api_key, current_api_base
    current_llm_model = model_name or config.LLM_MODEL
    current_api_key = api_key or config.DASHSCOPE_API_KEY
    current_api_base = api_base or config.LLM_BASE_URL
    llm = ChatOpenAI(
        model=current_llm_model,
        api_key=current_api_key,
        base_url=current_api_base,
        temperature=temperature if temperature is not None else config.LLM_TEMPERATURE,
        max_tokens=max_tokens or config.LLM_MAX_TOKENS
    )


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

    # 4. 逐片向量化并存入 ChromaDB
    for i, chunk in enumerate(chunks):
        try:
            # 使用 DashScope 原生接口进行向量化
            vector = _embed_text(chunk)

            collection.add(
                ids=[f"{doc_prefix}_chunk_{i}"],
                embeddings=[vector],
                documents=[chunk],
                metadatas=[{
                    "doc_title": doc_title,
                    "chunk_index": i,
                    "total_chunks": len(chunks)
                }]
            )
        except Exception as e:
            print(f"向量化失败 chunk {i}: {e}")

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
    RAG 问答主流程
    1. 检索相关知识（向量检索 → 关键词检索兜底）
    2. 拼接 Prompt
    3. 调用大模型生成回答（失败则返回原文）

    返回: {answer, sources, is_fallback, search_method}
    """
    search_method = "vector"

    # 1. 检索：先向量检索，失败则关键词检索兜底
    try:
        sources = retrieve_context(question)
    except Exception:
        sources = keyword_search(question)
        search_method = "keyword"

    # 2. 构建 messages
    prompt_to_use = current_system_prompt if current_system_prompt else SYSTEM_PROMPT

    if sources:
        # 模式1：有知识库内容 → RAG 增强回答
        context_text = "\n\n---\n\n".join([
            f"【参考知识 {i+1}】来源：《{s['title']}》\n{s['content']}"
            for i, s in enumerate(sources)
        ])
        user_prompt = f"""请根据以下参考知识回答用户的问题。

{context_text}

用户问题：{question}

请回答："""
    else:
        # 模式2：知识库无相关内容 → 大模型直接回答（不限制范围）
        user_prompt = f"""用户问了以下问题。知识库中暂时没有找到相关内容。
请根据你的通用知识直接回答。如果你不确定答案，请诚实告知。

用户问题：{question}

请回答："""

    messages = [SystemMessage(content=prompt_to_use)]

    # 注入历史对话（最近 5 轮）
    if conversation_history:
        for msg in conversation_history[-10:]:
            if msg.get("role") == "user":
                messages.append(HumanMessage(content=msg["content"]))
            else:
                messages.append(SystemMessage(content=msg["content"]))

    messages.append(HumanMessage(content=user_prompt))

    # 3. 调用大模型（失败则返回原始检索结果）
    llm_available = True
    try:
        response = llm.invoke(messages)
        answer = response.content
    except Exception as e:
        llm_available = False
        if sources:
            # 大模型挂了但检索有结果 → 直接返回检索到的原文
            answer = "【注意：AI 模型暂时不可用，以下为知识库检索到的原始内容（" + search_method + "检索）】\n\n"
            for i, s in enumerate(sources):
                answer += "---\n【来源：" + s['title'] + "】（匹配度：" + str(s['score']) + "）\n" + s['content'] + "\n"
        else:
            answer = "抱歉，AI 服务和知识库检索均不可用。请稍后重试或联系管理员。"

    # 6. 判断状态
    is_fallback = len(sources) == 0

    return {
        "answer": answer,
        "sources": sources,
        "is_fallback": is_fallback,
        "search_method": search_method,
        "llm_available": llm_available
    }


def ask_stream(question: str, conversation_history: list[dict] = None):
    """
    RAG 流式问答 — 逐 token 返回，支持 SSE 推送
    """
    search_method = "vector"

    # 1. 检索
    try:
        sources = retrieve_context(question)
    except Exception:
        sources = keyword_search(question)
        search_method = "keyword"

    # 2. 先发检索结果元数据
    yield {"type": "meta", "sources": sources, "search_method": search_method}

    # 3. 构建 messages
    prompt_to_use = current_system_prompt if current_system_prompt else SYSTEM_PROMPT

    if sources:
        context_text = "\n\n---\n\n".join([
            f"【参考知识 {i+1}】来源：《{s['title']}》\n{s['content']}"
            for i, s in enumerate(sources)
        ])
        user_prompt = f"请根据以下参考知识回答用户的问题。\n\n{context_text}\n\n用户问题：{question}\n\n请回答："
    else:
        user_prompt = f"知识库中暂时没有找到相关内容。请根据你的通用知识直接回答。\n\n用户问题：{question}\n\n请回答："

    messages = [SystemMessage(content=prompt_to_use)]
    if conversation_history:
        for msg in conversation_history[-10:]:
            if msg.get("role") == "user":
                messages.append(HumanMessage(content=msg["content"]))
            else:
                messages.append(SystemMessage(content=msg["content"]))
    messages.append(HumanMessage(content=user_prompt))

    # 4. 流式调用大模型
    try:
        for chunk in llm.stream(messages):
            if chunk.content:
                yield {"type": "chunk", "text": chunk.content}
    except Exception as e:
        # 降级：返回原始检索结果
        if sources:
            yield {"type": "chunk", "text": "\n\n【AI模型暂时不可用，以下为检索到的原始内容】\n\n"}
            for s in sources:
                yield {"type": "chunk", "text": "\n---\n" + s['title'] + "\n" + s['content']}
        else:
            yield {"type": "error", "msg": str(e)}

    # 5. 完成
    yield {"type": "done"}
