"""Writer agent: persists the generated Angular workspace to disk."""

from __future__ import annotations

import shutil
from pathlib import Path


def write_project(output_dir: str, source_dir: str, files: dict) -> list[str]:
    out = Path(output_dir).resolve()
    src_root = Path(source_dir).resolve()
    written = []
    for rel, content in sorted(files.items()):
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, dict) and "copy_from" in content:
            shutil.copy2(src_root / content["copy_from"], target)
        else:
            target.write_text(content, encoding="utf-8")
        written.append(rel)
    return written


def writer_agent(state: dict) -> dict:
    if state.get("options", {}).get("dry_run"):
        return {"written_files": [], "log": ["[writer] dry-run: no files written"]}
    written = write_project(state["output_dir"], state["source_dir"], state.get("project_files", {}))
    return {"written_files": written, "log": [f"[writer] wrote {len(written)} files to {state['output_dir']}"]}
