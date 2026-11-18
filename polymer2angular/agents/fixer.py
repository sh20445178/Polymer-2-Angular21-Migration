"""Fixer agent: repairs components flagged by the reviewer or the build verifier (LLM-assisted)."""

from __future__ import annotations

from ..config import get_llm
from ..prompts import COMPONENT_FIX
from .llm_support import accept_llm_output, child_apis, invoke_component_llm


def fixer_agent(state: dict) -> dict:
    llm = get_llm(state.get("options", {}))
    iteration = state.get("fix_iteration", 0) + 1
    updated, log = {}, [f"[fixer] iteration {iteration}"]
    elements = state.get("elements", {})
    for tag, comp in state.get("components", {}).items():
        errors = [i for i in comp.get("issues", []) if i["severity"] == "error"]
        if not errors or comp.get("status") == "failed" or llm is None:
            continue
        element = elements.get(tag, {})
        prompt = COMPONENT_FIX.format(
            tag=tag, class_name=comp["class_name"],
            issues="\n".join(f"- [{i['file']}] {i['message']}" for i in errors),
            ts_path=comp["ts_path"], ts=comp["ts"], html_path=comp["html_path"], html=comp["html"],
            scss_path=comp["scss_path"], scss=comp["scss"], child_apis=child_apis(comp, state.get("registry", {})),
            polymer_source=(element.get("template") or "") + "\n\n" + "\n".join(
                f"{m['name']}({m.get('params', '')}) {{{m.get('body', '')}}}" for m in element.get("methods", [])))
        try:
            files = invoke_component_llm(llm, prompt)
            ok, reason = accept_llm_output(comp, files)
            if ok:
                updated[tag] = {**comp, "ts": files.ts, "html": files.html, "scss": files.scss,
                                "build_errors": [], "llm_refined": True,
                                "notes": comp.get("notes", []) + [f"LLM fix: {n}" for n in files.notes]}
                log.append(f"[fixer] <{tag}> fixed {len(errors)} issue(s)")
            else:
                log.append(f"[fixer] <{tag}> fix rejected ({reason})")
        except Exception as exc:
            log.append(f"[fixer] <{tag}> fix failed: {exc}")
    return {"components": updated, "fix_iteration": iteration, "log": log}
