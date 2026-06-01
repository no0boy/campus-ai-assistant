"""
Agent 外部工具箱 — 天气查询 / 网页搜索
Agent Think 时可以自主决定调用哪个工具
"""

import urllib.request
import urllib.parse
import json


# 城市→经纬度映射（常用城市）
CITY_COORDS = {
    "北京": (39.90, 116.40), "上海": (31.23, 121.47), "广州": (23.13, 113.26),
    "深圳": (22.54, 114.06), "杭州": (30.29, 120.15), "成都": (30.57, 104.07),
    "武汉": (30.58, 114.30), "南京": (32.06, 118.80), "西安": (34.26, 108.94),
    "重庆": (29.56, 106.55), "长沙": (28.23, 112.94), "郑州": (34.75, 113.63),
    "济南": (36.65, 117.00), "天津": (39.13, 117.20), "厦门": (24.48, 118.09),
    "东莞": (23.05, 113.75), "佛山": (23.03, 113.12), "珠海": (22.27, 113.58),
}


def get_weather(city: str = "广州") -> str:
    """
    查询天气 — open-meteo 免费 API，无需 Key，JSON 结构化返回
    """
    coords = CITY_COORDS.get(city, (23.13, 113.26))  # 默认广州
    try:
        lat, lon = coords
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&timezone=Asia/Shanghai"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read().decode())
        cur = data.get("current", {})
        temp = cur.get("temperature_2m", "?")
        hum = cur.get("relative_humidity_2m", "?")
        wind = cur.get("wind_speed_10m", "?")
        code = cur.get("weather_code", 0)
        weather_map = {0: "晴", 1: "少云", 2: "多云", 3: "阴", 45: "雾", 51: "小雨", 61: "中雨", 80: "阵雨"}
        wx = weather_map.get(code, f"code{code}")
        return f"{city}天气：{wx} · {temp}°C · 湿度{hum}% · 风速{wind}km/h"
    except Exception:
        return f"{city}天气：查询超时"


import os as _os
TAVILY_API_KEY = _os.getenv("TAVILY_API_KEY", "")

def search_web(query: str) -> str:
    """
    网页搜索 — Tavily Search API（专为 AI Agent 设计）
    """
    try:
        data = json.dumps({
            "query": query,
            "search_depth": "basic",
            "max_results": 3,
            "include_answer": True
        }).encode()
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {TAVILY_API_KEY}"}
        )
        resp = urllib.request.urlopen(req, timeout=5)
        result = json.loads(resp.read().decode())

        parts = []
        if result.get("answer"):
            parts.append("摘要：" + result["answer"])
        for i, r in enumerate(result.get("results", [])[:3]):
            parts.append(f"{i + 1}. {r.get('title','')}：{r.get('content','')[:200]}")
        return "\n".join(parts) if parts else f"未找到「{query}」的结果"
    except Exception as e:
        return f"搜索失败：{e}"


# 工具注册表
TOOLS = {
    "天气查询": {
        "fn": get_weather,
        "desc": "查询指定城市的天气，参数：城市名",
        "example": "TOOL: 天气查询 广州",
    },
    "网页搜索": {
        "fn": search_web,
        "desc": "搜索网页信息，参数：搜索关键词",
        "example": "TOOL: 网页搜索 2025年考研报名时间",
    },
}

TOOL_DESCRIPTIONS = "\n".join([
    f"- {name}：{info['desc']}（例如：{info['example']}）"
    for name, info in TOOLS.items()
])
