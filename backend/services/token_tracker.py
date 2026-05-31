"""
Token 估算与成本追踪
- 使用 tiktoken 本地估算 token 数（不额外调用 API）
- 支持主流模型定价匹配
- 封装计时 + 计数 + 成本计算
"""

import hashlib
import time
import tiktoken
import config

# ========== 编码器（cl100k_base 对大部分 LLM 估算误差 < 5%）==========
try:
    ENCODING = tiktoken.get_encoding("cl100k_base")
except Exception:
    ENCODING = None  # tiktoken 不可用时降级为字符数估算


def count_tokens(text: str) -> int:
    """估算文本的 token 数，失败则用字符数/2 粗略估计"""
    if ENCODING:
        try:
            return len(ENCODING.encode(text))
        except Exception:
            pass
    # 降级：中文 ~1.5 字符/token，英文 ~4 字符/token，取平均 ~2
    return max(1, len(text) // 2)


def estimate_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    根据模型名和 token 数估算成本（元）
    支持精确匹配 + 模糊匹配（如 qwen-plus-latest 匹配 qwen-plus）
    """
    pricing = None

    # 精确匹配
    if model_name in config.MODEL_PRICING:
        pricing = config.MODEL_PRICING[model_name]
    else:
        # 模糊匹配：按前缀/包含匹配
        for key in sorted(config.MODEL_PRICING.keys(), key=len, reverse=True):
            if key in model_name or model_name in key:
                pricing = config.MODEL_PRICING[key]
                break

    if not pricing:
        # 未知模型，用默认价格
        pricing = {"input": 0.001, "output": 0.002}

    cost = (prompt_tokens / 1000) * pricing["input"] + \
           (completion_tokens / 1000) * pricing["output"]
    return round(cost, 6)


def hash_question(question: str) -> str:
    """生成问题的短哈希（用于去重/缓存）"""
    return hashlib.md5(question.strip().encode()).hexdigest()[:16]


class TokenTracker:
    """
    单次 LLM 调用的追踪器
    同时记录 token 数、响应时间、成本
    """

    def __init__(self, model_name: str, question: str):
        self.model_name = model_name
        self.question_hash = hash_question(question)
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.start_time = time.time()
        self.end_time = None
        self.cached = False

    def count_prompt(self, messages: list) -> int:
        """从 LangChain messages 列表估算输入 token 数"""
        total = 0
        for msg in messages:
            content = ""
            if hasattr(msg, 'content'):
                content = str(msg.content)
            elif isinstance(msg, dict):
                content = str(msg.get('content', ''))
            total += count_tokens(content)
        # 加上消息结构开销（每条消息约 4 token）
        total += len(messages) * 4
        self.prompt_tokens = total
        return total

    def count_prompt_text(self, *texts: str) -> int:
        """从原始文本估算输入 token 数"""
        total = 0
        for t in texts:
            total += count_tokens(str(t))
        self.prompt_tokens = total
        return total

    def count_completion(self, text: str) -> int:
        """估算输出 token 数"""
        self.completion_tokens = count_tokens(text)
        self.end_time = time.time()
        return self.completion_tokens

    def get_elapsed_ms(self) -> int:
        """获取已用时间（毫秒）"""
        end = self.end_time or time.time()
        return int((end - self.start_time) * 1000)

    def get_cost(self) -> float:
        """计算本次调用成本"""
        return estimate_cost(self.model_name, self.prompt_tokens, self.completion_tokens)

    def get_model_name(self) -> str:
        """获取实际使用的模型名"""
        return self.model_name

    def to_dict(self) -> dict:
        """导出为字典，方便写入数据库"""
        return {
            "model_name": self.model_name,
            "question_hash": self.question_hash,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "cost": self.get_cost(),
            "response_time_ms": self.get_elapsed_ms(),
            "cached": self.cached,
        }
