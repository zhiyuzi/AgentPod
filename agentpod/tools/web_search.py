"""WebSearchTool - search the web via Bing (cn.bing.com)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import httpx

from agentpod.tools.base import Tool, ToolResult

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_MAX_RESULTS = 8
_TIMEOUT = 15


def _parse_bing_html(html: str) -> list[dict]:
    """Parse Bing search result HTML and extract results."""
    results: list[dict] = []

    # Bing wraps each organic result in <li class="b_algo">
    parts = re.split(r'<li class="b_algo"', html)

    for part in parts[1:]:  # skip everything before first result
        # Title + URL: <h2 ...><a ... href="URL" ...>TITLE</a></h2>
        m = re.search(
            r'<h2[^>]*><a[^>]*href="([^"]*)"[^>]*>(.*?)</a></h2>',
            part, re.DOTALL,
        )
        if not m:
            continue

        url = m.group(1)
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not title:
            continue

        # Snippet: <p class="b_lineclamp...">TEXT</p>
        snippet = ""
        m2 = re.search(
            r'<p class="b_lineclamp[^"]*">(.*?)</p>', part, re.DOTALL
        )
        if not m2:
            m2 = re.search(
                r'<div class="b_caption"><p>(.*?)</p>', part, re.DOTALL
            )
        if m2:
            snippet = re.sub(r"<[^>]+>", "", m2.group(1)).strip()
            # Clean up HTML entities
            snippet = snippet.replace("&ensp;", " ").replace("&#0183;", "·")
            snippet = re.sub(r"&\w+;", "", snippet)

        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= _MAX_RESULTS:
            break

    return results


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for information using Bing."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        query = input.get("query", "").strip()
        if not query:
            return ToolResult(content="Empty search query", is_error=True)

        url = f"https://cn.bing.com/search?q={quote(query)}"
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=_TIMEOUT
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
        except Exception as e:
            return ToolResult(content=f"Search error: {e}", is_error=True)

        results = _parse_bing_html(resp.text)
        if not results:
            return ToolResult(content="No results found.")

        lines = []
        for i, r in enumerate(results, 1):
            line = f"{i}. {r['title']}"
            if r["url"]:
                line += f"\n   URL: {r['url']}"
            if r["snippet"]:
                line += f"\n   {r['snippet']}"
            lines.append(line)

        return ToolResult(content="\n\n".join(lines))
