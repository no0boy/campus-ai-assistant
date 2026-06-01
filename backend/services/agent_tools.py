"""
Agent 外部工具箱 — 天气查询 / 网页搜索
Agent Think 时可以自主决定调用哪个工具
"""

import urllib.request
import urllib.parse
import json


def get_weather(city: str = "广州") -> str:
    """
    查询天气 — wttr.in 免费 API，无需 Key
    """
    try:
        city_enc = urllib.parse.quote(city)
        url = f"https://wttr.in/{city_enc}?format=%C+%t+%h+%w&lang=zh"
        req = urllib.request.Request(url, headers={"User-Agent": "curl"})
        resp = urllib.request.urlopen(req, timeout=5)
        return f"{city}天气：{resp.read().decode().strip()}"
    except Exception as e:
        return f"天气查询失败：{e}"


def search_web(query: str) -> str:
    """
    网页搜索 — DuckDuckGo HTML 版，免费无 Key
    返回前几条结果摘要
    """
    try:
        q = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={q}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=8)
        html = resp.read().decode()

        # 简单解析搜索结果
        results = []
        import re
        snippets = re.findall(r'class="result__snippet">(.*?)</a>', html, re.DOTALL)
        for i, s in enumerate(snippets[:3]):
            text = re.sub(r'<[^>]+>', '', s).strip()[:200]
            if text:
                results.append(f"{i + 1}. {text}")

        if results:
            return "搜索结果：\n" + "\n".join(results)
        return f"未找到「{query}」的搜索结果"
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
