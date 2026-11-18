"""Dependency Analyzer agent: merges definitions, resolves behaviors/mixins and maps dependencies."""

from __future__ import annotations

import copy
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from ..config import get_llm
from ..parsing.polymer_parser import merge_models
from ..prompts import ANALYSIS_PROMPT, ANALYSIS_SYSTEM
from ..transform.styles import extract_mixins

THIRD_PARTY_PREFIXES = ("iron-", "paper-", "app-", "neon-", "gold-", "platinum-", "vaadin-", "google-", "marked-",
                        "prism-", "font-roboto")


def _short(name: str) -> str:
    return name.split(".")[-1]


def resolve_behaviors(element: dict, behaviors: dict[str, dict], seen: set | None = None) -> tuple[dict, list[str]]:
    """Inline behavior/mixin members into an element (element members win). Returns (element, unresolved)."""
    seen = seen or set()
    result = copy.deepcopy(element)
    unresolved: list[str] = []
    for name in element.get("behaviors", []):
        key = name if name in behaviors else _short(name) if _short(name) in behaviors else None
        if key is None:
            unresolved.append(name)
            continue
        if key in seen:
            continue
        behavior, nested_unresolved = resolve_behaviors(behaviors[key], behaviors, seen | {key})
        unresolved.extend(nested_unresolved)
        have_props = {p["name"] for p in result["properties"]}
        result["properties"] += [p for p in behavior.get("properties", []) if p["name"] not in have_props]
        have_methods = {m["name"] for m in result["methods"]}
        lifecycle_merge = {"ready", "attached", "detached", "connectedCallback", "disconnectedCallback", "created"}
        for method in behavior.get("methods", []):
            if method["name"] not in have_methods:
                result["methods"].append(method)
            elif method["name"] in lifecycle_merge:
                target = next(m for m in result["methods"] if m["name"] == method["name"])
                target["body"] = method["body"] + "\n" + target["body"]
        result["observers"] = list(dict.fromkeys(behavior.get("observers", []) + result.get("observers", [])))
        result["listeners"] = {**behavior.get("listeners", {}), **result.get("listeners", {})}
        result["host_attributes"] = {**behavior.get("host_attributes", {}), **result.get("host_attributes", {})}
        result.setdefault("merged_behaviors", []).append(key)
    return result, unresolved


def analyze(scan: dict) -> dict:
    merged = merge_models(scan["models"])
    elements, behaviors, style_modules = {}, {}, {}
    for key, model in merged.items():
        kind = model.get("kind")
        if kind == "element" and model.get("tag"):
            elements[model["tag"]] = model
        elif kind in ("behavior", "mixin"):
            behaviors[_short(key)] = model
            behaviors[key] = model
        elif kind == "style-module":
            style_modules[model["tag"]] = model

    known_mixins: dict[str, str] = {}
    for module in style_modules.values():
        for css in module.get("styles", []):
            known_mixins.update(extract_mixins(css)[1])
    for css in scan.get("index", {}).get("global_styles", []):
        known_mixins.update(extract_mixins(css)[1])

    effective, unresolved_map, deps, external = {}, {}, {}, {}
    for tag, element in elements.items():
        eff, unresolved = resolve_behaviors(element, behaviors)
        effective[tag] = eff
        if unresolved:
            unresolved_map[tag] = unresolved
        used = set(re.findall(r"<([a-z][\w]*-[\w-]*)", eff.get("template") or ""))
        used -= {"dom-if", "dom-repeat", "dom-bind", "custom-style", "dom-module", "array-selector"}
        deps[tag] = sorted(u for u in used if u in elements)
        ext = sorted(u for u in used if u not in elements)
        if ext:
            external[tag] = ext

    used_somewhere = {d for ds in deps.values() for d in ds}
    index_tags = set(re.findall(r"<([a-z][\w]*-[\w-]*)", scan.get("index", {}).get("body", "")))
    roots = sorted(t for t in elements if t in index_tags) or sorted(t for t in elements if t not in used_somewhere)

    complexity = {}
    for tag, el in effective.items():
        score = (len(el.get("properties", [])) + 2 * len(el.get("observers", [])) + len(el.get("methods", []))
                 + 3 * len(external.get(tag, [])) + (len(el.get("template") or "") // 400))
        complexity[tag] = "high" if score > 25 else "medium" if score > 10 else "low"

    all_external = sorted({e for es in external.values() for e in es})
    return {
        "elements": effective,
        "behaviors": {k: v for k, v in behaviors.items() if "." not in k},
        "style_modules": style_modules,
        "known_mixins": known_mixins,
        "analysis": {
            "element_count": len(elements),
            "behavior_count": len({id(v) for v in behaviors.values()}),
            "style_module_count": len(style_modules),
            "polymer_versions": scan.get("polymer_versions", []),
            "dependencies": deps,
            "external_elements": external,
            "third_party_elements": [e for e in all_external if e.startswith(THIRD_PARTY_PREFIXES)],
            "unknown_elements": [e for e in all_external if not e.startswith(THIRD_PARTY_PREFIXES)],
            "unresolved_behaviors": unresolved_map,
            "root_elements": roots,
            "complexity": complexity,
            "package_dependencies": scan.get("dependencies", {}),
        },
    }


def analyzer_agent(state: dict) -> dict:
    result = analyze(state["scan"])
    analysis = result["analysis"]
    analysis["known_mixins"] = result["known_mixins"]
    log = [f"[analyzer] {analysis['element_count']} elements, {analysis['behavior_count']} behaviors/mixins, "
           f"{analysis['style_module_count']} style modules; roots={analysis['root_elements']}"]
    llm = get_llm(state.get("options", {}))
    if llm is not None:
        try:
            summary = {k: v for k, v in analysis.items() if k != "known_mixins"}
            reply = llm.invoke([SystemMessage(ANALYSIS_SYSTEM),
                                HumanMessage(ANALYSIS_PROMPT.format(analysis=json.dumps(summary, indent=2)[:12000]))])
            analysis["strategy"] = reply.content if isinstance(reply.content, str) else str(reply.content)
            log.append("[analyzer] LLM migration strategy generated")
        except Exception as exc:
            log.append(f"[analyzer] LLM strategy skipped: {exc}")
    return {
        "elements": result["elements"],
        "behaviors": result["behaviors"],
        "style_modules": result["style_modules"],
        "analysis": analysis,
        "log": log,
    }
