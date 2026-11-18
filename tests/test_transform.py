from polymer2angular.transform.component import migrate_component
from polymer2angular.transform.styles import convert_css

REGISTRY = {
    "child-el": {"tag": "child-el", "class_name": "ChildEl", "import_path": "src/app/components/child-el/child-el",
                 "inputs": ["label"], "models": ["value"], "outputs": {"item-picked": "itemPicked"}},
}


def _migrate(template: str, properties=None, methods=None, observers=None, kinds=None, outputs=None):
    element = {
        "tag": "x-el", "template": template, "properties": properties or [], "methods": methods or [],
        "observers": observers or [], "listeners": {}, "host_attributes": {}, "fields": [], "styles": [],
        "style_includes": [], "warnings": [], "source_file": "x-el.html",
    }
    plan_entry = {"class_name": "XEl", "ts_path": "src/app/components/x-el/x-el.ts", "file_stem": "x-el",
                  "kinds": kinds or {p["name"]: "input" for p in properties or []}, "outputs": outputs or {}}
    return migrate_component(element, plan_entry, REGISTRY, set())


def _method(name, params="", body=""):
    return {"name": name, "kind": "method", "params": params, "body": body, "is_static": False, "is_async": False}


def test_template_control_flow_bindings_and_events():
    result = _migrate(
        """<template is="dom-repeat" items="[[items]]" as="row" index-as="i">
             <span class$="[[cls]]">[[i]]: [[row.name]] @ {x}</span>
             <button on-tap="_pick">Pick</button>
           </template>
           <template is="dom-if" if="[[!hidden]]"><slot name="footer"></slot></template>
           <input value="{{query::input}}">""",
        properties=[{"name": "items", "type": "Array", "default": "[]"}, {"name": "cls", "type": "String"},
                    {"name": "hidden", "type": "Boolean"}, {"name": "query", "type": "String"}],
        methods=[_method("_pick", "e", "console.log(e.model.row);")],
    )
    html = result["html"]
    assert "@for (row of items(); track $index; let i = $index) {" in html
    assert "{{ i }}: {{ row?.name }} &#64; &#123;x&#125;" in html
    assert '[class]="cls()"' in html
    assert '(click)="_pick($event, { row: row, i: i })"' in html
    assert "@if (!hidden()) {" in html and '<ng-content select="[slot=footer]" />' in html
    assert '[value]="query() ?? \'\'" (input)="query.set($any($event.target).value)"' in html
    assert result["kinds"]["query"] == "model"
    assert "_pick(e, model?: any)" in result["ts"] and "console.log(model.row)" in result["ts"]


def test_child_component_api_mapping_and_handler_arity():
    result = _migrate(
        '<child-el label="[[title]]" value="{{val}}" on-item-picked="_onPick"></child-el>'
        '<button on-click="reset">x</button>',
        properties=[{"name": "title", "type": "String"}, {"name": "val", "type": "String"}],
        methods=[_method("_onPick", "e", "this.title = e.detail;"), _method("reset")],
    )
    assert '[label]="title()"' in result["html"]
    assert '[(value)]="val"' in result["html"]
    assert '(itemPicked)="_onPick({ detail: $event })"' in result["html"]
    assert '(click)="reset()"' in result["html"]
    assert "import { ChildEl } from '../child-el/child-el';" in result["ts"]
    assert "this.title.set(e.detail);" in result["ts"]


def test_script_rewrites_polymer_apis():
    result = _migrate(
        '<div id="box"></div>',
        properties=[{"name": "count", "type": "Number", "default": "0"},
                    {"name": "list", "type": "Array", "default": "[]", "notify": True},
                    {"name": "page", "type": "String", "observer": "_pageChanged"},
                    {"name": "label", "type": "String", "computed": "_label(count)"}],
        kinds={"count": "model", "list": "model", "page": "input", "label": "computed"},
        outputs={"count-reset": "countReset"},
        methods=[
            _method("ready", "", "super.ready(); this.$.box.focus();"),
            _method("inc", "", "this.count++; this.count += 2; this.push('list', this.count);"),
            _method("reset", "", "this.set('list.0.name', 'x'); this.fire('count-reset', {n: this.count});"),
            _method("_pageChanged", "page"),
            _method("_label", "c", "return 'n=' + c;"),
        ],
    )
    ts = result["ts"]
    assert "readonly count = model<number>(0);" in ts and "readonly list = model<any[]>([]);" in ts
    assert "readonly label = computed(() => this._label(this.count()));" in ts
    assert "readonly countReset = output<any>();" in ts
    assert "private readonly boxRef = viewChild<ElementRef<any>>('box');" in ts and "#box" in result["html"]
    assert "ngAfterViewInit(): void {\n    this.boxRef()!.nativeElement.focus();" in ts
    assert "this.count.update((v) => v + 1);" in ts and "this.count.update((v) => v + (2));" in ts
    assert "arrayPush(this.list, '', this.count());" in ts
    assert "setPath(this.list, '0.name', 'x');" in ts
    assert "this.countReset.emit({n: this.count()});" in ts
    assert "untracked(() => this._pageChanged(page));" in ts


def test_css_conversion():
    css = convert_css(":host { --m: { color: red; }; } .a { @apply --m; @apply --layout-horizontal; } ::slotted(p) { x: 1 }")
    assert "color: red;" in css and "display: flex; flex-direction: row;" in css
    assert ":host ::ng-deep p" in css and "@apply" not in css
