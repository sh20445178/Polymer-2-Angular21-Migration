"""Component Migrator agent: converts one Polymer element (runs in parallel via Send)."""

from __future__ import annotations

from ..config import get_llm
from ..prompts import COMPONENT_REFINE
from ..transform.component import migrate_component
from .llm_support import accept_llm_output, child_apis, invoke_component_llm


def _polymer_script(element: dict) -> str:
    parts = []
    for p in element.get("properties", []):
        parts.append(f"// property {p['name']}: {p}")
    for obs in element.get("observers", []):
        parts.append(f"// observer {obs}")
    for m in element.get("methods", []):
        parts.append(f"{m['name']}({m.get('params', '')}) {{{m.get('body', '')}}}")
    return "\n".join(parts)


def component_migrator_agent(task: dict) -> dict:
    tag = task["tag"]
    element = task["element"]
    try:
        result = migrate_component(element, task["plan_entry"], task["registry"], set(task["style_modules"]),
                                   task.get("known_mixins"))
    except Exception as exc:  # isolate failures to the component
        entry = task["plan_entry"]
        return {"components": {tag: {"tag": tag, "class_name": entry["class_name"], "ts_path": entry["ts_path"],
                                     "status": "failed", "error": repr(exc), "issues": [], "todos": [],
                                     "notes": [], "kinds": entry["kinds"], "outputs": entry["outputs"]}},
                "log": [f"[migrator] <{tag}> FAILED: {exc!r}"]}

    log = [f"[migrator] <{tag}> converted by rules ({len(result['todos'])} TODOs)"]
    llm = get_llm(task.get("options", {}))
    if llm is not None:
        prompt = COMPONENT_REFINE.format(
            tag=tag, source_file=element.get("source_file", ""), polymer_template=element.get("template") or "",
            polymer_script=_polymer_script(element), ts_path=result["ts_path"], ts=result["ts"],
            html_path=result["html_path"], html=result["html"], scss_path=result["scss_path"], scss=result["scss"],
            child_apis=child_apis(result, task["registry"]),
            todos="\n".join(f"- {t}" for t in result["todos"]) or "(none)")
        try:
            files = invoke_component_llm(llm, prompt)
            ok, reason = accept_llm_output(result, files)
            if ok:
                result.update(ts=files.ts, html=files.html, scss=files.scss, llm_refined=True)
                result["notes"] = result["notes"] + [f"LLM: {n}" for n in files.notes]
                log.append(f"[migrator] <{tag}> refined by LLM")
            else:
                log.append(f"[migrator] <{tag}> LLM output rejected ({reason}); keeping rule-based draft")
        except Exception as exc:
            log.append(f"[migrator] <{tag}> LLM refinement failed: {exc}")
    return {"components": {tag: result}, "log": log}
