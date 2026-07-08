"""LLM mapper: JSON sample + schema group -> JSONPath (skim_design.md S8.4).

Lazy-imports anthropic so the module is importable without it installed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from jsonpath_ng.ext import parse as jsonpath_parse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM mapping functions
# ---------------------------------------------------------------------------


async def map_group(
    sample_json: dict,
    group: str,
    field_specs: dict[str, str],
    *,
    max_retries: int = 2,
    anthropic_client: Any = None,
) -> dict[str, str]:
    """Map schema group fields to JSONPath expressions using Haiku.

    Args:
        sample_json: API response sample dict.
        group: Schema group name (e.g. "ItemRef", "Post.body", "Post.interactions", "Comments").
        field_specs: {field_name: natural_language_description}.
        max_retries: Number of LLM retry attempts on validation failure.
        anthropic_client: Optional pre-configured client (for testing).

    Returns:
        dict[str, str]: {field_name: jsonpath_expression}
    """
    client = anthropic_client
    if client is None:
        client = _get_anthropic_client()

    if not field_specs:
        return {}

    # Build structured prompt
    field_list = "\n".join(
        f"- {name}: {desc}" for name, desc in field_specs.items()
    )
    sample_preview = _json_preview(sample_json, max_chars=4000)

    system_prompt = (
        "You are a JSONPath expert. Given a JSON sample and a list of fields "
        "with descriptions, return ONLY a JSON object mapping each field name "
        "to a JSONPath expression. Use jsonpath-ng syntax. "
        "Rules:\n"
        "- Root must start with '$'\n"
        "- Use [*] for array iteration\n"
        "- Do NOT include any explanation, only the JSON object\n"
        "- If a field cannot be found, map to an empty string ''\n"
        "- Use dot notation for object keys, [*] for arrays"
    )

    user_prompt = (
        f"JSON sample:\n{sample_preview}\n\n"
        f"Group: {group}\n\n"
        f"Fields to map:\n{field_list}\n\n"
        "Return JSON only, no markdown, no explanation."
    )

    for attempt in range(1 + max_retries):
        try:
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            content = ""
            if resp.content:
                for block in resp.content:
                    if hasattr(block, "text"):
                        content += block.text

            # Parse the response
            content = content.strip()
            # Strip markdown code blocks if present
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(
                    line for line in lines
                    if not line.strip().startswith("```")
                )

            field_map = json.loads(content)

            if not isinstance(field_map, dict):
                logger.warning("LLM returned non-dict response on attempt %d", attempt + 1)
                continue

            # Validate: all requested fields present
            missing = set(field_specs.keys()) - set(field_map.keys())
            if missing:
                logger.warning("LLM missing fields on attempt %d: %s", attempt + 1, missing)
                # Add empty paths for missing
                for m in missing:
                    field_map[m] = ""
                # Retry if still have attempts
                if attempt < max_retries:
                    user_prompt += (
                        f"\n\nPrevious attempt was missing fields: {missing}. "
                        f"Please include ALL fields."
                    )
                    continue

            return field_map

        except Exception as e:
            logger.warning("LLM map_group attempt %d failed: %s", attempt + 1, e)
            if attempt == max_retries:
                # Return empty map on final failure
                return {name: "" for name in field_specs}

    # Fallback
    return {name: "" for name in field_specs}


async def extract_custom_field(
    sample_json: dict,
    description: str,
    *,
    anthropic_client: Any = None,
) -> str:
    """Extract a single custom field JSONPath from a natural language description.

    Args:
        sample_json: API response sample dict.
        description: Natural language description (e.g. "video duration").

    Returns:
        str: JSONPath expression, or empty string if not found.
    """
    client = anthropic_client
    if client is None:
        client = _get_anthropic_client()

    sample_preview = _json_preview(sample_json, max_chars=4000)

    system_prompt = (
        "You are a JSONPath expert. Given a JSON sample and a natural language "
        "description of a field, return ONLY a JSONPath expression string "
        "(jsonpath-ng syntax). Root must start with '$'. "
        "If the field does not exist in the sample, return empty string ''."
    )

    user_prompt = (
        f"JSON sample:\n{sample_preview}\n\n"
        f"Field description: {description}\n\n"
        "Return ONLY the JSONPath string, nothing else."
    )

    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        content = ""
        if resp.content:
            for block in resp.content:
                if hasattr(block, "text"):
                    content += block.text

        content = content.strip().strip('"').strip("'")
        # Strip markdown
        if content.startswith("```"):
            content = content.split("\n")[1] if "\n" in content else content.replace("```", "").strip()

        return content
    except Exception as e:
        logger.warning("extract_custom_field failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_map(
    sample_json: dict,
    field_map: dict[str, str],
) -> dict[str, bool]:
    """Validate a field map by running JSONPath expressions against the sample.

    A field passes (True) if its JSONPath yields a non-empty, non-null value.
    A field fails (False) if the path is empty, invalid, or returns null/empty.

    Args:
        sample_json: API response sample dict.
        field_map: {field_name: jsonpath_expression}.

    Returns:
        {field_name: bool} — True if the path yields a value, False otherwise.
    """
    results: dict[str, bool] = {}
    for field_name, expr in field_map.items():
        if not expr or not expr.strip():
            results[field_name] = False
            continue

        try:
            parsed = jsonpath_parse(expr)
            matches = parsed.find(sample_json)
            if not matches:
                results[field_name] = False
                continue
            value = matches[0].value
            # Non-empty check: None, empty string, empty list/dict = False
            if value is None:
                results[field_name] = False
            elif isinstance(value, (str, list, dict)) and len(value) == 0:
                results[field_name] = False
            else:
                results[field_name] = True
        except Exception as e:
            logger.debug("validate_map field '%s' error: %s", field_name, e)
            results[field_name] = False

    return results


# ---------------------------------------------------------------------------
# Platform YAML builder
# ---------------------------------------------------------------------------


def build_platform_yaml(
    display_name: str,
    base_url: str,
    flows: dict[str, list[dict[str, Any]]],
    maps: dict[str, dict[str, dict[str, str]]],
) -> str:
    """Build a platform.yaml text from recorded flows and LLM-generated maps.

    Args:
        display_name: Human-readable platform name.
        base_url: Platform base URL.
        flows: {flow_name: [step_dict, ...]} — step dicts compatible with spec.Step.
        maps: {flow_name: {group_name: {field_name: jsonpath}}} — field maps per group.

    Returns:
        str: Valid YAML text compatible with PlatformSpec (DM-07 spec.py).
    """
    import yaml

    # Derive platform slug from display_name
    platform_slug = display_name.lower().replace(" ", "_").replace("-", "_")

    # Build flows spec
    flows_spec: dict[str, Any] = {}
    for flow_name, steps in flows.items():
        flow_steps = []
        for step in steps:
            step_dict: dict[str, Any] = {"type": step.get("type", "navigate")}

            if step.get("url"):
                step_dict["url"] = step["url"]

            if step.get("text"):
                step_dict["text"] = step["text"]

            if step.get("locator"):
                step_dict["locator"] = step["locator"]

            if step["type"] in ("scroll",):
                step_dict["max_times"] = step.get("max_times", 3)
                step_dict["wait_ms"] = step.get("wait_ms", 800)

            if step["type"] == "wait_xhr":
                step_dict["url_pattern"] = step.get("url_pattern", "")
                step_dict["method"] = step.get("method")
                step_dict["save_as"] = step.get("save_as")

            if step["type"] == "extract":
                step_dict["from"] = step.get("from", "")
                step_dict["group"] = step.get("group", "")
                step_dict["map"] = step.get("map", {})

            flow_steps.append(step_dict)

        # Inject extract steps from maps
        flow_maps = maps.get(flow_name, {})
        for group_name, field_map in flow_maps.items():
            # Find the save_as key for this flow
            save_as = _find_save_as_for_group(flow_name, group_name, steps)
            if save_as:
                extract_step = {
                    "type": "extract",
                    "from": save_as,
                    "group": group_name,
                    "map": field_map,
                }
                flow_steps.append(extract_step)

        flows_spec[flow_name] = {"steps": flow_steps}

    spec = {
        "platform": platform_slug,
        "display_name": display_name,
        "base_url": base_url,
        "login": {
            "type": "qrcode",
            "login_url": None,
            "success_detect": "url_change",
            "success_pattern": None,
            "timeout": 120,
        },
        "flows": flows_spec,
        "sort_values": {
            "general": "general",
            "time_descending": "latest",
            "popularity_descending": "hot",
        },
    }

    return yaml.dump(spec, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _find_save_as_for_group(
    flow_name: str,
    group_name: str,
    steps: list[dict],
) -> str | None:
    """Heuristically find the save_as key for a group in a flow."""
    group_to_save_as = {
        "ItemRef": "search",
        "Post.body": "detail",
        "Post.interactions": "detail",
        "Comments": "comments",
    }
    default = group_to_save_as.get(group_name)

    # Check if any step already has a matching save_as
    for step in steps:
        if step.get("type") == "wait_xhr" and step.get("save_as"):
            if default and default in step.get("save_as", ""):
                return step["save_as"]
            return step["save_as"]

    return default


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_anthropic_client() -> Any:
    """Lazy import anthropic client. Raises ImportError if not installed."""
    try:
        from anthropic import AsyncAnthropic
        return AsyncAnthropic()
    except ImportError:
        raise ImportError(
            "anthropic is required for LLM mapping. Install with: pip install anthropic"
        )


def _json_preview(data: Any, max_chars: int = 2000) -> str:
    """Create a truncated JSON preview for the prompt."""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text
