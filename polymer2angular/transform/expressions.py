"""Conversion of Polymer binding expressions to Angular template / TypeScript expressions.

Polymer bindings support: property paths (`a.b.0.c`), negation (`!a`), computed
function calls (`fn(a, 'lit', 3)`), and wildcard/splice paths (`a.*`, `a.splices`).
"""

from __future__ import annotations

import re

from ..utils.js_scanner import split_top_level
from .context import ComponentContext

LITERAL_RE = re.compile(r"""^(?:-?\d+(?:\.\d+)?|'[^']*'|"[^"]*"|true|false|null|undefined)$""")
PATH_RE = re.compile(r"^[\w$]+(?:\.[\w$*]+)*$")
CALL_RE = re.compile(r"^([\w$.]+)\s*\((.*)\)$", re.S)
BINDING_RE = re.compile(r"(\[\[|\{\{)(.+?)(\]\]|\}\})", re.S)


def parse_binding_value(value: str) -> list[tuple]:
    """Split an attribute/text value into ('text', s) and ('bind', expr, two_way, event) parts."""
    parts: list[tuple] = []
    pos = 0
    for m in BINDING_RE.finditer(value):
        if m.start() > pos:
            parts.append(("text", value[pos:m.start()]))
        expr, event = m.group(2).strip(), None
        if "::" in expr:
            expr, event = (x.strip() for x in expr.split("::", 1))
        parts.append(("bind", expr, m.group(1) == "{{", event))
        pos = m.end()
    if pos < len(value):
        parts.append(("text", value[pos:]))
    return parts


def has_binding(value: str | None) -> bool:
    return bool(value) and bool(BINDING_RE.search(value))


def convert_path(path: str, ctx: ComponentContext, scope: dict[str, str], mode: str = "template") -> str:
    segs = path.split(".")
    wildcard = segs[-1] in ("*", "splices") and len(segs) > 1
    if wildcard:
        segs = segs[:-1]
    root = segs[0]
    prefix = "this." if mode == "ts" else ""
    rest = "".join(f"?.[{s}]" if s.isdigit() else f"?.{s}" for s in segs[1:])
    if root in scope:
        base = root
    elif root in ctx.methods and root not in ctx.properties:
        base = f"{prefix}{root}"
    else:
        base = f"{prefix}{root}()"
        if mode == "template":
            ctx.template_roots.add(root)
    expr = base + rest
    if wildcard and mode == "ts":
        return f"{{ path: '{path}', value: {expr}, base: {expr} }}"
    return expr


def convert_expr(expr: str, ctx: ComponentContext, scope: dict[str, str], mode: str = "template") -> str:
    expr = expr.strip()
    if not expr:
        return "''"
    if expr.startswith("!"):
        return "!" + convert_expr(expr[1:], ctx, scope, mode)
    if LITERAL_RE.match(expr):
        return expr
    m = CALL_RE.match(expr)
    if m:
        fn = m.group(1)
        args = [convert_expr(a, ctx, scope, mode) for a in split_top_level(m.group(2))]
        target = fn if mode == "template" else f"this.{fn}"
        if fn not in ctx.methods:
            ctx.todo(f"Binding calls '{fn}()' which is not a method of {ctx.class_name}")
        return f"{target}({', '.join(args)})"
    if PATH_RE.match(expr):
        return convert_path(expr, ctx, scope, mode)
    ctx.todo(f"Unsupported binding expression '{expr}' copied verbatim")
    return expr


def binding_root(expr: str) -> str | None:
    expr = expr.strip().lstrip("!")
    if PATH_RE.match(expr):
        return expr.split(".")[0]
    return None


def interpolate_to_ts(value: str, ctx: ComponentContext) -> str:
    """Attribute value with bindings -> TS template literal (`/api/${this.id()}`)."""
    parts = parse_binding_value(value)
    if len(parts) == 1 and parts[0][0] == "bind":
        return convert_expr(parts[0][1], ctx, {}, "ts")
    out = []
    for part in parts:
        if part[0] == "text":
            out.append(part[1].replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${"))
        else:
            out.append("${" + convert_expr(part[1], ctx, {}, "ts") + "}")
    return "`" + "".join(out) + "`"
