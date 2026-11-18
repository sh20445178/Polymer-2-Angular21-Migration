from pathlib import Path

from polymer2angular.agents.analyzer import analyze
from polymer2angular.agents.scanner import scan_repository
from polymer2angular.parsing.polymer_parser import parse_html_file, parse_js_file

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_polymer1_legacy_element():
    models, _ = parse_html_file((EXAMPLES / "polymer2-shop/src/my-cart.html").read_text(), "my-cart.html")
    cart = models[0]
    assert cart.tag == "my-cart" and cart.polymer_version == "1"
    props = {p["name"]: p for p in cart.properties}
    assert props["items"]["notify"] and props["items"]["default"] == "[]"
    assert props["total"]["computed"] == "_total(items.*)"
    assert cart.listeners == {"keydown": "_onKeyDown"}
    assert cart.behaviors == ["MyApp.FormatBehavior"]
    assert cart.style_includes == ["shared-styles"]


def test_polymer2_class_with_mixin_behaviors():
    models, _ = parse_html_file((EXAMPLES / "polymer2-shop/src/my-products.html").read_text(), "p.html")
    el = models[0]
    assert el.name == "MyProducts" and el.behaviors == ["MyApp.FormatBehavior"]
    assert {p["name"] for p in el.properties} >= {"category", "products", "count"}
    assert "<style" not in el.template and "iron-ajax" in el.template


def test_polymer3_module_and_mixin():
    app = parse_js_file((EXAMPLES / "polymer3-todo/src/todo-app.js").read_text(), "todo-app.js")[0]
    assert app.tag == "todo-app" and app.polymer_version == "3"
    todos = next(p for p in app.properties if p["name"] == "todos")
    assert todos["default"].startswith("[{title")
    assert app.observers == ["_todosChanged(todos.splices)"]
    mixin = parse_js_file((EXAMPLES / "polymer3-todo/src/mixins/logging-mixin.js").read_text(), "m.js")[0]
    assert mixin.kind == "mixin" and mixin.name == "LoggingMixin"


def test_analyzer_inlines_behaviors_and_finds_roots():
    scan = scan_repository(str(EXAMPLES / "polymer2-shop"), ["node_modules"])
    result = analyze(scan)
    products = result["elements"]["my-products"]
    assert "formatPrice" in {m["name"] for m in products["methods"]}
    assert "currency" in {p["name"] for p in products["properties"]}
    assert result["analysis"]["root_elements"] == ["my-app"]
    assert "shared-styles" in result["style_modules"]
    assert "api/products.json" in scan["files"]["assets"]
