"""Assemble an Angular 21 standalone component (ts/html/scss) from a Polymer element model."""

from __future__ import annotations

import re

from ..utils.js_scanner import split_top_level
from .context import ComponentContext
from .expressions import LITERAL_RE, convert_expr
from .script import (
    HOOK_INTERFACES,
    LIFECYCLE,
    ScriptConverter,
    dollar_ids,
    indent,
    methods_using_model,
    parse_call_signature,
    relative_import,
    ts_type,
)
from .styles import build_component_scss
from .template import TemplateConverter

COMPAT_PATH = "src/app/shared/polymer-compat"
CORE_SYMBOLS = ["DestroyRef", "input", "model", "signal", "computed", "output", "effect", "untracked", "viewChild", "ElementRef",
                "inject", "booleanAttribute", "numberAttribute", "CUSTOM_ELEMENTS_SCHEMA", "Injector",
                "afterNextRender", "OnInit", "AfterViewInit", "OnDestroy"]
COMPAT_SYMBOLS = ["setPath", "getPath", "arrayPush", "arrayPop", "arrayShift", "arrayUnshift", "arraySplice",
                  "notifyChange", "toAttribute", "Debouncer", "matchRoute", "locationToRoute"]


def _default_literal(prop: dict, kind: str) -> str | None:
    default = prop.get("default")
    if default is None and (prop.get("type") or "") == "Boolean" and kind in ("input", "model"):
        return "false"
    return default


def _prop_decl(name: str, prop: dict, kind: str) -> str:
    t = ts_type(prop.get("type"))
    default = _default_literal(prop, kind)
    if kind == "input":
        if t == "boolean":
            return f"readonly {name} = input({default}, {{ transform: booleanAttribute }});"
        if t == "number" and default is not None:
            return f"readonly {name} = input({default}, {{ transform: numberAttribute }});"
        return f"readonly {name} = input<{t}>({default or ''});"
    if kind == "model":
        return f"readonly {name} = model<{t}>({default or ''});"
    if default is None:
        return f"readonly {name} = signal<{t} | undefined>(undefined);"
    return f"readonly {name} = signal<{t}>({default});"


def build_context(element: dict, plan_entry: dict, registry: dict, style_modules: set[str]) -> ComponentContext:
    kinds = plan_entry["kinds"]
    return ComponentContext(
        tag=element["tag"],
        class_name=plan_entry["class_name"],
        file_path=plan_entry["ts_path"],
        properties={p["name"]: {**p, "kind": kinds.get(p["name"])} for p in element.get("properties", [])},
        methods={m["name"] for m in element.get("methods", [])},
        method_arity={m["name"]: (99 if re.search(r"\barguments\b", m.get("body", "")) else
                                  len([p for p in split_top_level(m.get("params", "")) if p.strip()]))
                      for m in element.get("methods", [])},
        model_handlers=methods_using_model(element),
        dollar_ids=dollar_ids(element),
        registry=registry,
        style_modules=style_modules,
    )


def migrate_component(element: dict, plan_entry: dict, registry: dict, style_modules: set[str],
                      known_mixins: dict[str, str] | None = None) -> dict:
    ctx = build_context(element, plan_entry, registry, style_modules)
    kinds = dict(plan_entry["kinds"])
    outputs = dict(plan_entry["outputs"])

    html = TemplateConverter(ctx).convert(element.get("template") or "")

    for root in ctx.two_way_targets:
        if kinds.get(root) == "input":
            kinds[root] = "model"
    for root in sorted(ctx.template_roots):
        if root not in kinds and root not in ctx.methods:
            kinds[root] = "signal"
            ctx.notes.append(f"'{root}' is used in the template but was not declared; declared as signal")
            ctx.properties[root] = {"name": root, "type": None, "default": None, "kind": "signal"}

    sc = ScriptConverter(element, ctx, kinds, outputs)
    ts = _assemble_ts(element, plan_entry, ctx, sc, kinds, outputs, registry)

    scss_todos: list[str] = []
    scss = build_component_scss(element.get("styles", []), element.get("style_includes", []), style_modules,
                                known_mixins or {}, scss_todos)
    for t in scss_todos:
        ctx.todo(t)
    for w in element.get("warnings", []):
        ctx.todo(w)
    if ctx.external_elements:
        ctx.todo("Third-party custom elements kept as web components (CUSTOM_ELEMENTS_SCHEMA): "
                 + ", ".join(sorted(ctx.external_elements)))

    return {
        "tag": element["tag"],
        "class_name": plan_entry["class_name"],
        "ts_path": plan_entry["ts_path"],
        "html_path": plan_entry["ts_path"][:-3] + ".html",
        "scss_path": plan_entry["ts_path"][:-3] + ".scss",
        "ts": ts,
        "html": html,
        "scss": scss,
        "kinds": kinds,
        "outputs": outputs,
        "todos": ctx.todos,
        "notes": list(dict.fromkeys(ctx.notes)),
        "material": ctx.material,
        "uses_router": bool(ctx.route_bindings) or "RouterLink" in ctx.angular_imports,
        "uses_http": bool(ctx.ajax),
        "external_elements": sorted(ctx.external_elements),
        "source_file": element.get("source_file"),
        "status": "converted",
        "issues": [],
        "llm_refined": False,
    }


def _assemble_ts(element: dict, plan_entry: dict, ctx: ComponentContext, sc: ScriptConverter,
                 kinds: dict[str, str], outputs: dict[str, str], registry: dict) -> str:
    props = {p["name"]: p for p in element.get("properties", [])}
    # Signals written by emulated app-route / iron-ajax receive structured values: type them loosely.
    loose = {rb[k] for rb in ctx.route_bindings for k in ("data", "tail", "route") if rb.get(k)}
    loose |= {a[k] for a in ctx.ajax for k in ("last_response", "last_error") if a.get(k)}
    for name in loose & set(props):
        props[name] = {**props[name], "type": None}
    members: list[str] = []
    ctor: list[str] = []
    hooks: dict[str, list[str]] = {}
    methods_out: list[str] = []

    # ---- lifecycle & methods (converted first so that helper usage is known)
    for method in element.get("methods", []):
        hook = LIFECYCLE.get(method["name"], "__method__") if not method.get("is_static") else "__method__"
        if hook is None:
            ctx.todo(f"Polymer lifecycle '{method['name']}' has no Angular equivalent and was dropped")
            continue
        if hook == "__method__":
            if method["name"] == "attributeChangedCallback":
                ctx.todo("attributeChangedCallback has no Angular equivalent; use input() signals with effect()")
            methods_out.append(sc.convert_method(method))
            continue
        body = sc.finalize(sc.rewrite(method.get("body", ""), method)).strip()
        if body:
            (ctor if hook == "constructor" else hooks.setdefault(hook, [])).append(body)

    # ---- observers & computed
    effects: list[str] = []
    for name, prop in props.items():
        if prop.get("observer"):
            effects.append(sc.observer_effect(prop["observer"], [], single_prop=name))
    for obs in element.get("observers", []):
        fn, args = parse_call_signature(obs)
        effects.append(sc.observer_effect(fn, args))

    computed_decls = []
    for name, prop in props.items():
        if kinds.get(name) == "computed":
            computed_decls.append(_computed_decl(name, prop["computed"], ctx, props))
    for name, body in ctx.extra_computed:
        computed_decls.append(f"readonly {name} = computed(() => {{\n{indent(body, 2)}\n}});")

    # ---- routing (app-location / app-route)
    route_members = []
    if ctx.route_bindings:
        route_members.append("private readonly router = inject(Router);")
        route_members.append(
            "private readonly currentUrl = toSignal(\n  this.router.events.pipe(\n"
            "    filter((e) => e instanceof NavigationEnd),\n"
            "    map((e) => (e as NavigationEnd).urlAfterRedirects),\n  ),\n"
            "  { initialValue: inject(Location).path() || '/' },\n);")
        for rb in ctx.route_bindings:
            if rb["tag"] == "app-location" and rb["route"]:
                effects.insert(0, f"effect(() => {{\n  const url = this.currentUrl();\n"
                                  f"  untracked(() => this.{rb['route']}.set(locationToRoute(url)));\n}});")
            elif rb["tag"] == "app-route" and rb["route"]:
                sets = [f"this.{rb[k]}.set(match.{k});" for k in ("data", "tail", "active") if rb[k]]
                effects.append(
                    f"effect(() => {{\n  const match = matchRoute({rb['pattern']!r}, this.{rb['route']}());\n"
                    f"  untracked(() => {{\n" + "".join(f"    {s}\n" for s in sets) + "  });\n});")
        sc.uses.update({"matchRoute", "locationToRoute"})
        ctx.todo("Polymer app-route two-way URL updates: navigate with Router.navigate() when route data changes")

    # ---- iron-ajax
    ajax_members, ajax_methods = [], []
    for ajax in ctx.ajax:
        cfg_name = re.sub(r"Request$", "", ajax["method_name"][len("generate"):])
        cfg_name = cfg_name[:1].lower() + cfg_name[1:] + "Config"
        opts = [f"{k}: {ajax[k]}" for k in ("params", "body", "headers") if ajax.get(k)]
        ajax_members.append(
            f"private readonly {cfg_name} = computed(() => ({{\n  url: {ajax['url']},\n"
            + "".join(f"  {o},\n" for o in opts) + "}));")
        set_loading = lambda v: f"this.{ajax['loading']}.set({v});" if ajax["loading"] else ""  # noqa: E731
        next_lines = [set_loading("false")]
        if ajax["last_response"]:
            next_lines.append(f"this.{ajax['last_response']}.set(response);")
        if ajax["on_response"]:
            next_lines.append(f"this.{ajax['on_response']}({{ detail: {{ response }} }});")
        err_lines = [set_loading("false")]
        if ajax["last_error"]:
            err_lines.append(f"this.{ajax['last_error']}.set(error);")
        if ajax["on_error"]:
            err_lines.append(f"this.{ajax['on_error']}({{ detail: {{ error }} }});")
        else:
            err_lines.append(f"console.error('{ajax['id']} request failed', error);")
        rt = "" if ajax["handle_as"] == "json" else f", responseType: '{'text' if ajax['handle_as'] != 'blob' else 'blob'}' as 'json'"
        body_opts = ", ".join(f"{k}: config.{k}" for k in ("params", "body", "headers") if ajax.get(k))
        options = f", {{ {body_opts}{rt} }}" if (body_opts or rt) else ""
        ajax_methods.append(
            f"{ajax['method_name']}(): void {{\n  const config = this.{cfg_name}();\n"
            f"  if (!config.url) return;\n" + (f"  {set_loading('true')}\n" if ajax["loading"] else "")
            + f"  this.http.request<any>('{ajax['method']}', config.url{options})\n"
            "    .pipe(takeUntilDestroyed(this.destroyRef))\n    .subscribe({\n"
            "      next: (response) => {\n" + "".join(f"        {ln}\n" for ln in next_lines if ln) + "      },\n"
            "      error: (error) => {\n" + "".join(f"        {ln}\n" for ln in err_lines if ln) + "      },\n    });\n}")
        if ajax["auto"]:
            effects.append(f"effect(() => {{\n  this.{cfg_name}();\n  untracked(() => this.{ajax['method_name']}());\n}});")

    # ---- property declarations
    for name, kind in kinds.items():
        if kind == "computed":
            continue
        members.append(_prop_decl(name, props.get(name, {"name": name}), kind))
    for event, out_name in outputs.items():
        members.append(f"readonly {out_name} = output<any>();")
    ref_members = []
    for ref, kind in sorted(ctx.ref_kinds.items()):
        t = "ElementRef<any>" if kind == "native" else kind
        ref_members.append(f"private readonly {ref}Ref = viewChild<{t}>('{ref}');")
    field_members = []
    for f in element.get("fields", []):
        if f.get("name") in kinds:
            continue
        static = "static " if f.get("is_static") else ""
        value = f" = {sc.finalize(sc.rewrite(f['value']))}" if f.get("value") else ""
        field_members.append(f"{static}{f['name']}: any{value};")

    # ---- host
    host = dict(sc.host_listeners)
    for event, handler in (element.get("listeners") or {}).items():
        if "." in event:
            ctx.todo(f"Polymer listener '{event}' on a child node needs manual migration")
            continue
        host[f"({'click' if event == 'tap' else event})"] = f"{handler}({', '.join(ctx.handler_args(handler, ['$event']))})"
    for attr, value in (element.get("host_attributes") or {}).items():
        host[attr] = value
    for name, prop in props.items():
        if prop.get("reflect"):
            attr = re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()
            host[f"[attr.{attr}]"] = (f"{name}() ? '' : null" if prop.get("type") == "Boolean" else f"{name}()")

    # ---- injected services
    injected = []
    if sc.needs_host:
        injected.append("private readonly host = inject(ElementRef<HTMLElement>);")
    if ctx.ajax:
        injected.append("private readonly http = inject(HttpClient);")
        injected.append("private readonly destroyRef = inject(DestroyRef);")
    if sc.needs_injector:
        injected.append("private readonly injector = inject(Injector);")
    if sc.needs_debouncer:
        injected.append("private readonly debouncer = new Debouncer();")
        hooks.setdefault("ngOnDestroy", []).append("this.debouncer.cancelAll();")
        sc.uses.add("Debouncer")
    if ctx.needs_to_attr:
        injected.append("protected readonly toAttr = toAttribute;")
        sc.uses.add("toAttribute")
    if "bubbles" in "".join(m.get("body", "") for m in element.get("methods", [])):
        ctx.notes.append("Polymer events dispatched with bubbles/composed now use output(); ancestors that relied on "
                         "DOM bubbling must bind to the output on the direct child")

    # ---- undeclared members referenced via this.X
    declared = set(kinds) | {m["name"] for m in element.get("methods", [])} | set(outputs.values()) \
        | {f"{r}Ref" for r in ctx.ref_kinds} | {n for n, _ in ctx.extra_computed} \
        | {a["method_name"] for a in ctx.ajax} | {f.get("name") for f in element.get("fields", [])} \
        | {"host", "http", "injector", "debouncer", "router", "currentUrl", "toAttr", "destroyRef"} \
        | {re.search(r"readonly (\w+)", m).group(1) for m in ajax_members}
    code_so_far = "\n".join(methods_out + ctor + sum(hooks.values(), []) + effects + computed_decls + ajax_methods)
    undeclared = sorted(set(re.findall(r"this\.([A-Za-z_$][\w$]*)", code_so_far)) - declared)
    undeclared_members = [f"{n}: any; // TODO(migration): implicit Polymer instance property" for n in undeclared]
    if undeclared:
        ctx.todo("Implicit instance properties declared as 'any': " + ", ".join(undeclared))

    # ---- class body
    sections: list[list[str]] = [injected, route_members, members, computed_decls, ajax_members, ref_members,
                                 field_members, undeclared_members]
    body_parts = ["\n".join(indent(s, 2) for s in section) for section in sections if section]
    ctor_lines = effects + ctor
    if ctor_lines:
        body_parts.append(indent("constructor() {\n" + "\n".join(indent(c, 2) for c in ctor_lines) + "\n}", 2))
    for hook in ("ngOnInit", "ngAfterViewInit", "ngOnDestroy"):
        if hook in hooks:
            body_parts.append(indent(f"{hook}(): void {{\n" + "\n".join(indent(b, 2) for b in hooks[hook]) + "\n}", 2))
    for m in ajax_methods + methods_out:
        body_parts.append(indent(m, 2))
    class_body = "\n\n".join(body_parts)

    implements = [HOOK_INTERFACES[h] for h in ("ngOnInit", "ngAfterViewInit", "ngOnDestroy") if h in hooks]
    class_decl = f"export class {plan_entry['class_name']}" + (f" implements {', '.join(implements)}" if implements else "")

    # ---- decorator
    imports_list: list[str] = []
    import_lines: list[str] = []
    for tag in sorted(ctx.used_components):
        spec = registry[tag]
        if tag == element["tag"]:
            continue
        imports_list.append(spec["class_name"])
        import_lines.append(f"import {{ {spec['class_name']} }} from '{relative_import(plan_entry['ts_path'], spec['import_path'])}';")
    for symbol, module in sorted(ctx.material.items()):
        imports_list.append(symbol)
        import_lines.append(f"import {{ {symbol} }} from '{module}';")
    if "RouterLink" in ctx.angular_imports:
        imports_list.append("RouterLink")
    deco = [f"selector: '{element['tag']}'"]
    if imports_list:
        deco.append(f"imports: [{', '.join(imports_list)}]")
    deco.append(f"templateUrl: './{plan_entry['file_stem']}.html'")
    deco.append(f"styleUrl: './{plan_entry['file_stem']}.scss'")
    deco.append("changeDetection: ChangeDetectionStrategy.OnPush")
    if host:
        deco.append("host: {\n" + "".join(f"    '{k}': {_ts_str(v)},\n" for k, v in host.items()) + "  }")
    if ctx.needs_custom_schema:
        deco.append("schemas: [CUSTOM_ELEMENTS_SCHEMA]")
    decorator = "@Component({\n" + "".join(f"  {d},\n" for d in deco) + "})"

    full_body = class_body + "\n" + decorator + "\n" + class_decl
    core = ["ChangeDetectionStrategy", "Component"] + [s for s in CORE_SYMBOLS if re.search(rf"\b{s}\b", full_body)]
    header = [f"import {{ {', '.join(sorted(set(core), key=str.lower))} }} from '@angular/core';"]
    if ctx.ajax:
        header.append("import { HttpClient } from '@angular/common/http';")
    router_syms = [s for s in ("NavigationEnd", "Router", "RouterLink") if re.search(rf"\b{s}\b", full_body)]
    if router_syms:
        header.append(f"import {{ {', '.join(router_syms)} }} from '@angular/router';")
    if ctx.route_bindings:
        header.append("import { Location } from '@angular/common';")
    interop = [s for s in ("takeUntilDestroyed", "toSignal") if re.search(rf"\b{s}\b", full_body)]
    if interop:
        header.append(f"import {{ {', '.join(interop)} }} from '@angular/core/rxjs-interop';")
    if ctx.route_bindings:
        header.append("import { filter, map } from 'rxjs';")
    compat = [s for s in COMPAT_SYMBOLS if s in sc.uses]
    if compat:
        header.append(f"import {{ {', '.join(compat)} }} from '{relative_import(plan_entry['ts_path'], COMPAT_PATH)}';")
    header.extend(import_lines)

    doc = f"/**\n * Migrated from Polymer element <{element['tag']}> ({element.get('source_file', '')}).\n"
    if ctx.todos:
        doc += " *\n * Migration TODOs:\n" + "".join(f" *  - {t}\n" for t in ctx.todos)
    doc += " */"
    return "\n".join(header) + "\n\n" + doc + "\n" + decorator + "\n" + class_decl + " {\n" + class_body + "\n}\n"


def _ts_str(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _computed_decl(name: str, signature: str, ctx: ComponentContext, props: dict) -> str:
    fn, args = parse_call_signature(signature)
    conv = [convert_expr(a, ctx, {}, "ts") for a in args]
    guarded = []
    for raw, c in zip(args, conv):
        root = raw.split(".")[0].lstrip("!")
        if raw.endswith((".*", ".splices")) or LITERAL_RE.match(raw) or "(" in c and not c.endswith("()"):
            continue
        if root in props and props[root].get("default") is None and props[root].get("type") != "Boolean":
            guarded.append(c)
    arity = ctx.method_arity.get(fn, len(conv))
    if arity < len(conv):
        # Polymer passed dependencies the method ignores; still read them so the computed tracks them.
        tracked = "".join(f"  void ({d});\n" for d in conv[arity:])
        return (f"readonly {name} = computed(() => {{\n{tracked}"
                f"  return this.{fn}({', '.join(conv[:arity])});\n}});")
    call = f"this.{fn}({', '.join(conv)})"
    if not guarded:
        return f"readonly {name} = computed(() => {call});"
    cond = " || ".join(f"{g} === undefined" for g in guarded)
    return f"readonly {name} = computed(() => ({cond}) ? undefined : {call});"
