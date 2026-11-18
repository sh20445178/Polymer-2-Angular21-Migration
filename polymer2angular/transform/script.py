"""Polymer element script -> Angular 21 standalone component class (signals based)."""

from __future__ import annotations

import os
import re

from ..utils.js_scanner import (
    dedent_block,
    find_expression_end,
    find_in_code,
    find_matching,
    split_top_level,
    unquote,
)
from ..utils.naming import event_to_output, kebab_to_camel
from .context import ComponentContext
from .expressions import convert_expr

LIFECYCLE = {
    "ready": "ngAfterViewInit",
    "connectedCallback": "ngOnInit",
    "attached": "ngOnInit",
    "disconnectedCallback": "ngOnDestroy",
    "detached": "ngOnDestroy",
    "created": "constructor",
    "constructor": "constructor",
    "beforeRegister": None,
    "registered": None,
}
HOOK_INTERFACES = {"ngOnInit": "OnInit", "ngAfterViewInit": "AfterViewInit", "ngOnDestroy": "OnDestroy"}
TS_TYPES = {"String": "string", "Number": "number", "Boolean": "boolean", "Array": "any[]", "Object": "any",
            "Date": "Date", "Function": "(...args: any[]) => any"}
HOST_DOM_MEMBERS = (
    "querySelector", "querySelectorAll", "getAttribute", "setAttribute", "removeAttribute", "hasAttribute",
    "classList", "style", "getBoundingClientRect", "offsetWidth", "offsetHeight", "clientWidth", "clientHeight",
    "scrollTop", "scrollLeft", "focus", "blur", "click", "children", "parentNode", "parentElement",
    "dispatchEvent", "addEventListener", "removeEventListener", "textContent", "innerHTML", "contains",
    "shadowRoot", "root", "localName", "tagName", "nodeName",
)
POLYMER_PATH_APIS = ("set", "get", "push", "pop", "shift", "unshift", "splice", "notifyPath", "notifySplices",
                     "linkPaths", "unlinkPaths")
SIG = "$SIG$"
HOST_LISTENER_MARK = "void 0 /* converted to a host listener */"
REF = "__REF__"


# --------------------------------------------------------------------------- analysis helpers (also used by planner)


def _all_bodies(model: dict) -> str:
    return "\n".join(m.get("body", "") for m in model.get("methods", []))


def collect_events(model: dict) -> list[str]:
    body = _all_bodies(model)
    events = re.findall(r"this\.fire\(\s*['\"]([\w:.-]+)['\"]", body)
    events += re.findall(r"new\s+CustomEvent\(\s*['\"]([\w:.-]+)['\"]", body)
    return list(dict.fromkeys(events))


def compute_outputs(model: dict, model_props: set[str]) -> dict[str, str]:
    taken = {p["name"] for p in model.get("properties", [])} | {m["name"] for m in model.get("methods", [])}
    outputs = {}
    for event in collect_events(model):
        if event.endswith("-changed") and kebab_to_camel(event[:-8]) in model_props:
            continue
        name = event_to_output(event)
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
            name = re.sub(r"[^\w$]", "_", name)
        if name in taken:
            name += "Event"
        outputs[event] = name
        taken.add(name)
    return outputs


def written_properties(model: dict) -> set[str]:
    body = _all_bodies(model)
    names = set(re.findall(r"this\.([\w$]+)\s*(?:=(?![=>])|\+=|-=|\*=|/=|%=|\+\+|--|\|\|=|&&=|\?\?=)", body))
    names |= set(re.findall(r"(?:\+\+|--)\s*this\.([\w$]+)", body))
    names |= set(re.findall(r"this\.(?:set|push|pop|shift|unshift|splice|notifyPath|notifySplices)\(\s*['\"]([\w$]+)",
                            body))
    return names


def template_two_way_roots(template: str | None) -> set[str]:
    return set(re.findall(r"\{\{\s*([\w$]+)\s*(?:::[\w-]+)?\s*\}\}", template or ""))


def classify_properties(model: dict, extra_writable: set[str] | None = None) -> dict[str, str]:
    writable = written_properties(model) | (extra_writable or set())
    kinds = {}
    for prop in model.get("properties", []):
        name = prop["name"]
        if prop.get("computed"):
            kinds[name] = "computed"
        elif name.startswith("_") or prop.get("read_only"):
            kinds[name] = "signal"
        elif prop.get("notify") or name in writable:
            kinds[name] = "model"
        else:
            kinds[name] = "input"
    return kinds


def parse_call_signature(sig: str) -> tuple[str, list[str]]:
    m = re.match(r"\s*([\w$.]+)\s*\((.*)\)\s*$", sig, re.S)
    if not m:
        return sig.strip(), []
    return m.group(1), [a.strip() for a in split_top_level(m.group(2))]


def first_param(params: str) -> str | None:
    m = re.match(r"\s*([\w$]+)", params or "")
    return m.group(1) if m else None


def methods_using_model(model: dict) -> set[str]:
    out = set()
    for m in model.get("methods", []):
        p = first_param(m.get("params", ""))
        if p and re.search(rf"\b{re.escape(p)}\.model\b", m.get("body", "")):
            out.add(m["name"])
    return out


def dollar_ids(model: dict) -> set[str]:
    body = _all_bodies(model)
    ids = set(re.findall(r"this\.\$\.([\w$]+)", body))
    ids |= set(re.findall(r"this\.\$\[\s*['\"]([\w$-]+)['\"]\s*\]", body))
    return ids


def ts_type(polymer_type: str | None) -> str:
    return TS_TYPES.get((polymer_type or "").strip(), "any")


# --------------------------------------------------------------------------- body rewriting


def replace_calls(src: str, pattern: str, fn) -> str:
    """Replace call expressions matched by `pattern` (which must end with `(`)."""
    rx = re.compile(pattern)
    out, pos = [], 0
    while True:
        m = find_in_code(src, rx, pos)
        if not m:
            break
        open_idx = m.end() - 1
        close = find_matching(src, open_idx)
        if close == -1:
            break
        args = split_top_level(src[open_idx + 1:close])
        rep = fn(m, args)
        if rep is None:
            out.append(src[pos:m.end()])
            pos = m.end()
            continue
        out.append(src[pos:m.start()])
        out.append(rep)
        pos = close + 1
    out.append(src[pos:])
    return "".join(out)


class ScriptConverter:
    def __init__(self, model: dict, ctx: ComponentContext, kinds: dict[str, str], outputs: dict[str, str]):
        self.model = model
        self.ctx = ctx
        self.kinds = kinds
        self.outputs = outputs
        self.signals = set(kinds)
        self.member_names = {m["name"] for m in model.get("methods", [])}
        self.uses: set[str] = set()  # compat helpers
        self.needs_host = False
        self.needs_injector = False
        self.needs_debouncer = False
        self.host_listeners: dict[str, str] = {}

    # ---------------------------------------------------------------- helpers
    def _path_target(self, path_arg: str) -> tuple[str | None, str]:
        """`'items.0.name'` -> ('items', "'0.name'"); `'todos.' + i + '.done'` -> ('todos', "'' + i + '.done'")."""
        path_arg = path_arg.strip()
        if not path_arg or path_arg[0] not in "'\"`":
            return None, path_arg
        quote = path_arg[0]
        end = path_arg.find(quote, 1)
        literal = path_arg[1:end]
        root, _, rest_literal = literal.partition(".")
        if not re.fullmatch(r"[\w$]+", root or ""):
            return None, path_arg
        rel = quote + rest_literal + path_arg[end:]
        return root, rel

    def _ensure_signal(self, root: str) -> bool:
        if root in self.signals:
            return True
        if root in self.member_names:
            return False
        self.signals.add(root)
        self.kinds[root] = "signal"
        self.ctx.notes.append(f"'{root}' was not declared in Polymer 'properties'; declared as signal")
        return True

    # ---------------------------------------------------------------- polymer API rewrites
    def _rewrite_path_api(self, m: re.Match, args: list[str]) -> str | None:
        api = m.group(1)
        if not args:
            return None
        if api in ("linkPaths", "unlinkPaths"):
            self.ctx.todo(f"this.{api}() has no signal equivalent; use computed() instead")
            return "void 0 /* TODO(migration): " + api + " */"
        root, rel = self._path_target(args[0])
        if root is None or not self._ensure_signal(root):
            self.ctx.todo(f"this.{api}({args[0]}, ...) uses a dynamic path; migrate manually")
            return None
        rest = [self.rewrite(a) for a in args[1:]]
        rel_empty = rel.strip() in ("''", '""', "``")
        target = f"this.{SIG}{root}"
        if api == "set":
            if rel_empty:
                return f"{target}.set({rest[0] if rest else 'undefined'})"
            self.uses.add("setPath")
            return f"setPath({target}, {rel}, {rest[0] if rest else 'undefined'})"
        if api == "get":
            if rel_empty:
                return f"this.{root}"
            self.uses.add("getPath")
            return f"getPath(this.{root}, {rel})"
        if api in ("notifyPath", "notifySplices"):
            self.uses.add("notifyChange")
            return f"notifyChange({target})"
        helper = {"push": "arrayPush", "pop": "arrayPop", "shift": "arrayShift", "unshift": "arrayUnshift",
                  "splice": "arraySplice"}[api]
        self.uses.add(helper)
        return f"{helper}({', '.join([target, rel] + rest)})"

    def _emit(self, event: str, detail: str | None) -> str:
        prop = kebab_to_camel(event[:-8]) if event.endswith("-changed") else None
        if prop and self.kinds.get(prop) == "model":
            return f"void 0 /* '{event}' is emitted automatically by the {prop} model() signal */"
        name = self.outputs.get(event) or event_to_output(event)
        return f"this.{name}.emit({self.rewrite(detail) if detail else 'undefined'})"

    def _rewrite_fire(self, m: re.Match, args: list[str]) -> str | None:
        if not args or args[0].strip()[0] not in "'\"":
            self.ctx.todo("this.fire() with a dynamic event name; migrate manually")
            return None
        return self._emit(unquote(args[0]), args[1] if len(args) > 1 else None)

    def _rewrite_dispatch(self, m: re.Match, args: list[str]) -> str | None:
        if not args:
            return None
        arg = args[0].strip()
        cm = re.match(r"new\s+CustomEvent\s*\(", arg)
        if cm:
            inner = arg[cm.end() - 1:]
            inner_args = split_top_level(inner[1:find_matching(inner, 0)])
            if inner_args and inner_args[0][0] in "'\"":
                detail = None
                if len(inner_args) > 1 and inner_args[1].strip().startswith("{"):
                    opts = inner_args[1].strip()
                    dm = find_in_code(opts, re.compile(r"\bdetail\s*:\s*"))
                    if dm:
                        detail = opts[dm.end():find_expression_end(opts, dm.end())].strip()
                    elif re.search(r"[{,]\s*detail\s*[,}]", opts):
                        detail = "detail"
                return self._emit(unquote(inner_args[0]), detail)
        self.needs_host = True
        return f"this.host.nativeElement.dispatchEvent({self.rewrite(arg)})"

    def _rewrite_listener(self, m: re.Match, args: list[str]) -> str | None:
        if len(args) == 2 and args[0].strip()[:1] in "'\"" and re.fullmatch(r"this\.([\w$]+)", args[1].strip()):
            handler = args[1].strip()[5:]
            event = unquote(args[0])
            self.host_listeners[f"({event})"] = f"{handler}({', '.join(self.ctx.handler_args(handler, ['$event']))})"
            return HOST_LISTENER_MARK
        self.needs_host = True
        return None

    def rewrite_calls(self, body: str) -> str:
        body = replace_calls(body, r"this\.\$\.([\w$]+)\.generateRequest\s*\(", self._rewrite_generate_request)
        body = replace_calls(body, r"this\.fire\s*\(", self._rewrite_fire)
        body = replace_calls(body, r"this\.dispatchEvent\s*\(", self._rewrite_dispatch)
        body = replace_calls(body, r"this\.(?:add|remove)EventListener\s*\(", self._rewrite_listener)
        body = replace_calls(body, r"this\.(" + "|".join(POLYMER_PATH_APIS) + r")\s*\(", self._rewrite_path_api)
        body = replace_calls(body, r"this\.async\s*\(", lambda m, a: f"setTimeout({', '.join(a)})")
        body = replace_calls(body, r"this\.debounce\s*\(", self._rewrite_debounce)
        body = replace_calls(body, r"this\.(cancelDebouncer|isDebouncerActive|flushDebouncer)\s*\(", self._rewrite_debouncer_api)
        body = replace_calls(body, r"this\.(toggleClass|toggleAttribute)\s*\(", self._rewrite_toggle)
        body = replace_calls(body, r"(?:Polymer\.)?importHref\s*\(",
                             lambda m, a: "void 0 /* importHref removed: Angular bundles components */")
        body = replace_calls(body, r"this\.resolveUrl\s*\(", lambda m, a: a[0] if a else "''")
        body = replace_calls(body, r"(?:Polymer\.RenderStatus\.)?(afterNextRender|beforeNextRender)\s*\(\s*this\s*,",
                             self._rewrite_after_render)
        body = replace_calls(body, r"(?:Polymer\.Async\.)?microTask\.run\s*\(", lambda m, a: f"queueMicrotask({a[0]})")
        body = replace_calls(body, r"(?:Polymer\.)?dom\s*\(", self._rewrite_polymer_dom)
        body = replace_calls(body, r"this\.\$\$\s*\(", lambda m, a: self._host(f"querySelector({', '.join(a)})"))
        body = replace_calls(body, r"this\.(listen|unlisten)\s*\(", self._todo_call)
        return body

    def _host(self, member: str) -> str:
        self.needs_host = True
        return f"this.host.nativeElement.{member}"

    def _rewrite_generate_request(self, m: re.Match, args: list[str]) -> str | None:
        for ajax in self.ctx.ajax:
            if ajax["id"] == m.group(1):
                return f"this.{ajax['method_name']}()"
        return None

    def _rewrite_debounce(self, m, args):
        self.needs_debouncer = True
        return f"this.debouncer.debounce({', '.join(self.rewrite(a) for a in args)})"

    def _rewrite_debouncer_api(self, m, args):
        self.needs_debouncer = True
        method = {"cancelDebouncer": "cancel", "isDebouncerActive": "isActive", "flushDebouncer": "flush"}[m.group(1)]
        return f"this.debouncer.{method}({', '.join(args)})"

    def _rewrite_toggle(self, m, args):
        if len(args) < 1:
            return None
        node = args[2] if len(args) > 2 else "this.host.nativeElement"
        self.needs_host = self.needs_host or len(args) <= 2
        flag = f", {args[1]}" if len(args) > 1 else ""
        if m.group(1) == "toggleClass":
            return f"{node}.classList.toggle({args[0]}{flag})"
        return f"{node}.toggleAttribute({args[0]}{flag})"

    def _rewrite_after_render(self, m, args):
        self.needs_injector = True
        self.ctx.angular_imports[m.group(1) if m.group(1) == "afterNextRender" else "afterNextRender"] = "@angular/core"
        return f"afterNextRender({args[0] if args else '() => {}'}, {{ injector: this.injector }})"

    def _rewrite_polymer_dom(self, m, args):
        if not args:
            return None
        arg = args[0].strip()
        if arg in ("this", "this.root", "this.shadowRoot"):
            self.needs_host = True
            return "this.host.nativeElement"
        return arg

    def _todo_call(self, m, args):
        self.ctx.todo(f"this.{m.group(1)}() is not supported; use Renderer2.listen or host listeners")
        return None

    # ---------------------------------------------------------------- structural rewrites
    @staticmethod
    def functions_to_arrows(body: str) -> str:
        out, pos = [], 0
        rx = re.compile(r"\bfunction\s*\(")
        while True:
            m = find_in_code(body, rx, pos)
            if not m:
                break
            p_open = m.end() - 1
            p_close = find_matching(body, p_open)
            b_open = body.find("{", p_close)
            b_close = find_matching(body, b_open) if b_open != -1 else -1
            if p_close == -1 or b_close == -1:
                break
            inner = body[b_open + 1:b_close]
            if "this" not in inner or re.search(r"\barguments\b", inner):
                out.append(body[pos:m.end()])
                pos = m.end()
                continue
            out.append(body[pos:m.start()])
            out.append(f"({body[p_open + 1:p_close]}) => {{")
            out.append(ScriptConverter.functions_to_arrows(inner))
            out.append("}")
            pos = b_close + 1
            bind = re.match(r"\s*\.bind\(\s*this\s*\)", body[pos:])
            if bind:
                pos += bind.end()
        out.append(body[pos:])
        return "".join(out)

    def rewrite_assignments(self, body: str) -> str:
        names = sorted((n for n, k in self.kinds.items() if k != "computed"), key=len, reverse=True)
        if not names:
            return body
        alt = "|".join(re.escape(n) for n in names)
        rx = re.compile(rf"this\.({alt})\s*(=(?![=>])|\+=|-=|\*=|/=|%=|\|\|=|&&=|\?\?=)\s*")
        pos = 0
        while True:
            m = find_in_code(body, rx, pos)
            if not m:
                break
            name, op = m.group(1), m.group(2)
            end = find_expression_end(body, m.end())
            rhs = body[m.end():end].rstrip()
            if op == "=":
                rep = f"this.{SIG}{name}.set({rhs})"
            else:
                rep = f"this.{SIG}{name}.update((v) => v {op[:-1]} ({rhs}))"
            body = body[:m.start()] + rep + body[m.end() + len(rhs):]
            pos = m.start() + len(f"this.{SIG}{name}.set(")
        body = re.sub(rf"this\.({alt})\s*(\+\+|--)",
                      lambda mm: f"this.{SIG}{mm.group(1)}.update((v) => v {mm.group(2)[0]} 1)", body)
        body = re.sub(rf"(\+\+|--)\s*this\.({alt})\b",
                      lambda mm: f"this.{SIG}{mm.group(2)}.update((v) => v {mm.group(1)[0]} 1)", body)
        return body

    def rewrite_reads(self, body: str) -> str:
        names = sorted(self.signals, key=len, reverse=True)
        if not names:
            return body
        alt = "|".join(re.escape(n) for n in names)
        return re.sub(rf"this\.({alt})(?![\w$])(?!\s*=[^=>])", r"this.\1()", body)

    def rewrite_refs(self, body: str) -> str:
        ajax_ids = {a["id"] for a in self.ctx.ajax}

        def ref(m: re.Match) -> str:
            ident = m.group(1) or m.group(2)
            if ident in ajax_ids:
                self.ctx.todo(f"Direct access to <iron-ajax id='{ident}'> properties needs manual migration")
            name = kebab_to_camel(ident)
            kind = self.ctx.ref_kinds.get(name, "native")
            self.ctx.ref_kinds.setdefault(name, kind)
            return f"this.{name}Ref()!" + (".nativeElement" if kind == "native" else "")

        return re.sub(r"this\.\$(?:\.([\w$]+)|\[\s*['\"]([\w$-]+)['\"]\s*\])", ref, body)

    def rewrite_host_dom(self, body: str) -> str:
        members = [m for m in HOST_DOM_MEMBERS if m not in self.member_names and m not in self.signals]
        alt = "|".join(members)

        def rep(m: re.Match) -> str:
            self.needs_host = True
            member = m.group(1)
            if member in ("shadowRoot", "root"):
                return "this.host.nativeElement"
            return f"this.host.nativeElement.{member}"

        return re.sub(rf"this\.({alt})\b", rep, body)

    def rewrite(self, body: str, method: dict | None = None) -> str:
        """Rewrite an arbitrary JS snippet (method body / expression)."""
        body = re.sub(r"\bsuper\.(?:ready|connectedCallback|disconnectedCallback|attached|detached|created|"
                      r"attributeChangedCallback)\s*\([^)]*\)\s*;?", "", body) if method else body
        body = re.sub(r"^\s*super\s*\([^)]*\)\s*;?", "", body, flags=re.M) if method else body
        body = self.functions_to_arrows(body)
        body = self.rewrite_calls(body)
        body = self.rewrite_refs(body)
        body = self.rewrite_host_dom(body)
        if method and method["name"] in self.ctx.model_handlers:
            p = first_param(method.get("params", ""))
            body = re.sub(rf"\b{re.escape(p)}\.model\b", "model", body)
        body = self.rewrite_assignments(body)
        return body

    def finalize(self, body: str) -> str:
        body = self.rewrite_reads(body)
        body = re.sub(rf"^[ \t]*{re.escape(HOST_LISTENER_MARK)};?[ \t]*\n?", "", body, flags=re.M)
        return body.replace(SIG, "")

    # ---------------------------------------------------------------- generation
    def convert_method(self, method: dict, name: str | None = None) -> str:
        body = self.finalize(self.rewrite(method.get("body", ""), method))
        params = method.get("params", "")
        if method["name"] in self.ctx.model_handlers:
            params = (params + ", model?: any") if params.strip() else "_event?: any, model?: any"
        prefix = ("static " if method.get("is_static") else "") + ("async " if method.get("is_async") else "")
        if method.get("kind") in ("get", "set"):
            prefix += method["kind"] + " "
        return f"{prefix}{name or method['name']}({params}) {{\n{indent(dedent_block(body), 2)}\n}}"

    def observer_effect(self, method: str, args: list[str], single_prop: str | None = None) -> str:
        if single_prop:
            local = re.sub(r"\W", "_", single_prop)
            arity = self._method_arity(method)
            lines = [f"const {local} = this.{single_prop}();"]
            if arity >= 2:
                return (f"let previous_{local}: any;\neffect(() => {{\n  {lines[0]}\n"
                        f"  if ({local} === undefined && previous_{local} === undefined) return;\n"
                        f"  untracked(() => this.{method}({local}, previous_{local}));\n"
                        f"  previous_{local} = {local};\n}});")
            return (f"effect(() => {{\n  {lines[0]}\n  if ({local} === undefined) return;\n"
                    f"  untracked(() => this.{method}({local}));\n}});")
        conv = [convert_expr(a, self.ctx, {}, "ts") for a in args]
        locals_ = [f"arg{i}" for i in range(len(conv))]
        decls = "\n".join(f"  const {n} = {c};" for n, c in zip(locals_, conv))
        guard = (f"\n  if ([{', '.join(locals_)}].some((v) => v === undefined)) return;"
                 if locals_ and not any(a.endswith((".*", ".splices")) for a in args) else "")
        call_args = locals_[: self.ctx.method_arity.get(method, len(locals_))]
        return (f"effect(() => {{\n{decls}{guard}\n"
                f"  untracked(() => this.{method}({', '.join(call_args)}));\n}});")

    def _method_arity(self, name: str) -> int:
        return self.ctx.method_arity.get(name, 1)


def indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + ln if ln.strip() else "" for ln in text.split("\n"))


def relative_import(from_file: str, to_file_no_ext: str) -> str:
    rel = os.path.relpath(to_file_no_ext, os.path.dirname(from_file)).replace(os.sep, "/")
    return rel if rel.startswith(".") else "./" + rel
