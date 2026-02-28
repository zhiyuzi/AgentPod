"""Skill discovery and SKILL.md frontmatter parsing (pure stdlib, no PyYAML)."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union

Source = Union[str, os.PathLike, bytes, bytearray]

_BOOL_TRUE = {"true", "yes", "y", "on"}
_BOOL_FALSE = {"false", "no", "n", "off"}
_NULLS = {"null", "~", "none"}

_NUM_INT_RE = re.compile(r"^[+-]?\d+$")
_NUM_FLOAT_RE = re.compile(
    r"^[+-]?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?$|^[+-]?\d+[eE][+-]?\d+$"
)

# 支持 "-" / "- xxx" / "-\t xxx"
_SEQ_ITEM_LINE_RE = re.compile(r"^-(?:[ \t]+.*)?$")


def load_markdown(source: Source) -> str:
    """支持 bytes/path/text 三种输入，纯标准库。"""
    if isinstance(source, (bytes, bytearray)):
        return source.decode("utf-8", errors="replace")

    if isinstance(source, os.PathLike) or (
        isinstance(source, str) and "\n" not in source and os.path.isfile(source)
    ):
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    return str(source)


def parse_frontmatter(
    md: str,
    *,
    allow_leading_blank_lines: bool = True,
    parse_types: bool = True,
) -> Tuple[Dict[str, Any], str]:
    """
    纯标准库解析 Markdown 顶部 YAML frontmatter（常用子集）。
    返回 (meta: Dict[str, Any], body: str)。

    - frontmatter 必须在文件开头（可选允许开头空行）
    - 起始分隔符 '---'，结束分隔符 '---' 或 '...'
    - 解析失败时：视为没有 frontmatter，返回 ({}, 原文) —— 避免悄悄丢内容
    """
    if not md:
        return {}, md

    if md.startswith("\ufeff"):  # BOM
        md = md[1:]

    lines = md.splitlines(True)  # 保留换行
    i = 0

    if allow_leading_blank_lines:
        while i < len(lines) and lines[i].strip() == "":
            i += 1

    if i >= len(lines) or lines[i].strip() != "---":
        return {}, md

    i += 1  # skip opening ---
    fm_lines: List[str] = []
    closing_idx: Optional[int] = None

    while i < len(lines):
        s = lines[i].strip()
        if s in ("---", "..."):
            closing_idx = i
            break
        fm_lines.append(lines[i].rstrip("\r\n"))
        i += 1

    if closing_idx is None:
        return {}, md

    body = "".join(lines[closing_idx + 1 :])

    try:
        meta, _ = _parse_node(fm_lines, 0, indent=0, parse_types=parse_types)
        if not isinstance(meta, dict):
            return {}, md
        return meta, body
    except Exception:
        return {}, md


def load_frontmatter_and_body(
    source: Source,
    *,
    allow_leading_blank_lines: bool = True,
    parse_types: bool = True,
) -> Tuple[Dict[str, Any], str]:
    """便捷入口：从文件/bytes/text 加载并解析 frontmatter。"""
    text = load_markdown(source)
    return parse_frontmatter(
        text,
        allow_leading_blank_lines=allow_leading_blank_lines,
        parse_types=parse_types,
    )


import logging as _logging

_log = _logging.getLogger("agentpod.skills")


def _body_first_paragraph(body: str) -> str:
    """从 body 中提取第一段非标题、非空文本作为 fallback description。"""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def discover_skills(skills_dir) -> List[Dict[str, Any]]:
    """扫描 skills 目录，返回合规 skill 的元数据列表。

    每个元素: {"name": str, "description": str, "dir": Path, "meta": dict, "body": str}

    校验规则（对齐 Agent Skills 规范）：
    - 目录下必须有 SKILL.md（大小写严格）
    - frontmatter 中 name 和 description 为必填
    - name 必须与目录名一致
    - 校验失败的 skill 会 log warning 并跳过
    """
    from pathlib import Path
    skills_dir = Path(skills_dir)
    if not skills_dir.is_dir():
        return []

    results: List[Dict[str, Any]] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue

        meta, body = load_frontmatter_and_body(skill_md)
        dir_name = child.name

        # 校验 name
        name = meta.get("name")
        if not name:
            _log.warning("Skill '%s': missing required 'name' in frontmatter, skipped", dir_name)
            continue
        name = str(name)
        if name != dir_name:
            _log.warning(
                "Skill '%s': frontmatter name '%s' does not match directory name, skipped",
                dir_name, name,
            )
            continue

        # 校验 description
        description = meta.get("description")
        if not description:
            _log.warning("Skill '%s': missing required 'description' in frontmatter, skipped", dir_name)
            continue
        description = str(description)

        results.append({
            "name": name,
            "description": description,
            "dir": child,
            "meta": meta,
            "body": body,
        })

    return results


# ----------------- YAML 子集解析核心 -----------------

_PLACEHOLDER_CONTINUE = "<!-- more content below -->"


def _leading_ws_len(s: str) -> int:
    m = re.match(r"^[ \t]*", s)
    return len(m.group(0)) if m else 0


def _strip_inline_comment(s: str) -> str:
    """去掉行内注释：尽量不影响引号内的 #。"""
    in_single = False
    in_double = False
    escaped = False

    for idx, ch in enumerate(s):
        if escaped:
            escaped = False
            continue
        if in_double and ch == "\\":
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            if idx == 0 or s[idx - 1].isspace():
                return s[:idx].rstrip()
    return s.rstrip()


def _parse_quoted_string(s: str) -> Optional[str]:
    s = s.strip()
    if len(s) < 2:
        return None

    if s[0] == "'" and s[-1] == "'":
        inner = s[1:-1]
        return inner.replace("''", "'")

    if s[0] == '"' and s[-1] == '"':
        inner = s[1:-1]
        inner = inner.replace(r"\\", "\\").replace(r"\"", '"')
        inner = inner.replace(r"\n", "\n").replace(r"\t", "\t").replace(r"\r", "\r")
        return inner

    return None


def _scan_for_closing_quote(text: str, quote: str) -> Optional[int]:
    if not text or text[0] != quote:
        return None

    i = 1
    if quote == '"':
        escaped = False
        while i < len(text):
            ch = text[i]
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                return i
            i += 1
        return None

    while i < len(text):
        if text[i] == "'":
            if i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            return i
        i += 1
    return None


def _consume_multiline_quoted(
    lines: List[str],
    i: int,
    *,
    prefix: str,
    base_indent: int,
    quote: str,
) -> Tuple[str, int]:
    close = _scan_for_closing_quote(prefix, quote)
    if close is not None:
        return prefix[: close + 1], i + 1

    raw_cont: List[str] = []
    detect = prefix
    end_line: Optional[int] = None

    j = i + 1
    while j < len(lines):
        ln = lines[j]
        if ln.strip() != "":
            ws = _leading_ws_len(ln)
            if ws <= base_indent:
                raise ValueError("unterminated quoted scalar")

        raw_cont.append(ln)
        detect += "\n" + ln
        if _scan_for_closing_quote(detect, quote) is not None:
            end_line = j
            break
        j += 1

    if end_line is None:
        raise ValueError("unterminated quoted scalar")

    cont_indents = [_leading_ws_len(x) for x in raw_cont if x.strip() != ""]
    strip_indent = min(cont_indents) if cont_indents else (base_indent + 1)

    cont_stripped: List[str] = []
    for x in raw_cont:
        if x.strip() == "":
            cont_stripped.append("")
        else:
            cont_stripped.append(x[strip_indent:])

    full = prefix + "\n" + "\n".join(cont_stripped)
    close2 = _scan_for_closing_quote(full, quote)
    if close2 is None:
        raise ValueError("unterminated quoted scalar")

    return full[: close2 + 1], end_line + 1


def _parse_scalar(s: str, *, parse_types: bool) -> Any:
    raw = _strip_inline_comment(s).strip()
    if raw == "":
        return ""

    qs = _parse_quoted_string(raw)
    if qs is not None:
        return qs

    if not parse_types:
        return raw

    low = raw.lower()
    if low in _BOOL_TRUE:
        return True
    if low in _BOOL_FALSE:
        return False
    if low in _NULLS:
        return None
    if _NUM_INT_RE.match(raw):
        try:
            return int(raw)
        except Exception:
            return raw
    if _NUM_FLOAT_RE.match(raw):
        try:
            return float(raw)
        except Exception:
            return raw

    return raw


def _split_top_level(s: str, sep: str) -> List[str]:
    """顶层分割：忽略引号内，忽略 {} / [] 嵌套。"""
    parts: List[str] = []
    buf: List[str] = []

    in_single = False
    in_double = False
    escaped = False
    brace = 0
    bracket = 0

    for ch in s:
        if escaped:
            buf.append(ch)
            escaped = False
            continue

        if in_double and ch == "\\":
            buf.append(ch)
            escaped = True
            continue

        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
            continue

        if not in_single and not in_double:
            if ch == "{":
                brace += 1
            elif ch == "}":
                brace = max(0, brace - 1)
            elif ch == "[":
                bracket += 1
            elif ch == "]":
                bracket = max(0, bracket - 1)

            if ch == sep and brace == 0 and bracket == 0:
                parts.append("".join(buf).strip())
                buf = []
                continue

        buf.append(ch)

    tail = "".join(buf).strip()
    if tail != "":
        parts.append(tail)
    return parts


def _split_top_level_first_colon(s: str) -> Optional[Tuple[str, str]]:
    """顶层找到第一个 ':'，忽略引号内与 {} / [] 嵌套。"""
    in_single = False
    in_double = False
    escaped = False
    brace = 0
    bracket = 0

    for idx, ch in enumerate(s):
        if escaped:
            escaped = False
            continue
        if in_double and ch == "\\":
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue

        if not in_single and not in_double:
            if ch == "{":
                brace += 1
            elif ch == "}":
                brace = max(0, brace - 1)
            elif ch == "[":
                bracket += 1
            elif ch == "]":
                bracket = max(0, bracket - 1)
            elif ch == ":" and brace == 0 and bracket == 0:
                left = s[:idx].strip()
                right = s[idx + 1 :].strip()
                if left == "":
                    return None
                return left, right

    return None


def _parse_inline_dict(rest2: str, *, parse_types: bool) -> Dict[str, Any]:
    """解析行内字典：{a:1, b:"x", c:[1,2], d:{e:3}}"""
    inner = rest2[1:-1].strip()
    if inner == "":
        return {}

    items = _split_top_level(inner, ",")
    out: Dict[str, Any] = {}

    for it in items:
        kv = _split_top_level_first_colon(it)
        if kv is None:
            continue
        k_raw, v_raw = kv

        kq = _parse_quoted_string(k_raw)
        key = kq if kq is not None else k_raw.strip()

        out[str(key)] = _parse_inline_value(v_raw, parse_types=parse_types)

    return out


def _parse_inline_value(rest: str, *, parse_types: bool) -> Any:
    rest2 = _strip_inline_comment(rest).strip()

    if rest2.startswith("[") and rest2.endswith("]"):
        inner = rest2[1:-1].strip()
        if inner == "":
            return []
        parts = _split_top_level(inner, ",")
        return [_parse_inline_value(p, parse_types=parse_types) for p in parts]

    if rest2.startswith("{") and rest2.endswith("}"):
        return _parse_inline_dict(rest2, parse_types=parse_types)

    return _parse_scalar(rest2, parse_types=parse_types)


def _dedent_block(block_lines: List[str], *, parent_indent: int) -> List[str]:
    indents: List[int] = []
    for ln in block_lines:
        if ln.strip() == "":
            continue
        ws = _leading_ws_len(ln)
        if ws > parent_indent:
            indents.append(ws)
    cut = min(indents) if indents else (parent_indent + 1)

    out: List[str] = []
    for ln in block_lines:
        if ln.strip() == "":
            out.append("")
        else:
            out.append(ln[cut:].rstrip("\r\n"))
    return out


def _fold_text(lines: List[str]) -> str:
    out_parts: List[str] = []
    para: List[str] = []

    for ln in lines:
        if ln == "":
            if para:
                out_parts.append(" ".join(para).rstrip())
                para = []
            out_parts.append("")
        else:
            para.append(ln)

    if para:
        out_parts.append(" ".join(para).rstrip())

    return "\n".join(out_parts)


def _collect_block_lines(
    lines: List[str], i: int, *, parent_indent: int
) -> Tuple[List[str], int]:
    block: List[str] = []
    n = len(lines)
    while i < n:
        ln = lines[i]
        if ln.strip() != "" and _leading_ws_len(ln) <= parent_indent:
            break
        block.append(ln)
        i += 1
    return block, i


def _next_significant(
    lines: List[str], i: int
) -> Optional[Tuple[int, str, int]]:
    n = len(lines)
    while i < n:
        raw = lines[i]
        if raw.strip() == "" or raw.lstrip().startswith("#"):
            i += 1
            continue
        return i, raw, _leading_ws_len(raw)
    return None


def _parse_mapping_entry(stripped: str) -> Optional[Tuple[str, str]]:
    if stripped.startswith("- "):
        return None
    pos = stripped.find(":")
    if pos <= 0:
        return None
    key = stripped[:pos].strip()
    rest = stripped[pos + 1 :]
    if not key:
        return None
    return key, rest


def _is_seq_item_line(stripped: str) -> bool:
    return _SEQ_ITEM_LINE_RE.match(stripped) is not None


def _parse_node(
    lines: List[str],
    i: int,
    *,
    indent: int,
    parse_types: bool,
) -> Tuple[Any, int]:
    sig = _next_significant(lines, i)
    if sig is None:
        return {}, len(lines)
    i0, _, ind0 = sig

    if ind0 < indent:
        return {}, i

    i = i0
    raw0 = lines[i]
    ind0 = _leading_ws_len(raw0)
    stripped0 = raw0[ind0:]

    if _is_seq_item_line(stripped0):
        return _parse_sequence(lines, i, indent=ind0, parse_types=parse_types)
    return _parse_mapping(lines, i, indent=ind0, parse_types=parse_types)


def _parse_mapping(
    lines: List[str],
    i: int,
    *,
    indent: int,
    parse_types: bool,
) -> Tuple[Dict[str, Any], int]:
    data: Dict[str, Any] = {}
    n = len(lines)

    while i < n:
        raw = lines[i]

        if raw.strip() == "" or raw.lstrip().startswith("#"):
            i += 1
            continue

        ind = _leading_ws_len(raw)
        if ind < indent:
            break
        if ind > indent:
            break

        stripped = raw[ind:]
        kv = _parse_mapping_entry(stripped)
        if kv is None:
            i += 1
            continue

        key, rest = kv
        rest_l = rest.lstrip()

        # 跨行双/单引号标量
        if rest_l.startswith('"') and _scan_for_closing_quote(rest_l, '"') is None:
            quoted, new_i = _consume_multiline_quoted(
                lines, i, prefix=rest_l, base_indent=indent, quote='"'
            )
            val = _parse_quoted_string(quoted)
            if val is None:
                raise ValueError("bad double-quoted scalar")
            data[key] = val
            i = new_i
            continue

        if rest_l.startswith("'") and _scan_for_closing_quote(rest_l, "'") is None:
            quoted, new_i = _consume_multiline_quoted(
                lines, i, prefix=rest_l, base_indent=indent, quote="'"
            )
            val = _parse_quoted_string(quoted)
            if val is None:
                raise ValueError("bad single-quoted scalar")
            data[key] = val
            i = new_i
            continue

        # 块标量
        if rest_l.startswith("|") or rest_l.startswith(">"):
            style = rest_l[0]
            chomping = rest_l[1:2] if len(rest_l) >= 2 and rest_l[1] in "+-" else ""
            i += 1
            block_raw, i = _collect_block_lines(lines, i, parent_indent=indent)
            dedented = _dedent_block(block_raw, parent_indent=indent)
            text = "\n".join(dedented) if style == "|" else _fold_text(dedented)
            if chomping == "-":
                text = text.rstrip("\n")
            data[key] = text
            continue

        # 行内值
        if _strip_inline_comment(rest).strip() != "":
            data[key] = _parse_inline_value(rest, parse_types=parse_types)
            i += 1
            continue

        # key: （空） -> 嵌套块或空字符串
        i += 1
        sig = _next_significant(lines, i)
        if sig is None:
            data[key] = ""
            break
        j, _, indj = sig
        if indj <= indent:
            data[key] = ""
            continue

        node, i = _parse_node(lines, j, indent=indj, parse_types=parse_types)
        data[key] = node

    return data, i


def _parse_sequence(
    lines: List[str],
    i: int,
    *,
    indent: int,
    parse_types: bool,
) -> Tuple[List[Any], int]:
    arr: List[Any] = []
    n = len(lines)

    while i < n:
        raw = lines[i]

        if raw.strip() == "" or raw.lstrip().startswith("#"):
            i += 1
            continue

        ind = _leading_ws_len(raw)
        if ind < indent:
            break
        if ind > indent:
            break

        stripped = raw[ind:]
        if not _is_seq_item_line(stripped):
            break

        m = re.match(r"^-(?:[ \t]+(.*))?$", stripped)
        item_rest = (m.group(1) if m else None) or ""
        item_rest_l = item_rest.lstrip()

        # sequence 里的跨行双/单引号标量
        if item_rest_l.startswith('"') and _scan_for_closing_quote(item_rest_l, '"') is None:
            quoted, new_i = _consume_multiline_quoted(
                lines, i, prefix=item_rest_l, base_indent=indent, quote='"'
            )
            val = _parse_quoted_string(quoted)
            if val is None:
                raise ValueError("bad double-quoted scalar")
            arr.append(val)
            i = new_i
            continue

        if item_rest_l.startswith("'") and _scan_for_closing_quote(item_rest_l, "'") is None:
            quoted, new_i = _consume_multiline_quoted(
                lines, i, prefix=item_rest_l, base_indent=indent, quote="'"
            )
            val = _parse_quoted_string(quoted)
            if val is None:
                raise ValueError("bad single-quoted scalar")
            arr.append(val)
            i = new_i
            continue

        # - | / - >
        if item_rest_l.startswith("|") or item_rest_l.startswith(">"):
            style = item_rest_l[0]
            chomping = item_rest_l[1:2] if len(item_rest_l) >= 2 and item_rest_l[1] in "+-" else ""
            i += 1
            block_raw, i = _collect_block_lines(lines, i, parent_indent=indent)
            dedented = _dedent_block(block_raw, parent_indent=indent)
            text = "\n".join(dedented) if style == "|" else _fold_text(dedented)
            if chomping == "-":
                text = text.rstrip("\n")
            arr.append(text)
            continue

        # - （空） -> 嵌套块
        if _strip_inline_comment(item_rest).strip() == "":
            i += 1
            sig = _next_significant(lines, i)
            if sig is None:
                arr.append("")
                break
            j, _, indj = sig
            if indj <= indent:
                arr.append("")
                continue
            node, i = _parse_node(lines, j, indent=indj, parse_types=parse_types)
            arr.append(node)
            continue

        # - key: value（列表里的一行 mapping）
        maybe_kv = _parse_mapping_entry(item_rest_l)
        if maybe_kv is not None:
            key, rest = maybe_kv
            entry_indent = indent + 2
            obj: Dict[str, Any] = {}

            rest_l = rest.lstrip()
            if rest_l.startswith("|") or rest_l.startswith(">"):
                style = rest_l[0]
                chomping = rest_l[1:2] if len(rest_l) >= 2 and rest_l[1] in "+-" else ""
                i += 1
                block_raw, i = _collect_block_lines(lines, i, parent_indent=indent)
                dedented = _dedent_block(block_raw, parent_indent=indent)
                text = "\n".join(dedented) if style == "|" else _fold_text(dedented)
                if chomping == "-":
                    text = text.rstrip("\n")
                obj[key] = text
            elif _strip_inline_comment(rest).strip() != "":
                obj[key] = _parse_inline_value(rest, parse_types=parse_types)
                i += 1
            else:
                i += 1
                sig = _next_significant(lines, i)
                if sig is None:
                    obj[key] = ""
                else:
                    j, _, indj = sig
                    if indj <= indent:
                        obj[key] = ""
                    else:
                        node, i = _parse_node(lines, j, indent=indj, parse_types=parse_types)
                        obj[key] = node

            sig2 = _next_significant(lines, i)
            if sig2 is not None:
                j2, _, ind2 = sig2
                if ind2 > indent and ind2 == entry_indent and not lines[j2].lstrip().startswith("-"):
                    more, i = _parse_mapping(lines, j2, indent=entry_indent, parse_types=parse_types)
                    obj.update(more)

            arr.append(obj)
            continue

        # 普通标量 / 行内列表 / 行内字典
        arr.append(_parse_inline_value(item_rest, parse_types=parse_types))
        i += 1

    return arr, i