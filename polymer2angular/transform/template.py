"""Polymer template -> Angular 21 template conversion."""

from __future__ import annotations

import re

from ..utils import html_dom as dom
from ..utils.naming import kebab_to_camel, kebab_to_pascal
from .context import ComponentContext
from .expressions import binding_root, convert_expr, has_binding, interpolate_to_ts, parse_binding_value

ATTR_ONLY = re.compile(r"^(aria-.*|data-.*|role|for|colspan|rowspan)$")
REMOVED_ELEMENTS = {"app-location", "app-route", "iron-ajax", "iron-media-query", "iron-a11y-keys",
                    "iron-localstorage", "app-localstorage-document", "array-selector"}

MAT = {
    "button": ("MatButtonModule", "@angular/material/button"),
    "icon": ("MatIconModule", "@angular/material/icon"),
    "input": ("MatInputModule", "@angular/material/input"),
    "form": ("MatFormFieldModule", "@angular/material/form-field"),
    "checkbox": ("MatCheckboxModule", "@angular/material/checkbox"),
    "toggle": ("MatSlideToggleModule", "@angular/material/slide-toggle"),
    "card": ("MatCardModule", "@angular/material/card"),
    "spinner": ("MatProgressSpinnerModule", "@angular/material/progress-spinner"),
    "progress": ("MatProgressBarModule", "@angular/material/progress-bar"),
    "toolbar": ("MatToolbarModule", "@angular/material/toolbar"),
    "list": ("MatListModule", "@angular/material/list"),
    "tabs": ("MatTabsModule", "@angular/material/tabs"),
}


class TemplateConverter:
    def __init__(self, ctx: ComponentContext):
        self.ctx = ctx

    # ------------------------------------------------------------------ entry
    def convert(self, template: str) -> str:
        nodes = dom.parse_html(template or "")
        return dom.serialize(self.convert_nodes(nodes, {})).strip() + "\n"

    def convert_nodes(self, nodes: list, scope: dict) -> list:
        out = []
        for node in nodes:
            if isinstance(node, dom.Text):
                out.append(dom.Text(self.convert_text(node.text, scope)))
            elif isinstance(node, dom.Element):
                out.extend(self.convert_element(node, scope))
            else:
                out.append(node)
        return out

    # ------------------------------------------------------------------ text
    @staticmethod
    def escape_text(text: str) -> str:
        return text.replace("@", "&#64;").replace("{", "&#123;").replace("}", "&#125;")

    def convert_text(self, text: str, scope: dict) -> str:
        out = []
        for part in parse_binding_value(text):
            if part[0] == "text":
                out.append(self.escape_text(part[1]))
            else:
                out.append("{{ " + convert_expr(part[1], self.ctx, scope) + " }}")
        return "".join(out)

    # ------------------------------------------------------------------ elements
    def _template_children(self, el: dom.Element) -> list:
        if el.tag.lower() == "template":
            return el.children
        inner = next((c for c in el.children if isinstance(c, dom.Element) and c.tag.lower() == "template"), None)
        return inner.children if inner else el.children

    def _single_binding(self, el: dom.Element, name: str, scope: dict, default: str | None = None) -> str | None:
        value = el.get(name)
        if value is None:
            return default
        parts = parse_binding_value(value)
        if len(parts) == 1 and parts[0][0] == "bind":
            return convert_expr(parts[0][1], self.ctx, scope)
        return repr(value) if not has_binding(value) else convert_expr(value, self.ctx, scope)

    def convert_element(self, el: dom.Element, scope: dict) -> list:
        tag = el.tag.lower()
        is_attr = el.get("is")
        if (tag == "template" and is_attr == "dom-if") or tag == "dom-if":
            cond = self._single_binding(el, "if", scope, "false")
            return [dom.Block(f"@if ({cond})", self.convert_nodes(self._template_children(el), scope))]
        if (tag == "template" and is_attr == "dom-repeat") or tag in ("dom-repeat", "iron-list"):
            block = self.convert_repeat(el, scope)
            if tag == "iron-list":
                self.ctx.todo("<iron-list> converted to a plain @for loop; consider @angular/cdk/scrolling for virtualisation")
                return [dom.Element("div", [["class", "iron-list"]], [block])]
            return [block]
        if (tag == "template" and is_attr == "dom-bind") or tag == "dom-bind":
            return self.convert_nodes(self._template_children(el), scope)
        if tag == "template":
            el.tag = "ng-template"
            el.children = self.convert_nodes(el.children, scope)
            return [el]
        if tag in ("slot", "content"):
            return [self.convert_slot(el, scope)]
        if tag in ("app-location", "app-route"):
            self.record_route(el)
            return [dom.Comment(f"<!-- <{tag}> replaced by Angular Router integration in the component class -->")]
        if tag == "iron-ajax":
            self.record_ajax(el)
            return [dom.Comment(f"<!-- <iron-ajax{' #' + el.get('id') if el.get('id') else ''}> migrated to HttpClient in the component class -->")]
        if tag in REMOVED_ELEMENTS:
            self.ctx.todo(f"<{tag}> has no direct Angular equivalent and was removed; re-implement its behaviour")
            return [dom.Comment(f"<!-- TODO(migration): <{tag}> removed -->")]
        if tag == "iron-pages":
            return [self.convert_iron_pages(el, scope)]
        mapper = getattr(self, "map_" + tag.replace("-", "_"), None)
        if mapper:
            return mapper(el, scope)
        return [self.convert_generic(el, scope)]

    def element_kind(self, tag: str) -> str:
        if tag in self.ctx.registry:
            return "component"
        if "-" in tag and not tag.startswith(("mat-", "ng-")):
            return "external"
        return "native"

    def convert_generic(self, el: dom.Element, scope: dict) -> dom.Element:
        tag = el.tag.lower()
        kind = self.element_kind(tag)
        if kind == "component":
            self.ctx.used_components.add(tag)
        elif kind == "external":
            self.ctx.needs_custom_schema = True
            self.ctx.external_elements.add(tag)
        el.attrs = self.convert_attrs(el, scope, kind)
        el.children = self.convert_nodes(el.children, scope)
        return el

    # ------------------------------------------------------------------ attributes
    def convert_attrs(self, el: dom.Element, scope: dict, kind: str) -> list[list]:
        tag = el.tag.lower()
        spec = self.ctx.registry.get(tag, {})
        out: list[list] = []
        for name, value in el.attrs:
            if name == "id" and value:
                out.append([name, value])
                ref = kebab_to_camel(value)
                if value in self.ctx.dollar_ids:
                    out.append(["#" + ref, None])
                    self.ctx.ref_kinds[ref] = spec.get("class_name", "native") if kind == "component" else "native"
                continue
            if name.startswith("on-"):
                out.append(self.convert_event(name[3:], value or "", scope, kind, spec))
                continue
            if value is None or not has_binding(value):
                if kind == "component" and "-" in name and (
                        kebab_to_camel(name) in spec.get("inputs", ()) or kebab_to_camel(name) in spec.get("models", ())):
                    name = kebab_to_camel(name)
                out.append([name, value])
                continue
            out.extend(self.convert_bound_attr(name, value, scope, kind, spec))
        return out

    def convert_event(self, event: str, handler: str, scope: dict, kind: str, spec: dict) -> list:
        handler = handler.strip()
        arg = "$event"
        dom_event = "click" if event == "tap" else event
        if kind == "component":
            prop = kebab_to_camel(event[: -len("-changed")]) if event.endswith("-changed") else None
            out_name = kebab_to_camel(event)
            if prop and prop in spec.get("models", ()):
                dom_event, arg = prop + "Change", "{ detail: { value: $event } }"
            elif out_name in spec.get("outputs", {}).values():
                dom_event, arg = out_name, "{ detail: $event }"
            elif event in spec.get("outputs", {}):
                dom_event, arg = spec["outputs"][event], "{ detail: $event }"
        args = [arg]
        if handler in self.ctx.model_handlers and scope:
            args.append("{ " + ", ".join(f"{k}: {k}" for k in scope) + " }")
        if handler not in self.ctx.methods:
            self.ctx.todo(f"Event handler '{handler}' for '{event}' is not a method of {self.ctx.class_name}")
        return [f"({dom_event})", f"{handler}({', '.join(self.ctx.handler_args(handler, args))})"]

    def convert_bound_attr(self, name: str, value: str, scope: dict, kind: str, spec: dict) -> list[list]:
        ctx = self.ctx
        parts = parse_binding_value(value)
        single = len(parts) == 1 and parts[0][0] == "bind"
        is_attr_binding = name.endswith("$")
        base = name[:-1] if is_attr_binding else name

        if base == "href" and value.startswith("[[rootPath]]") and kind == "native":
            rest = value[len("[[rootPath]]"):]
            ctx.angular_imports["RouterLink"] = "@angular/router"
            if not has_binding(rest):
                return [["routerLink", "/" + rest]]
            return [["[routerLink]", "'/' + " + interpolate_to_template(rest, ctx, scope)]]

        if not single:
            text = "".join(p[1] if p[0] == "text" else "{{ " + convert_expr(p[1], ctx, scope) + " }}" for p in parts)
            target = f"attr.{base}" if (is_attr_binding and base not in ("class", "style")) else base
            return [[target, text]]

        _, expr, two_way, event = parts[0]
        conv = convert_expr(expr, ctx, scope)
        root = binding_root(expr)
        simple_signal = root is not None and root == expr.strip() and root not in scope

        if is_attr_binding:
            if base in ("class", "style"):
                return [[f"[{base}]", conv]]
            if base == "hidden":
                return [["[hidden]", conv]]
            ctx.needs_to_attr = True
            return [[f"[attr.{base}]", f"toAttr({conv})"]]

        prop = kebab_to_camel(base)
        if kind == "native":
            target = f"[attr.{base}]" if ATTR_ONLY.match(base) else f"[{'class' if base == 'class' else prop}]"
            # Polymer never pushes `undefined` into native value properties (it would render "undefined").
            result = [[target, f"{conv} ?? ''" if prop == "value" else conv]]
            if two_way and event and simple_signal:
                ctx.two_way_targets.add(root)
                result.append([f"({event})", f"{root}.set($any($event.target).{prop})"])
            return result

        if kind == "component":
            if two_way and prop in spec.get("models", ()):
                if simple_signal:
                    ctx.two_way_targets.add(root)
                    return [[f"[({prop})]", root]]
                if root in scope:
                    return [[f"[{prop}]", conv], [f"({prop}Change)", f"{expr.strip()} = $event"]]
                ctx.todo(f"Two-way binding of nested path '{expr}' to <{spec.get('tag')}> {prop} is one-way now")
            if prop not in spec.get("inputs", ()) and prop not in spec.get("models", ()):
                ctx.todo(f"<{spec.get('tag')}> has no declared property '{prop}' (bound in template)")
            return [[f"[{prop}]", conv]]

        result = [[f"[{prop}]", conv]]
        if two_way and simple_signal:
            ctx.two_way_targets.add(root)
            result.append([f"({base}-changed)", f"{root}.set($event.detail.value)"])
        return result

    # ------------------------------------------------------------------ structural
    def convert_repeat(self, el: dom.Element, scope: dict) -> dom.Block:
        ctx = self.ctx
        items_raw = el.get("items") or "[]"
        as_name = el.get("as") or "item"
        idx_name = el.get("index-as") or "index"
        parts = parse_binding_value(items_raw)
        items_expr = parts[0][1] if parts and parts[0][0] == "bind" else items_raw
        root = binding_root(items_expr)
        conv = convert_expr(items_expr, ctx, scope)
        filt, sort = el.get("filter"), el.get("sort")
        if (filt or sort) and root and root == items_expr.strip() and root not in scope:
            view_name = f"{root}View"
            source = convert_expr(items_expr, ctx, {}, "ts")
            body = f"let list = [...({source} ?? [])];"
            if filt:
                body += f"\nlist = list.filter((item) => this.{filt}(item));"
            if sort:
                body += f"\nlist.sort((a, b) => this.{sort}(a, b));"
            body += "\nreturn list;"
            ctx.extra_computed.append((view_name, body))
            conv = f"{view_name}()"
        elif filt or sort:
            ctx.todo(f"dom-repeat filter/sort on '{items_expr}' needs manual migration")
        elif root and root not in scope and not (ctx.properties.get(root, {}).get("default")):
            conv = f"({conv} ?? [])"
        inner_scope = dict(scope)
        inner_scope[as_name] = "item"
        inner_scope[idx_name] = "index"
        if el.get("observe"):
            ctx.notes.append(f"dom-repeat 'observe' ({el.get('observe')}) is unnecessary with signals")
        header = f"@for ({as_name} of {conv}; track $index; let {idx_name} = $index)"
        return dom.Block(header, self.convert_nodes(self._template_children(el), inner_scope))

    def convert_slot(self, el: dom.Element, scope: dict) -> dom.Element:
        attrs = []
        if el.tag.lower() == "slot" and el.get("name"):
            attrs.append(["select", f"[slot={el.get('name')}]"])
        elif el.get("select"):
            attrs.append(["select", el.get("select")])
        children = self.convert_nodes(el.children, scope)
        return dom.Element("ng-content", attrs, children, self_closing=not any(
            not isinstance(c, dom.Text) or c.text.strip() for c in children))

    def convert_iron_pages(self, el: dom.Element, scope: dict) -> dom.Element:
        ctx = self.ctx
        selected = self._single_binding(el, "selected", scope, "0")
        attr = el.get("attr-for-selected")
        fallback = el.get("fallback-selection")
        cases: list = [dom.Text("\n")]
        default_child = None
        index = 0
        for child in el.children:
            if not isinstance(child, dom.Element):
                continue
            key = child.get(attr) if attr else str(index)
            index += 1
            if attr:
                child.remove(attr)
            converted = self.convert_element(child, scope)
            if fallback and key == fallback:
                default_child = converted
            cases.append(dom.Block(f"  @case ({key!r})", [dom.Text("\n    ")] + converted + [dom.Text("\n  ")]))
            cases.append(dom.Text("\n"))
        if default_child:
            cases.append(dom.Block("  @default", [dom.Text("\n    ")] + default_child + [dom.Text("\n  ")]))
            cases.append(dom.Text("\n"))
        ctx.notes.append("<iron-pages> converted to @switch; inactive pages are destroyed instead of hidden")
        attrs = [a for a in el.attrs if a[0] in ("role", "class", "id")]
        return dom.Element("div", attrs + [["class", "iron-pages"]] if not el.get("class") else attrs,
                           [dom.Block(f"@switch ({selected})", cases)])

    # ------------------------------------------------------------------ app-route / iron-ajax
    def record_route(self, el: dom.Element) -> None:
        def bound(name):
            v = el.get(name)
            return binding_root(parse_binding_value(v)[0][1]) if v and has_binding(v) else None

        spec = {"tag": el.tag.lower(), "route": bound("route"), "pattern": el.get("pattern") or "",
                "data": bound("data"), "tail": bound("tail"), "active": bound("active")}
        for key in ("route", "data", "tail", "active"):
            if spec[key]:
                self.ctx.two_way_targets.add(spec[key])
                self.ctx.template_roots.add(spec[key])
        self.ctx.route_bindings.append(spec)
        self.ctx.angular_imports.setdefault("__router__", "")

    def record_ajax(self, el: dom.Element) -> None:
        ctx = self.ctx
        ajax_id = el.get("id") or f"ajax{len(ctx.ajax) + 1}"

        def bound_root(name):
            v = el.get(name)
            if v and has_binding(v):
                root = binding_root(parse_binding_value(v)[0][1])
                if root:
                    ctx.two_way_targets.add(root)
                    ctx.template_roots.add(root)
                return root
            return None

        def value_ts(name, default=None):
            v = el.get(name)
            if v is None:
                return default
            if has_binding(v):
                for part in parse_binding_value(v):
                    if part[0] == "bind" and binding_root(part[1]):
                        ctx.template_roots.add(binding_root(part[1]))
                return interpolate_to_ts(v, ctx)
            return repr(v)

        handler = lambda n: (el.get(n) or "").strip() or None  # noqa: E731
        ctx.ajax.append({
            "id": ajax_id,
            "method_name": f"generate{kebab_to_pascal(kebab_to_camel(ajax_id))}Request",
            "url": value_ts("url", "''"),
            "method": (el.get("method") or "GET").upper(),
            "params": value_ts("params"),
            "body": value_ts("body"),
            "headers": value_ts("headers"),
            "handle_as": el.get("handle-as") or "json",
            "auto": el.has("auto"),
            "last_response": bound_root("last-response"),
            "loading": bound_root("loading"),
            "last_error": bound_root("last-error"),
            "on_response": handler("on-response"),
            "on_error": handler("on-error"),
        })
        ctx.angular_imports.setdefault("__http__", "")

    # ------------------------------------------------------------------ Polymer element catalog -> Material/native
    def _mat(self, key: str) -> None:
        symbol, module = MAT[key]
        self.ctx.material[symbol] = module

    def _native(self, el: dom.Element, tag: str, scope: dict, extra: list | None = None, drop=()) -> dom.Element:
        el.attrs = [a for a in el.attrs if a[0] not in drop]
        el.attrs = self.convert_attrs(el, scope, "native") + (extra or [])
        el.tag = tag
        el.children = self.convert_nodes(el.children, scope)
        return el

    def map_paper_button(self, el, scope):
        self._mat("button")
        variant = "mat-raised-button" if el.has("raised") else "mat-button"
        el.attrs = [a for a in el.attrs if a[0] != "raised"]
        return [self._native(el, "button", scope, [["type", "button"], [variant, None]])]

    def map_paper_icon_button(self, el, scope):
        self._mat("button")
        self._mat("icon")
        icon = (el.get("icon") or "").split(":")[-1]
        el.remove("icon")
        btn = self._native(el, "button", scope, [["type", "button"], ["mat-icon-button", None]])
        btn.children = [dom.Element("mat-icon", [], [dom.Text(icon)])] + btn.children
        return [btn]

    def map_iron_icon(self, el, scope):
        self._mat("icon")
        icon = el.get("icon") or ""
        el.remove("icon")
        node = self._native(el, "mat-icon", scope)
        if has_binding(icon):
            node.children = [dom.Text(self.convert_text(icon, scope))]
        else:
            node.children = [dom.Text(icon.split(":")[-1])]
        return [node]

    def map_paper_input(self, el, scope):
        self._mat("form")
        self._mat("input")
        label = el.get("label") or ""
        el.remove("label")
        for drop in ("always-float-label", "no-label-float", "auto-validate", "char-counter"):
            el.remove(drop)
        value = el.get("value")
        el.remove("value")
        node = self._native(el, "input", scope, [["matInput", None]])
        if value is not None:
            parts = parse_binding_value(value)
            if len(parts) == 1 and parts[0][0] == "bind":
                _, expr, two_way, _event = parts[0]
                node.attrs.append(["[value]", convert_expr(expr, self.ctx, scope) + " ?? ''"])
                root = binding_root(expr)
                if two_way and root and root == expr.strip() and root not in scope:
                    self.ctx.two_way_targets.add(root)
                    node.attrs.append(["(input)", f"{root}.set($any($event.target).value)"])
            else:
                node.attrs.append(["value", value])
        children = [dom.Text("\n  ")]
        if label:
            children += [dom.Element("mat-label", [], [dom.Text(self.convert_text(label, scope))]), dom.Text("\n  ")]
        children += [node, dom.Text("\n")]
        return [dom.Element("mat-form-field", [], children)]

    def _toggle_like(self, el, scope, tag, key):
        self._mat(key)
        checked = el.get("checked")
        el.remove("checked")
        node = self._native(el, tag, scope)
        node.attrs = [a for a in node.attrs if a[0] not in ("[value]",)]
        if checked is not None:
            parts = parse_binding_value(checked)
            if len(parts) == 1 and parts[0][0] == "bind":
                _, expr, two_way, _ = parts[0]
                node.attrs.append(["[checked]", convert_expr(expr, self.ctx, scope)])
                root = binding_root(expr)
                if two_way and root and root == expr.strip() and root not in scope:
                    self.ctx.two_way_targets.add(root)
                    node.attrs.append(["(change)", f"{root}.set($event.checked)"])
            else:
                node.attrs.append(["checked", checked])
        return [node]

    def map_paper_checkbox(self, el, scope):
        return self._toggle_like(el, scope, "mat-checkbox", "checkbox")

    def map_paper_toggle_button(self, el, scope):
        return self._toggle_like(el, scope, "mat-slide-toggle", "toggle")

    def map_paper_card(self, el, scope):
        self._mat("card")
        heading = el.get("heading")
        el.remove("heading")
        image = el.get("image")
        el.remove("image")
        card = self._native(el, "mat-card", scope)
        body = card.children
        content_nodes = []
        if image:
            content_nodes.append(dom.Element("img", [["mat-card-image", None], ["src", image], ["alt", ""]]))
        if heading:
            title = dom.Element("mat-card-title", [], [dom.Text(self.convert_text(heading, scope))])
            content_nodes.append(dom.Element("mat-card-header", [], [title]))
        content_nodes.append(dom.Element("mat-card-content", [], body))
        card.children = content_nodes
        return [card]

    def map_paper_spinner(self, el, scope):
        self._mat("spinner")
        active = self._single_binding(el, "active", scope)
        el.remove("active")
        spinner = self._native(el, "mat-spinner", scope, [["diameter", "28"]])
        if active is None:
            return [spinner]
        if active.startswith("'"):
            return [spinner]
        return [dom.Block(f"@if ({active})", [spinner])]

    map_paper_spinner_lite = map_paper_spinner

    def map_paper_progress(self, el, scope):
        self._mat("progress")
        mode = [["mode", "indeterminate"]] if el.has("indeterminate") else [["mode", "determinate"]]
        el.remove("indeterminate")
        return [self._native(el, "mat-progress-bar", scope, mode)]

    def map_app_toolbar(self, el, scope):
        self._mat("toolbar")
        return [self._native(el, "mat-toolbar", scope)]

    def map_app_header(self, el, scope):
        for drop in ("condenses", "reveals", "effects", "fixed", "shadow", "slot"):
            el.remove(drop)
        return [self._native(el, "header", scope)]

    def map_app_header_layout(self, el, scope):
        return [self._native(el, "div", scope, [["class", "app-header-layout"]])]

    def map_app_drawer_layout(self, el, scope):
        self.ctx.todo("<app-drawer-layout>/<app-drawer> converted to plain containers; consider MatSidenav")
        return [self._native(el, "div", scope, [["class", "app-drawer-layout"]])]

    def map_app_drawer(self, el, scope):
        el.remove("slot")
        return [self._native(el, "aside", scope, [["class", "app-drawer"]])]

    def map_paper_item(self, el, scope):
        return [self._native(el, "div", scope, [["role", "option"], ["class", "paper-item"]])]

    map_paper_icon_item = map_paper_item

    def map_paper_listbox(self, el, scope):
        self.ctx.todo("<paper-listbox> selection semantics (selected/attr-for-selected) need manual migration")
        return [self._native(el, "div", scope, [["role", "listbox"]], drop=("selected", "attr-for-selected"))]

    def map_iron_selector(self, el, scope):
        self.ctx.todo("<iron-selector> selection semantics need manual migration")
        return [self._native(el, "div", scope, [["class", "iron-selector"]],
                             drop=("selected", "attr-for-selected", "selected-attribute"))]

    def map_iron_image(self, el, scope):
        for drop in ("sizing", "preload", "fade", "placeholder"):
            el.remove(drop)
        return [self._native(el, "img", scope)]

    def map_iron_collapse(self, el, scope):
        opened = self._single_binding(el, "opened", scope, "false")
        el.remove("opened")
        node = self._native(el, "div", scope, [["[hidden]", f"!({opened})"]])
        return [node]

    def map_paper_dialog(self, el, scope):
        self.ctx.todo("<paper-dialog> converted to native <dialog>; consider MatDialog for full parity")
        opened = el.get("opened")
        el.remove("opened")
        for drop in ("modal", "with-backdrop", "entry-animation", "exit-animation"):
            el.remove(drop)
        extra = [["[open]", self._single_binding(dom.Element("x", [["v", opened]]), "v", scope)]] if opened else []
        return [self._native(el, "dialog", scope, extra)]


def interpolate_to_template(value: str, ctx: ComponentContext, scope: dict) -> str:
    out = []
    for part in parse_binding_value(value):
        out.append(repr(part[1]) if part[0] == "text" else f"({convert_expr(part[1], ctx, scope)})")
    return " + ".join(out) or "''"
