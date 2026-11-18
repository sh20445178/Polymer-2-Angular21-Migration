"""Shared LLM helpers for agents that rewrite component files."""

from __future__ import annotations

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..prompts import COMPONENT_SYSTEM


class ComponentFiles(BaseModel):
    """Complete contents of the Angular component files."""

    ts: str = Field(description="Full TypeScript component file content")
    html: str = Field(description="Full HTML template file content")
    scss: str = Field(description="Full SCSS file content")
    notes: list[str] = Field(default_factory=list, description="Short notes about the changes made")


def child_apis(component: dict, registry: dict) -> str:
    tags = set(re.findall(r"<([a-z][\w]*-[\w-]*)", component.get("html", ""))) & set(registry)
    tags.discard(component.get("tag"))
    if not tags:
        return "(none)"
    lines = []
    for tag in sorted(tags):
        spec = registry[tag]
        lines.append(f"- <{tag}> class {spec['class_name']}: inputs={spec['inputs']}, models={spec['models']}, "
                     f"outputs={sorted(spec['outputs'].values())}")
    return "\n".join(lines)


def _parse_fallback(text: str) -> ComponentFiles:
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return ComponentFiles(**json.loads(m.group(0)))
        except Exception:
            pass
    blocks = dict(re.findall(r"```(ts|typescript|html|scss|css)\n([\s\S]*?)```", text))
    return ComponentFiles(ts=blocks.get("ts") or blocks.get("typescript", ""), html=blocks.get("html", ""),
                          scss=blocks.get("scss") or blocks.get("css", ""), notes=[])


def invoke_component_llm(llm, prompt: str) -> ComponentFiles:
    messages = [SystemMessage(COMPONENT_SYSTEM), HumanMessage(prompt)]
    try:
        return llm.with_structured_output(ComponentFiles).invoke(messages)
    except Exception:
        reply = llm.invoke(messages + [HumanMessage("Respond with a JSON object with keys ts, html, scss, notes.")])
        return _parse_fallback(reply.content if isinstance(reply.content, str) else str(reply.content))


def accept_llm_output(component: dict, files: ComponentFiles) -> tuple[bool, str]:
    """Guard rails: never accept LLM output that breaks the component's public contract."""
    if not files.ts.strip() or not files.html.strip():
        return False, "empty output"
    if f"class {component['class_name']}" not in files.ts:
        return False, f"class {component['class_name']} missing"
    if f"'{component['tag']}'" not in files.ts and f'"{component["tag"]}"' not in files.ts:
        return False, "selector changed"
    for out_name in component.get("outputs", {}).values():
        if not re.search(rf"\b{re.escape(out_name)}\s*=\s*output", files.ts):
            return False, f"output '{out_name}' removed"
    for name, kind in component.get("kinds", {}).items():
        if kind in ("input", "model") and not re.search(rf"\b{re.escape(name)}\s*=\s*(input|model)\b", files.ts):
            return False, f"{kind} '{name}' removed"
    return True, ""
