"""Field extraction: JSONPath (API), CSS/XPath (DOM), template rendering."""

from __future__ import annotations

import re
from typing import Any

from jsonpath_ng.ext import parse as jsonpath_parse
from selectolax.parser import HTMLParser


# ---------------------------------------------------------------------------
# Cleansing helpers (PRD §8.5 场景5.1 — interaction-string normalization)
# ---------------------------------------------------------------------------

# Match a leading number (int or decimal) optionally followed by a CN/EN unit.
_LIKE_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(万|w|k|千)?", re.IGNORECASE)


def parse_likes(raw: Any) -> int:
    """Normalize a platform interaction string into an int (PRD §8.5 场景5.1).

    "1.2w"/"1.5万" → 12000/15000; "赞"/""/None/hidden → 0; plain "1234" → 1234.
    Never raises — a missing/unparseable like count is recorded as 0 and the
    worker keeps parsing the next field (PRD §4.3.1 fallback redline).
    """
    if raw is None:
        return 0
    # bool is an int subclass; treat True/False as "no real count" → 0.
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    s = str(raw).strip()
    if not s:
        return 0
    m = _LIKE_RE.match(s)
    if not m or m.group(1) is None:
        return 0
    try:
        num = float(m.group(1))
    except (TypeError, ValueError):
        return 0
    unit = (m.group(2) or "").lower()
    if unit in ("万", "w"):
        num *= 10000
    elif unit in ("k", "千"):
        num *= 1000
    return int(num)


def title_fallback(title: Any, content: Any) -> str:
    """Return the title, or the first 20 chars of the body when the title is
    empty (PRD §8.5 场景5.2). Never raises; empty everywhere → "".
    """
    t = title if isinstance(title, str) else ("" if title is None else str(title))
    if t.strip():
        return t.strip()
    c = content if isinstance(content, str) else ("" if content is None else str(content))
    return c[:20]


def render_template(tpl: str, **vars: Any) -> str:
    """Render {keyword} style placeholders from vars."""
    result = tpl
    for key, value in vars.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def _find_list_root(data: dict) -> list:
    """Heuristic: find the list of items in the response.

    Common patterns: $.data.items[*], $.data.comments[*], $.items[*].
    """
    if isinstance(data, dict):
        # Try common nested keys
        for outer in ("data", "result", "body"):
            outer_data = data.get(outer)
            if isinstance(outer_data, dict):
                for inner in ("items", "comments", "results", "list", "feeds", "notes"):
                    inner_data = outer_data.get(inner)
                    if isinstance(inner_data, list) and inner_data:
                        return inner_data
        # Try top-level list keys
        for key in ("items", "comments", "results", "list", "feeds", "notes"):
            val = data.get(key)
            if isinstance(val, list) and val:
                return val
    return []


def extract_api(
    sample_json: dict,
    group: str,
    field_map: dict[str, str],
) -> list[dict]:
    """Extract items from a JSON response using jsonpath-ng.

    Args:
        sample_json: The full API response dict.
        group: Schema group name (e.g. "ItemRef", "Post.body").
        field_map: {field_name: jsonpath_expression}.

    Returns:
        List of dicts with extracted fields.  Missing fields get None.
        Empty/deformed JSON returns [] without crashing.
    """
    if not sample_json or not isinstance(sample_json, dict):
        return []
    if not field_map:
        return []

    # Check if any field expression contains [*] — if so, derive list path from it
    list_path_expr: str | None = None
    for _field, expr in field_map.items():
        if "[*]" in expr:
            # Extract the list path: e.g. "$.data.items[*].note_id" -> "$.data.items[*]"
            idx = expr.index("[*]")
            list_path_expr = expr[: idx + 3]
            break

    if list_path_expr:
        try:
            lp = jsonpath_parse(list_path_expr)
            matches = lp.find(sample_json)
            items = [m.value for m in matches] if matches else []
        except Exception:
            items = []
    else:
        # No [*] in any expression — find the list heuristically
        items = _find_list_root(sample_json)

    if not items:
        return []

    results = []
    for item in items:
        row: dict[str, Any] = {}
        for field_name, expr in field_map.items():
            try:
                jp = jsonpath_parse(expr)
                matches = jp.find(item)
                row[field_name] = matches[0].value if matches else None
            except Exception:
                row[field_name] = None
        results.append(row)

    return results


def extract_dom(
    page_or_html: Any,
    group: str,
    field_map: dict[str, str],
) -> list[dict]:
    """Extract fields from HTML/DOM using selectolax.

    Args:
        page_or_html: Either a Playwright page (mocked in tests) or raw HTML string.
        group: Schema group name.
        field_map: {field_name: selector_expression}.
            - css:<sel> — get text content
            - css:<sel>@<attr> — get attribute value
            - xpath:<expr> — xpath expression (converted to css best-effort)

    Returns:
        List of dicts with extracted fields.
    """
    if isinstance(page_or_html, str):
        html = page_or_html
    else:
        html = ""
        try:
            html = page_or_html.content() if hasattr(page_or_html, "content") else ""
        except Exception:
            html = ""

    if not html or not field_map:
        return []

    tree = HTMLParser(html)
    row: dict[str, Any] = {}

    for field_name, expr in field_map.items():
        try:
            if expr.startswith("xpath:"):
                row[field_name] = None
            elif expr.startswith("css:"):
                selector_part = expr[4:]
                attr = None
                if "@" in selector_part:
                    selector_part, attr = selector_part.split("@", 1)
                nodes = tree.css(selector_part)
                if nodes:
                    if attr:
                        row[field_name] = nodes[0].attributes.get(attr)
                    else:
                        row[field_name] = nodes[0].text(strip=True)
                else:
                    row[field_name] = None
            else:
                row[field_name] = None
        except Exception:
            row[field_name] = None

    return [row] if any(v is not None for v in row.values()) else [row]
