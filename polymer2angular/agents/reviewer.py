"""Quality Reviewer agent: static validation of every migrated component."""

from __future__ import annotations

import re

from ..utils.js_scanner import skip_non_code

TS_FORBIDDEN = [
    (r"\bPolymer\.", "Polymer global API still referenced"),
    (r"this\.\$\.", "this.$ node map still referenced"),
    (r"this\.fire\(", "this.fire() still used"),
    (r"this\.(?:set|push|splice|notifyPath|notifySplices)\(\s*['\"]", "Polymer path API still used"),
    (r"customElements\.define", "customElements.define still present"),
    (r"@polymer/", "@polymer import still present"),
    (r"\bPolymerElement\b", "PolymerElement still referenced"),
    (r"\bhtml`", "Polymer html`` template literal still present"),
    (r"@(Input|Output|ViewChild)\(", "Decorator-based API used; use signal-based input()/output()/viewChild()"),
    (r"standalone:\s*true", "standalone: true is redundant in Angular 21"),
]
HTML_FORBIDDEN = [
    (r"\[\[", "Polymer one-way binding [[...]] left in template"),
    (r"\{\{[^}]*::", "Polymer two-way native binding {{x::event}} left in template"),
    (r"\son-[\w-]+\s*=", "Polymer on-* event attribute left in template"),
    (r"is=[\"']dom-(?:if|repeat|bind)", "Polymer dom-if/dom-repeat template left in template"),
    (r"<dom-(?:if|repeat|bind)\b", "Polymer dom-if/dom-repeat element left in template"),
    (r"\s[\w-]+\$=", "Polymer attribute binding (attr$=) left in template"),
    (r"<slot\b", "<slot> must be <ng-content>"),
    (r"<content\b", "<content> must be <ng-content>"),
    (r"\*ng(?:If|For)\b", "Legacy structural directive; use @if/@for"),
]
JS_GLOBALS = {"$any", "toAttr", "if", "for", "switch", "case", "track", "Math", "String", "Number", "Boolean",
              "JSON", "Array", "Object", "Date", "parseInt", "parseFloat", "isNaN", "encodeURIComponent"}


def _strip_comments_html(html: str) -> str:
    return re.sub(r"<!--[\s\S]*?-->", "", html)


def _balanced(ts: str) -> bool:
    depth = 0
    i = 0
    while i < len(ts):
        j = skip_non_code(ts, i)
        if j != i:
            i = j
            continue
        if ts[i] in "({[":
            depth += 1
        elif ts[i] in ")}]":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def class_members(ts: str) -> set[str]:
    members = set(re.findall(r"^\s{2}(?:(?:readonly|private|protected|public|static|async|get|set)\s+)*"
                             r"([A-Za-z_$][\w$]*)\s*[(=:<!?;]", ts, re.M))
    return members


def template_calls(html: str) -> set[str]:
    html = _strip_comments_html(html)
    exprs = re.findall(r"\{\{([\s\S]*?)\}\}", html)
    exprs += re.findall(r"\s[\[(][^\s=]*[\])]\s*=\s*\"([^\"]*)\"", html)
    exprs += re.findall(r"@(?:if|for|switch|case)\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\)", html)
    names = set()
    for expr in exprs:
        expr = re.sub(r"'[^']*'|\"[^\"]*\"", "''", expr)
        names.update(re.findall(r"(?<![\w$.?])([A-Za-z_$][\w$]*)\s*\(", expr))
    return names - JS_GLOBALS


def review_component(comp: dict) -> list[dict]:
    issues: list[dict] = []
    if comp.get("status") == "failed":
        return [{"severity": "error", "file": comp.get("ts_path"), "message": f"Conversion failed: {comp.get('error')}"}]
    ts, html, scss = comp.get("ts", ""), comp.get("html", ""), comp.get("scss", "")
    html_code = _strip_comments_html(html)

    def add(sev, file, msg):
        issues.append({"severity": sev, "file": file, "message": msg})

    for pattern, msg in TS_FORBIDDEN:
        if re.search(pattern, re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", ts)):
            add("error", comp["ts_path"], msg)
    for pattern, msg in HTML_FORBIDDEN:
        if re.search(pattern, html_code):
            add("error", comp["html_path"], msg)
    if not _balanced(ts):
        add("error", comp["ts_path"], "Unbalanced brackets in TypeScript")
    for ref in re.findall(r"viewChild<[^>]*>+\('([\w$]+)'\)", ts):
        if not re.search(rf"#{re.escape(ref)}\b", html):
            add("error", comp["html_path"], f"viewChild('{ref}') has no matching #{ref} template reference")
    members = class_members(ts)
    for name in sorted(template_calls(html)):
        if name not in members:
            add("error", comp["html_path"], f"Template references '{name}()' which is not a member of {comp['class_name']}")
    if "@apply" in re.sub(r"/\*[\s\S]*?\*/", "", scss):
        add("warning", comp["scss_path"], "@apply CSS mixin is not supported")
    for tag in re.findall(r"<((?:iron|paper|app|neon|vaadin)-[\w-]+)", html_code):
        add("warning", comp["html_path"], f"Third-party Polymer element <{tag}> still used")
    todo_count = ts.count("TODO(migration)") + html.count("TODO(migration)")
    if todo_count:
        add("info", comp["ts_path"], f"{todo_count} TODO(migration) markers to review")
    for build_err in comp.get("build_errors", []):
        add("error", build_err.get("file", comp["ts_path"]), "Build: " + build_err["message"])
    return issues


def reviewer_agent(state: dict) -> dict:
    components = state.get("components", {})
    updated, summary = {}, {"errors": 0, "warnings": 0, "by_component": {}}
    for tag, comp in components.items():
        issues = review_component(comp)
        errors = [i for i in issues if i["severity"] == "error"]
        summary["errors"] += len(errors)
        summary["warnings"] += len([i for i in issues if i["severity"] == "warning"])
        summary["by_component"][tag] = len(errors)
        status = comp.get("status")
        if status != "failed":
            status = "needs-fix" if errors else ("converted-with-todos" if comp.get("todos") else "converted")
        updated[tag] = {**comp, "issues": issues, "status": status}
    return {
        "components": updated,
        "review": summary,
        "log": [f"[reviewer] {summary['errors']} errors, {summary['warnings']} warnings across {len(components)} components"],
    }
