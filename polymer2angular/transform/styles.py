"""Polymer CSS (custom-property mixins, ::slotted, shady-dom selectors) -> Angular SCSS."""

from __future__ import annotations

import re

from ..utils.js_scanner import dedent_block, find_matching

IRON_FLEX_MIXINS = {
    "--layout": "display: flex;",
    "--layout-inline": "display: inline-flex;",
    "--layout-horizontal": "display: flex; flex-direction: row;",
    "--layout-horizontal-reverse": "display: flex; flex-direction: row-reverse;",
    "--layout-vertical": "display: flex; flex-direction: column;",
    "--layout-vertical-reverse": "display: flex; flex-direction: column-reverse;",
    "--layout-wrap": "flex-wrap: wrap;",
    "--layout-flex": "flex: 1; flex-basis: 0.000000001px;",
    "--layout-flex-auto": "flex: 1 1 auto;",
    "--layout-flex-none": "flex: none;",
    "--layout-center": "align-items: center;",
    "--layout-start": "align-items: flex-start;",
    "--layout-end": "align-items: flex-end;",
    "--layout-center-justified": "justify-content: center;",
    "--layout-start-justified": "justify-content: flex-start;",
    "--layout-end-justified": "justify-content: flex-end;",
    "--layout-justified": "justify-content: space-between;",
    "--layout-around-justified": "justify-content: space-around;",
    "--layout-center-center": "display: flex; align-items: center; justify-content: center;",
    "--layout-fit": "position: absolute; top: 0; right: 0; bottom: 0; left: 0;",
    "--layout-fixed-top": "position: fixed; top: 0; left: 0; right: 0;",
    "--layout-scroll": "-webkit-overflow-scrolling: touch; overflow: auto;",
}

IRON_FLEX_INCLUDES = {"iron-flex", "iron-flex-reverse", "iron-flex-alignment", "iron-flex-factors",
                      "iron-positioning", "iron-flex-layout-classes"}

_MIXIN_DEF = re.compile(r"(--[\w-]+)\s*:\s*\{")


def extract_mixins(css: str) -> tuple[str, dict[str, str]]:
    """Remove `--name: { ... };` definitions, returning (css, {name: declarations})."""
    mixins: dict[str, str] = {}
    out = []
    pos = 0
    for m in _MIXIN_DEF.finditer(css):
        if m.start() < pos:
            continue
        brace = m.end() - 1
        end = find_matching(css, brace)
        if end == -1:
            continue
        mixins[m.group(1)] = " ".join(css[brace + 1:end].split())
        out.append(css[pos:m.start()])
        pos = end + 1
        while pos < len(css) and css[pos] in " \t;":
            pos += 1
    out.append(css[pos:])
    return "".join(out), mixins


def convert_css(css: str, known_mixins: dict[str, str] | None = None, todos: list[str] | None = None,
                component: bool = True) -> str:
    todos = todos if todos is not None else []
    css, local = extract_mixins(css)
    mixins = {**IRON_FLEX_MIXINS, **(known_mixins or {}), **local}

    def apply(m: re.Match) -> str:
        name = m.group(1)
        if name in mixins:
            return mixins[name]
        msg = f"CSS mixin '@apply {name}' is not supported in Angular; replace with explicit declarations"
        if msg not in todos:
            todos.append(msg)
        return f"/* TODO(migration): @apply {name}; */"

    css = re.sub(r"@apply\s*\(?\s*(--[\w-]+)\s*\)?\s*;?", apply, css)
    css = re.sub(r":host(\([^)]*\))?\s*::slotted\(([^)]*)\)", r":host\1 ::ng-deep \2", css)
    css = re.sub(r"(^\s*|[,{}]\s*)::slotted\(([^)]*)\)", r"\1:host ::ng-deep \2", css, flags=re.M)
    css = re.sub(r"::slotted\(([^)]*)\)", r" ::ng-deep \1", css)
    css = re.sub(r"\s*(/deep/|::shadow|>>>)\s*", " ::ng-deep ", css)
    if component:
        css = re.sub(r":root\b", ":host", css)
    css = re.sub(r"\n{3,}", "\n\n", dedent_block(css))
    return css.strip() + "\n" if css.strip() else ""


def build_component_scss(styles: list[str], includes: list[str], style_modules: set[str],
                         known_mixins: dict[str, str], todos: list[str]) -> str:
    header = []
    for inc in includes:
        if inc in style_modules:
            header.append(f"@use '{inc}';")
        elif inc in IRON_FLEX_INCLUDES:
            header.append("@use 'iron-flex-layout';")
        else:
            header.append(f"/* TODO(migration): shared style module '{inc}' not found in source */")
            todos.append(f"Style include '{inc}' could not be resolved")
    body = "\n".join(convert_css(s, known_mixins, todos) for s in styles)
    return "\n".join(dict.fromkeys(header)) + ("\n\n" if header and body else "") + body


IRON_FLEX_SCSS = """// Replacement for Polymer's iron-flex-layout classes.
.layout { display: flex; }
.layout.inline { display: inline-flex; }
.layout.horizontal { flex-direction: row; }
.layout.vertical { flex-direction: column; }
.layout.wrap { flex-wrap: wrap; }
.layout.center, .layout.center-center { align-items: center; }
.layout.start { align-items: flex-start; }
.layout.end { align-items: flex-end; }
.layout.center-justified, .layout.center-center { justify-content: center; }
.layout.start-justified { justify-content: flex-start; }
.layout.end-justified { justify-content: flex-end; }
.layout.justified { justify-content: space-between; }
.layout.around-justified { justify-content: space-around; }
.flex { flex: 1; flex-basis: 0.000000001px; }
.flex-auto { flex: 1 1 auto; }
.flex-none { flex: none; }
.self-start { align-self: flex-start; }
.self-center { align-self: center; }
.self-end { align-self: flex-end; }
.self-stretch { align-self: stretch; }
.fit { position: absolute; top: 0; right: 0; bottom: 0; left: 0; }
.relative { position: relative; }
.fixed-top { position: fixed; top: 0; left: 0; right: 0; }
.scroll { -webkit-overflow-scrolling: touch; overflow: auto; }
"""
