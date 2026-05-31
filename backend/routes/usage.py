"""
Token 消耗统计模块 — 查询接口（供后台数据看板使用）
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from datetime import datetime, timedelta

from routes.auth import get_current_user
from database import get_db, User, UsageLog
from services.rag_service import query_cache
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/usage", tags=["Token统计"])


def _get_model_name():
    """获取当前使用的模型名（从最新记录推断）"""
    # 由 rag_service 的 current_llm_model 决定，这里只做展示
    return ""


@router.get("/summary")
def usage_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Token 消耗总览
    返回：总调用次数、总 Token、总成本、今日/本周数据
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())

    # 总计
    total_calls = db.query(func.count(UsageLog.id)).scalar() or 0
    total_tokens = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0)).scalar()
    total_cost = db.query(func.coalesce(func.sum(UsageLog.cost), 0.0)).scalar()

    # 今日
    today_calls = db.query(func.count(UsageLog.id))\
        .filter(UsageLog.created_at >= today_start).scalar() or 0
    today_tokens = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0))\
        .filter(UsageLog.created_at >= today_start).scalar()
    today_cost = db.query(func.coalesce(func.sum(UsageLog.cost), 0.0))\
        .filter(UsageLog.created_at >= today_start).scalar()

    # 本周
    week_calls = db.query(func.count(UsageLog.id))\
        .filter(UsageLog.created_at >= week_start).scalar() or 0
    week_tokens = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0))\
        .filter(UsageLog.created_at >= week_start).scalar()
    week_cost = db.query(func.coalesce(func.sum(UsageLog.cost), 0.0))\
        .filter(UsageLog.created_at >= week_start).scalar()

    # 平均响应时间
    avg_response = db.query(func.coalesce(func.avg(UsageLog.response_time_ms), 0))\
        .filter(UsageLog.response_time_ms > 0).scalar()

    # 成功率
    success_count = db.query(func.count(UsageLog.id))\
        .filter(UsageLog.success == 1).scalar() or 0
    success_rate = round(success_count / total_calls * 100, 1) if total_calls > 0 else 0

    return {
        "code": 0,
        "data": {
            "total": {
                "calls": total_calls,
                "tokens": int(total_tokens),
                "cost": round(float(total_cost), 4),
                "avg_response_ms": int(avg_response),
                "success_rate": success_rate,
            },
            "today": {
                "calls": today_calls,
                "tokens": int(today_tokens),
                "cost": round(float(today_cost), 4),
            },
            "week": {
                "calls": week_calls,
                "tokens": int(week_tokens),
                "cost": round(float(week_cost), 4),
            },
        }
    }


@router.get("/trend")
def usage_trend(days: int = 7, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Token 消耗趋势（按日聚合）
    """
    start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)

    rows = db.query(
        func.date(UsageLog.created_at).label("day"),
        func.count(UsageLog.id).label("calls"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(UsageLog.cost), 0.0).label("cost"),
        func.coalesce(func.avg(UsageLog.response_time_ms), 0).label("avg_response"),
    ).filter(
        UsageLog.created_at >= start_date
    ).group_by(
        func.date(UsageLog.created_at)
    ).order_by(
        func.date(UsageLog.created_at)
    ).all()

    trend = []
    for row in rows:
        trend.append({
            "day": str(row.day),
            "calls": row.calls,
            "tokens": int(row.tokens),
            "cost": round(float(row.cost), 4),
            "avg_response_ms": int(row.avg_response),
        })

    return {"code": 0, "data": trend}


@router.get("/by-model")
def usage_by_model(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    按模型维度的使用统计
    """
    rows = db.query(
        UsageLog.model_name,
        func.count(UsageLog.id).label("calls"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(UsageLog.cost), 0.0).label("cost"),
        func.coalesce(func.avg(UsageLog.response_time_ms), 0).label("avg_response"),
    ).group_by(
        UsageLog.model_name
    ).order_by(
        func.count(UsageLog.id).desc()
    ).all()

    models = []
    for row in rows:
        models.append({
            "model_name": row.model_name or "unknown",
            "calls": row.calls,
            "tokens": int(row.tokens),
            "cost": round(float(row.cost), 4),
            "avg_response_ms": int(row.avg_response),
        })

    return {"code": 0, "data": models}


@router.get("/recent")
def usage_recent(limit: int = 20, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    最近 Token 消耗记录
    """
    rows = db.query(UsageLog)\
        .order_by(UsageLog.created_at.desc())\
        .limit(limit)\
        .all()

    records = []
    for r in rows:
        records.append({
            "id": r.id,
            "username": r.username,
            "question": r.question[:80] + ("..." if len(r.question or "") > 80 else ""),
            "model_name": r.model_name,
            "total_tokens": r.total_tokens,
            "cost": round(r.cost, 6),
            "response_time_ms": r.response_time_ms,
            "search_method": r.search_method,
            "success": r.success,
            "cached": r.cached,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        })

    return {"code": 0, "data": records}


# ==================== 缓存管理 ====================

@router.get("/cache/stats")
def cache_stats(user: User = Depends(get_current_user)):
    """查询缓存统计（命中率、条目数）"""
    return {"code": 0, "data": query_cache.stats()}


@router.post("/cache/clear")
def cache_clear(user: User = Depends(get_current_user)):
    """清空缓存（管理员）"""
    if user.role != "admin":
        return {"code": 403, "message": "仅管理员可清空缓存"}
    query_cache.clear()
    return {"code": 0, "message": "缓存已清空"}
