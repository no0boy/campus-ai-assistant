"""
OpenClaw 多渠道 Webhook — 飞书/钉钉/微信统一入口
"""

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from typing import Optional
import time
import uuid
import os

router = APIRouter(prefix="/api/webhook", tags=["Webhook"])

# 简单 API Key 鉴权（环境变量配置）
WEBHOOK_API_KEY = os.getenv("WEBHOOK_API_KEY", "openclaw-demo-key")


class WebhookRequest(BaseModel):
    question: str
    user_id: Optional[str] = "anonymous"
    platform: Optional[str] = "openclaw"  # feishu / wechat / dingtalk
    stream: Optional[bool] = True


# ====== 内存限流 ======
_rate_records: dict[str, list[float]] = {}
RATE_LIMIT = 30      # 每分钟最多 30 次
RATE_WINDOW = 60     # 窗口 60 秒


def _check_rate(user_id: str) -> bool:
    """检查限流，返回 True=放行 False=限流"""
    now = time.time()
    if user_id not in _rate_records:
        _rate_records[user_id] = []
    # 清理过期记录
    _rate_records[user_id] = [t for t in _rate_records[user_id] if now - t < RATE_WINDOW]
    if len(_rate_records[user_id]) >= RATE_LIMIT:
        return False
    _rate_records[user_id].append(now)
    return True


@router.post("/chat")
async def webhook_chat(req: WebhookRequest, request: Request):
    """OpenClaw 统一消息入口"""
    rid = str(uuid.uuid4())[:8]

    # 鉴权
    auth = request.headers.get("Authorization", "").replace("Bearer ", "")
    if auth != WEBHOOK_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    # 限流
    if not _check_rate(req.user_id):
        raise HTTPException(status_code=429, detail="请求太频繁，稍后再试")

    start = time.time()

    # 调 Agent
    try:
        from services.rag_service import agent_ask, agent_ask_stream
        from fastapi.responses import StreamingResponse
        import json

        if req.stream:
            def generate():
                yield f"data: {json.dumps({'type':'log','rid':rid,'platform':req.platform}, ensure_ascii=False)}\n\n"
                for event in agent_ask_stream(req.question, None):
                    kind = event.get("type", "")
                    if kind in ("chunk", "done", "meta", "agent"):
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                elapsed = int((time.time() - start) * 1000)
                print(f"[WEBHOOK] rid={rid} user={req.user_id} q={req.question[:30]}... time={elapsed}ms")

            return StreamingResponse(generate(), media_type="text/event-stream")

        result = agent_ask(req.question, None)
        elapsed = int((time.time() - start) * 1000)
        print(f"[WEBHOOK] rid={rid} user={req.user_id} q={req.question[:30]}... time={elapsed}ms")

        return {
            "code": 0,
            "data": {
                "answer": result.get("answer", ""),
                "sources": [s["title"] for s in result.get("sources", [])[:5]],
                "search_method": result.get("search_method", "agent"),
                "rid": rid,
            }
        }

    except Exception as e:
        print(f"[WEBHOOK ERROR] rid={rid} {e}")
        raise HTTPException(status_code=500, detail=str(e)[:200])


@router.get("/health")
def webhook_health():
    """Webhook 通道健康检查"""
    return {"status": "ok", "platforms": ["feishu", "wechat", "dingtalk"]}
