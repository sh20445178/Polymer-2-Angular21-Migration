"""Project Assembler agent: generates the Angular 21 workspace around the migrated components."""

from __future__ import annotations

from ..scaffold.angular_project import scaffold_project


def assembler_agent(state: dict) -> dict:
    scan, analysis = state["scan"], state["analysis"]
    index = scan.get("index", {})
    options = state.get("options", {})
    files, todos = scaffold_project(
        name=options.get("project_name", "migrated-app"),
        title=index.get("title") or options.get("project_name", "Migrated App"),
        components=state.get("components", {}),
        registry=state["registry"],
        style_modules=state.get("style_modules", {}),
        index_body=index.get("body", ""),
        head_links=index.get("head_links", []),
        global_css=index.get("global_styles", []),
        root_tags=analysis.get("root_elements", []),
        known_mixins=analysis.get("known_mixins", {}),
    )
    for comp in state.get("components", {}).values():
        if comp.get("status") == "failed":
            continue
        files[comp["ts_path"]] = comp["ts"]
        files[comp["html_path"]] = comp["html"]
        files[comp["scss_path"]] = comp["scss"]
    for asset in scan["files"].get("assets", []):
        files[f"public/{asset}"] = {"copy_from": asset}
    return {"project_files": files,
            "analysis": {**analysis, "project_todos": todos},
            "log": [f"[assembler] {len(files)} project files prepared"]}
