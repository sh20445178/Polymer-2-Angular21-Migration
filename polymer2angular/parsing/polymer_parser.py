"""Parser that turns Polymer source files into normalised element models (plain dicts)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..utils import html_dom as dom
from ..utils.js_scanner import (
    JsMember,
    dedent_block,
    extract_template_literal,
    find_expression_end,
    find_in_code,
    find_matching,
    object_inner,
    parse_class_members,
    parse_es_imports,
    parse_object_literal,
    skip_non_code,
    split_top_level,
    unquote,
)
from ..utils.naming import camel_to_kebab

SPECIAL_STATIC = {"is", "properties", "observers", "template", "importMeta", "importPath"}
LEGACY_SPECIAL_KEYS = {"is", "properties", "observers", "listeners", "behaviors", "hostAttributes", "_template",
                       "extends", "keyBindings", "importMeta"}


@dataclass
class PolymerProperty:
    name: str
    type: str | None = None
    default: str | None = None
    notify: bool = False
    observer: str | None = None
    computed: str | None = None
    reflect: bool = False
    read_only: bool = False


@dataclass
class PolymerModel:
    kind: str  # element | behavior | mixin | style-module
    tag: str | None = None
    name: str | None = None  # class / behavior / mixin identifier
    source_file: str = ""
    polymer_version: str = "2"
    template: str | None = None
    styles: list[str] = field(default_factory=list)
    style_includes: list[str] = field(default_factory=list)
    properties: list[dict] = field(default_factory=list)
    observers: list[str] = field(default_factory=list)
    listeners: dict[str, str] = field(default_factory=dict)
    host_attributes: dict[str, str] = field(default_factory=dict)
    behaviors: list[str] = field(default_factory=list)
    methods: list[dict] = field(default_factory=list)
    fields: list[dict] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    script: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- properties


def _default_from_function(member: JsMember) -> str:
    """Polymer `value: function() { return X; }` -> `X` (or an IIFE when not a simple return)."""
    body = member.body.strip()
    m = re.match(r"return\s+", body)
    if m:
        end = find_expression_end(body, m.end())
        if not body[end:].strip(" ;\n\t"):
            return body[m.end():end].strip()
    return f"(() => {{{member.body}}})()"


def _parse_properties(inner: str) -> list[dict]:
    props = []
    for entry in parse_object_literal(inner):
        prop = PolymerProperty(entry.key)
        value = entry.value.strip()
        if value.startswith("{"):
            for sub in parse_object_literal(object_inner(value, 0)):
                v = sub.value.strip()
                if sub.key == "type":
                    prop.type = v
                elif sub.key == "value":
                    prop.default = _default_from_function(sub.member) if sub.member else v
                elif sub.key == "notify":
                    prop.notify = v == "true"
                elif sub.key == "observer":
                    prop.observer = unquote(v)
                elif sub.key == "computed":
                    prop.computed = unquote(v)
                elif sub.key == "reflectToAttribute":
                    prop.reflect = v == "true"
                elif sub.key == "readOnly":
                    prop.read_only = v == "true"
        elif entry.member is None:
            prop.type = value
        props.append(asdict(prop))
    return props


def _parse_string_array(text: str) -> list[str]:
    text = text.strip()
    if not text.startswith("["):
        return []
    inner = text[1:find_matching(text, 0)]
    return [unquote(x) for x in split_top_level(inner)]


def _parse_string_map(text: str) -> dict[str, str]:
    text = text.strip()
    if not text.startswith("{"):
        return {}
    return {e.key: unquote(e.value) for e in parse_object_literal(object_inner(text, 0))}


def _returned_literal(body: str, opener: str) -> str | None:
    m = re.search(r"\breturn\s*" + re.escape(opener), body)
    if not m:
        return None
    start = m.end() - 1
    end = find_matching(body, start)
    return body[start:end + 1] if end != -1 else None


# --------------------------------------------------------------------------- templates & styles


def split_template_styles(template: str) -> tuple[str, list[str], list[str], list[str]]:
    """Remove <style> elements from a template. Returns (template, styles, includes, warnings)."""
    nodes = dom.parse_html(template)
    styles: list[str] = []
    includes: list[str] = []
    warnings: list[str] = []

    def walk(children: list) -> list:
        kept = []
        for node in children:
            if isinstance(node, dom.Element):
                tag = node.tag.lower()
                if tag == "style":
                    includes.extend((node.get("include") or "").split())
                    styles.append(dedent_block(node.text_content()))
                    continue
                if tag == "custom-style":
                    walk(node.children)
                    continue
                if tag == "link" and (node.get("rel") == "import" and node.get("type") == "css"):
                    warnings.append(f"External CSS import '{node.get('href')}' must be merged into component styles")
                    continue
                node.children = walk(node.children)
            kept.append(node)
        return kept

    nodes = walk(nodes)
    return dedent_block(dom.serialize(nodes)), [s for s in styles if s], includes, warnings


def _extract_js_template(body: str) -> tuple[str | None, list[str]]:
    m = re.search(r"\bhtml\s*`", body)
    if not m:
        return None, ["Template getter does not return an html`` literal; migrate manually"]
    content, _ = extract_template_literal(body, m.end() - 1)
    warnings = []
    if "${" in content:
        warnings.append("Template literal contains ${...} interpolations (e.g. shared template parts); review manually")
    return content, warnings


# --------------------------------------------------------------------------- JS definitions

_CLASS_RE = re.compile(r"\bclass\s+([\w$]+\s+)?extends\s+")
_MIXIN_CTX_RE = re.compile(
    r"([\w$.]+)\s*=\s*(?:[\w$.]*dedupingMixin\s*\(\s*)?(?:function\s*\(\s*([\w$]+)\s*\)\s*\{\s*return|\(?\s*([\w$]+)\s*\)?\s*=>\s*(?:\{\s*return)?)\s*$"
)


def _members_to_dicts(members: list[JsMember]) -> tuple[list[dict], list[dict]]:
    methods, fields = [], []
    for m in members:
        if m.is_static and m.name in SPECIAL_STATIC:
            continue
        (fields if m.kind == "field" else methods).append(m.to_dict())
    return methods, fields


def _extends_clause_end(src: str, start: int) -> int:
    depth = 0
    i = start
    while i < len(src):
        j = skip_non_code(src, i)
        if j != i:
            i = j
            continue
        c = src[i]
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "{" and depth == 0:
            return i
        i += 1
    return -1


def _behaviors_from_extends(clause: str) -> list[str]:
    out = []
    mb = re.search(r"mixinBehaviors\s*\(\s*\[", clause)
    if mb:
        arr_start = mb.end() - 1
        out.extend(x.strip() for x in split_top_level(clause[arr_start + 1:find_matching(clause, arr_start)]))
    for call in re.findall(r"([\w$.]+)\s*\(", clause):
        if not call.endswith("mixinBehaviors") and call not in out:
            out.append(call)
    return out


def _parse_classes(js: str, rel: str, version: str) -> list[PolymerModel]:
    models = []
    pos = 0
    while True:
        m = find_in_code(js, _CLASS_RE, pos)
        if not m:
            break
        clause_start = m.end()
        brace = _extends_clause_end(js, clause_start)
        if brace == -1:
            break
        end = find_matching(js, brace)
        if end == -1:
            break
        pos = end + 1
        extends = js[clause_start:brace].strip()
        body = js[brace + 1:end]
        members = parse_class_members(body)
        by_name = {mm.name: mm for mm in members if mm.is_static}
        class_name = (m.group(1) or "").strip() or None

        ctx = _MIXIN_CTX_RE.search(js[max(0, m.start() - 300):m.start()])
        is_mixin = bool(ctx) and (ctx.group(2) or ctx.group(3)) == extends
        is_polymer = is_mixin or bool(
            re.search(r"(Polymer\.Element|PolymerElement|mixinBehaviors|LegacyElementMixin|ElementMixin)", extends)
        ) or any(k in by_name for k in ("is", "template", "properties"))
        if not is_polymer:
            continue

        model = PolymerModel(kind="mixin" if is_mixin else "element", name=ctx.group(1) if is_mixin else class_name,
                             source_file=rel, polymer_version=version)
        if not is_mixin:
            model.behaviors = [b for b in _behaviors_from_extends(extends)
                               if b not in ("Polymer.Element", "PolymerElement")]
        else:
            model.behaviors = []
        if "is" in by_name:
            tm = re.search(r"return\s*['\"]([^'\"]+)['\"]", by_name["is"].body)
            model.tag = tm.group(1) if tm else None
        if "properties" in by_name:
            lit = _returned_literal(by_name["properties"].body, "{")
            if lit:
                model.properties = _parse_properties(lit[1:-1])
        for f in members:
            if f.is_static and f.kind == "field" and f.name == "properties" and f.value.startswith("{"):
                model.properties = _parse_properties(object_inner(f.value, 0))
        if "observers" in by_name:
            lit = _returned_literal(by_name["observers"].body, "[")
            model.observers = _parse_string_array(lit or "")
        if "template" in by_name:
            model.template, warns = _extract_js_template(by_name["template"].body)
            model.warnings.extend(warns)
        model.methods, model.fields = _members_to_dicts(members)
        if not model.tag and class_name and not is_mixin:
            dm = re.search(r"customElements\.define\(\s*['\"]([^'\"]+)['\"]\s*,\s*" + re.escape(class_name) + r"\b", js)
            model.tag = dm.group(1) if dm else camel_to_kebab(class_name)
        models.append(model)
    return models


def _parse_legacy_calls(js: str, rel: str) -> list[PolymerModel]:
    models = []
    pos = 0
    while True:
        m = find_in_code(js, re.compile(r"\bPolymer\s*\(\s*\{"), pos)
        if not m:
            break
        brace = m.end() - 1
        end = find_matching(js, brace)
        pos = end + 1 if end != -1 else len(js)
        model = _model_from_legacy_object(js[brace + 1:end], rel, "element")
        if model.tag:
            models.append(model)
    return models


def _model_from_legacy_object(inner: str, rel: str, kind: str, name: str | None = None) -> PolymerModel:
    model = PolymerModel(kind=kind, name=name, source_file=rel, polymer_version="1")
    for entry in parse_object_literal(inner):
        key, value = entry.key, entry.value
        if key == "is":
            model.tag = unquote(value)
        elif key == "properties":
            model.properties = _parse_properties(object_inner(value.strip(), 0)) if value.strip().startswith("{") else []
        elif key == "observers":
            model.observers = _parse_string_array(value)
        elif key == "listeners":
            model.listeners = _parse_string_map(value)
        elif key == "hostAttributes":
            model.host_attributes = _parse_string_map(value)
        elif key == "behaviors":
            v = value.strip()
            model.behaviors = [x.strip() for x in split_top_level(v[1:find_matching(v, 0)])] if v.startswith("[") else []
        elif key in LEGACY_SPECIAL_KEYS:
            continue
        elif entry.member:
            model.methods.append(entry.member.to_dict())
        else:
            model.fields.append({"name": key, "kind": "field", "value": value, "params": "", "body": "",
                                 "is_static": False, "is_async": False})
    return model


_BEHAVIOR_RE = re.compile(r"(?:^|[;\s])(?:(?:export\s+)?(?:const|let|var)\s+)?([\w$.]*Behaviou?r[\w$]*)\s*=\s*([\[{])", re.M)


def _parse_behaviors(js: str, rel: str) -> list[PolymerModel]:
    models = []
    for m in _BEHAVIOR_RE.finditer(js):
        name = m.group(1)
        start = m.end() - 1
        end = find_matching(js, start)
        if end == -1:
            continue
        if m.group(2) == "{":
            model = _model_from_legacy_object(js[start + 1:end], rel, "behavior", name)
        else:
            model = PolymerModel(kind="behavior", name=name, source_file=rel, polymer_version="1",
                                 behaviors=[x.strip() for x in split_top_level(js[start + 1:end])])
        model.tag = None
        models.append(model)
    return models


def parse_script(js: str, rel: str, version_hint: str = "2") -> list[PolymerModel]:
    is_module = bool(re.search(r"^\s*import\s.+from\s+['\"]", js, re.M)) or "@polymer/" in js
    version = "3" if is_module else version_hint
    models = _parse_classes(js, rel, version) + _parse_legacy_calls(js, rel) + _parse_behaviors(js, rel)
    imports = [i.source for i in parse_es_imports(js)]
    for model in models:
        model.imports = imports
        model.script = js
        if model.kind == "element" and model.polymer_version != "1":
            model.polymer_version = version
    return models


# --------------------------------------------------------------------------- files


def parse_html_file(text: str, rel: str) -> tuple[list[PolymerModel], list[str]]:
    """Parse an HTML-import style file. Returns (models, referenced_js_srcs)."""
    imports = re.findall(r"<link[^>]+rel=[\"']import[\"'][^>]*href=[\"']([^\"']+)[\"']", text)
    imports += re.findall(r"<link[^>]+href=[\"']([^\"']+)[\"'][^>]*rel=[\"']import[\"']", text)
    script_srcs = re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", text)
    inline_js = "\n".join(
        m.group(1) for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", text)
    )
    is_legacy = bool(re.search(r"\bPolymer\s*\(\s*\{", inline_js))
    parsed = parse_script(inline_js, rel, "1" if is_legacy else "2") if inline_js.strip() else []
    definitions = {m.tag: m for m in parsed if m.tag}
    others = [m for m in parsed if not m.tag]

    models: list[PolymerModel] = []
    for dm in re.finditer(r"<dom-module\s+([^>]*)>([\s\S]*?)</dom-module>", text):
        attrs = dict((a[0], a[1]) for a in dom.parse_attrs(dm.group(1)))
        tag = attrs.get("id")
        if not tag:
            continue
        inner_nodes = dom.parse_html(dm.group(2))
        tpl_el = next((n for n in inner_nodes if isinstance(n, dom.Element) and n.tag.lower() == "template"), None)
        template_src = dom.serialize(tpl_el.children) if tpl_el else ""
        for n in inner_nodes:
            if isinstance(n, dom.Element) and n.tag.lower() == "style":
                template_src = dom.serialize([n]) + template_src
        template, styles, includes, warns = split_template_styles(template_src)
        model = definitions.pop(tag, None) or PolymerModel(kind="element", tag=tag, source_file=rel)
        model.template = template
        model.styles, model.style_includes = styles, includes
        model.warnings.extend(warns)
        if model.name is None and not model.methods and not model.properties and not template.strip() and styles:
            model.kind = "style-module"
        models.append(model)
    models.extend(definitions.values())
    models.extend(others)
    for model in models:
        model.imports = imports + script_srcs + model.imports
    return models, script_srcs


def parse_js_file(text: str, rel: str) -> list[PolymerModel]:
    models = parse_script(text, rel)
    for model in models:
        if model.template is not None:
            tpl, styles, includes, warns = split_template_styles(model.template)
            model.template, model.styles, model.style_includes = tpl, styles, includes
            model.warnings.extend(warns)
    # Polymer 3 shared style modules: `$_documentContainer.innerHTML = `<dom-module id="x">...``
    for m in re.finditer(r"<dom-module\s+id=[\"']([^\"']+)[\"'][^>]*>([\s\S]*?)</dom-module>", text):
        _, styles, _, _ = split_template_styles(re.sub(r"</?template>", "", m.group(2)))
        if styles:
            models.append(PolymerModel(kind="style-module", tag=m.group(1), source_file=rel, polymer_version="3",
                                       styles=styles))
    return models


def merge_models(models: list[dict]) -> dict[str, dict]:
    """Merge partial models for the same tag (e.g. template in .html and class in .js)."""
    merged: dict[str, dict] = {}
    for model in models:
        key = model.get("tag") or model.get("name")
        if not key:
            continue
        if key not in merged:
            merged[key] = model
            continue
        base = merged[key]
        for k, v in model.items():
            if v and not base.get(k):
                base[k] = v
            elif isinstance(v, list) and isinstance(base.get(k), list) and k in ("warnings", "imports"):
                base[k] = list(dict.fromkeys(base[k] + v))
        if base.get("kind") == "style-module" and (model.get("methods") or model.get("properties")):
            base["kind"] = "element"
    return merged
