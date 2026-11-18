"""Reporter agent: writes MIGRATION_REPORT.md and migration-report.json."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def render_report(state: dict) -> str:
    analysis = state.get("analysis", {})
    comps = state.get("components", {})
    build = state.get("build") or {}
    review = state.get("review", {})
    opts = state.get("options", {})
    mode = f"LLM-assisted ({opts.get('llm_provider')}:{opts.get('llm_model')})" if opts.get("llm_provider") else "rule-based"
    lines = [
        "# Polymer → Angular 21 Migration Report",
        "",
        f"- Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- Source: `{state.get('source_dir')}`",
        f"- Output: `{state.get('output_dir')}`",
        f"- Mode: {mode}",
        f"- Polymer versions detected: {', '.join(analysis.get('polymer_versions', [])) or 'n/a'}",
        f"- Components: {len(comps)} | review errors: {review.get('errors', 0)} | warnings: {review.get('warnings', 0)}",
        f"- Build verification: {build.get('status', 'not run')}",
        "",
    ]
    if analysis.get("strategy"):
        lines += ["## Migration strategy (LLM)", "", analysis["strategy"], ""]
    lines += ["## Components", "", "| Polymer element | Angular class | File | Status | TODOs | LLM |",
              "|---|---|---|---|---|---|"]
    for tag in state.get("plan", {}).get("order", sorted(comps)):
        c = comps.get(tag)
        if not c:
            continue
        lines.append(f"| `<{tag}>` | `{c.get('class_name')}` | `{c.get('ts_path')}` | {c.get('status')} | "
                     f"{len(c.get('todos', []))} | {'yes' if c.get('llm_refined') else 'no'} |")
    lines.append("")
    if analysis.get("third_party_elements") or analysis.get("unknown_elements"):
        lines += ["## Third-party / unresolved elements", ""]
        for e in analysis.get("third_party_elements", []):
            lines.append(f"- `<{e}>` (Polymer catalog element — mapped to Angular Material/native where supported)")
        for e in analysis.get("unknown_elements", []):
            lines.append(f"- `<{e}>` (not found in the repository — kept as a custom element)")
        lines.append("")
    if analysis.get("unresolved_behaviors"):
        lines += ["## Unresolved behaviors / mixins", ""]
        for tag, names in analysis["unresolved_behaviors"].items():
            lines.append(f"- `<{tag}>`: {', '.join(names)} — re-implement as an Angular service or base class")
        lines.append("")
    lines += ["## Details per component", ""]
    for tag in state.get("plan", {}).get("order", sorted(comps)):
        c = comps.get(tag)
        if not c:
            continue
        lines += [f"### `<{tag}>` → `{c.get('class_name')}`", "", f"Source: `{c.get('source_file')}`", ""]
        if c.get("error"):
            lines += [f"**Conversion failed:** `{c['error']}`", ""]
        if c.get("todos"):
            lines += ["**TODOs**", ""] + [f"- [ ] {t}" for t in c["todos"]] + [""]
        if c.get("notes"):
            lines += ["**Notes**", ""] + [f"- {n}" for n in c["notes"]] + [""]
        issues = [i for i in c.get("issues", []) if i["severity"] in ("error", "warning")]
        if issues:
            lines += ["**Review findings**", ""] + [f"- {i['severity']}: {i['message']} (`{i['file']}`)" for i in issues] + [""]
    if analysis.get("project_todos"):
        lines += ["## Project-level TODOs", ""] + [f"- [ ] {t}" for t in analysis["project_todos"]] + [""]
    if build.get("errors"):
        lines += ["## Build errors", ""] + [f"- `{e['file']}:{e['line']}` {e['message']}" for e in build["errors"][:50]] + [""]
    lines += [
        "## Next steps",
        "",
        "1. `cd` into the output folder and run `npm install` then `npm start`.",
        "2. Work through the TODO(migration) markers (search the code base for `TODO(migration)`).",
        "3. Replace emulated Polymer routing (`app-location`/`app-route`) with real Angular routes where appropriate.",
        "4. Move shared state/behaviors into injectable services and tighten TypeScript (`strict: true`).",
        "5. Add unit tests (`ng generate config` / Vitest) and e2e tests to confirm behavioural parity.",
        "",
    ]
    return "\n".join(lines)


def reporter_agent(state: dict) -> dict:
    report = render_report(state)
    if state.get("options", {}).get("dry_run"):
        return {"report_path": "", "log": ["[reporter] dry-run: report not written"]}
    out = Path(state["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    path = out / "MIGRATION_REPORT.md"
    path.write_text(report, encoding="utf-8")
    data = {
        "source_dir": state.get("source_dir"),
        "analysis": {k: v for k, v in state.get("analysis", {}).items() if k != "known_mixins"},
        "review": state.get("review"),
        "build": {k: v for k, v in (state.get("build") or {}).items() if k != "output"},
        "components": {t: {k: c.get(k) for k in ("class_name", "ts_path", "status", "todos", "notes", "issues",
                                                  "llm_refined", "source_file")}
                       for t, c in state.get("components", {}).items()},
        "log": state.get("log", []),
    }
    (out / "migration-report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"report_path": str(path), "log": [f"[reporter] report written to {path}"]}
