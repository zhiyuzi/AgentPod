"""WebSearchTool - search the web via Baidu."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote, unquote

import httpx

from agentpod.tools.base import Tool, ToolResult

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_MAX_RESULTS = 8
_TIMEOUT = 15


def _extract_real_url(baidu_url: str) -> str:
    """Try to extract the real URL from Baidu's redirect link."""
    # Baidu wraps results in /link?url=xxx redirects
    # We return the Baidu link as-is since resolving requires another request
    return baidu_url


def _parse_baidu_html(html: str) -> list[dict]:
    """Parse Baidu search result HTML and extract results."""
    results: list[dict] = []

    # Baidu wraps each result in <div class="result ..."> or <div class="c-container">
    # Title is in <h3><a href="...">title</a></h3>
    # Snippet is in <span class="content-right_..."> or various abstract containers

    # Extract result blocks: find all <h3> with <a> inside them
    # Pattern: <h3 ...><a ... href="URL" ...>TITLE</a></h3>
    title_pattern = re.compile(
        r'<h3[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>\s*</h3>',
        re.DOTALL,
    )

    for match in title_pattern.finditer(html):
        url = match.group(1)
        title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        if not title or not url:
            continue

        # Try to find snippet near this result
        # Look for text after the title match within the next ~2000 chars
        start = match.end()
        block = html[start : start + 2000]

        # Extract snippet from common Baidu snippet containers
        snippet = ""
        snippet_patterns = [
            r'<span class="content-right_[^"]*">(.*?)</span>',
            r'<span class="[^"]*abstract[^"]*">(.*?)</span>',
            r'class="c-abstract[^"]*">(.*?)</div>',
        ]
        for sp in snippet_patterns:
            m = re.search(sp, block, re.DOTALL)
            if m:
                snippet = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                break

        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= _MAX_RESULTS:
            break

    return results


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for information using Baidu."
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

        url = f"https://www.baidu.com/s?wd={quote(query)}"
        headers = {"User-Agent": _USER_AGENT}

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=_TIMEOUT
            ) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
        except Exception as e:
            return ToolResult(content=f"Search error: {e}", is_error=True)

        results = _parse_baidu_html(resp.text)
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
