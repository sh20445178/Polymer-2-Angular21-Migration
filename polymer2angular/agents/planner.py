"""Migration Planner agent: decides target structure, public APIs and the per-component work queue."""

from __future__ import annotations

import re

from ..transform.script import classify_properties, compute_outputs, template_two_way_roots
from ..utils.naming import kebab_to_pascal


def _route_pages(elements: dict) -> set[str]:
    pages = set()
    for el in elements.values():
        for block in re.findall(r"<iron-pages[^>]*>([\s\S]*?)</iron-pages>", el.get("template") or ""):
            pages.update(re.findall(r"<([a-z][\w]*-[\w-]*)", block))
    return pages


def _topological(deps: dict[str, list[str]]) -> list[str]:
    order, visiting, done = [], set(), set()

    def visit(tag):
        if tag in done or tag in visiting:
            return
        visiting.add(tag)
        for d in deps.get(tag, []):
            visit(d)
        visiting.discard(tag)
        done.add(tag)
        order.append(tag)

    for tag in sorted(deps):
        visit(tag)
    return order


def build_plan(elements: dict, analysis: dict) -> tuple[dict, dict]:
    pages = _route_pages(elements)
    registry, entries, used_names = {}, {}, {"App"}
    for tag, el in elements.items():
        class_name = kebab_to_pascal(tag)
        if class_name in used_names:
            class_name += "Element"
        used_names.add(class_name)
        folder = "pages" if tag in pages or re.search(r"(^|-)(view|page)\d*($|-)", tag) else "components"
        ts_path = f"src/app/{folder}/{tag}/{tag}.ts"
        kinds = classify_properties(el, template_two_way_roots(el.get("template")))
        models = {n for n, k in kinds.items() if k == "model"}
        outputs = compute_outputs(el, models)
        entries[tag] = {"tag": tag, "class_name": class_name, "ts_path": ts_path, "file_stem": tag,
                        "folder": folder, "kinds": kinds, "outputs": outputs,
                        "complexity": analysis.get("complexity", {}).get(tag, "low")}
        registry[tag] = {"tag": tag, "class_name": class_name, "import_path": ts_path[:-3],
                         "inputs": sorted(n for n, k in kinds.items() if k == "input"),
                         "models": sorted(models), "outputs": outputs}
    order = _topological({t: analysis.get("dependencies", {}).get(t, []) for t in elements})
    return {"entries": entries, "order": order}, registry


def planner_agent(state: dict) -> dict:
    plan, registry = build_plan(state["elements"], state["analysis"])
    lines = [f"[planner] {len(plan['entries'])} components planned; order: {' -> '.join(plan['order'])}"]
    for tag in plan["order"]:
        e = plan["entries"][tag]
        lines.append(f"[planner]   <{tag}> -> {e['ts_path']} ({e['class_name']}, complexity={e['complexity']})")
    return {"plan": plan, "registry": registry, "fix_iteration": 0, "log": lines}
