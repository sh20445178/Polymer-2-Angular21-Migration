from polymer2angular.utils.js_scanner import (
    find_matching,
    function_return_expression,
    parse_class_members,
    parse_object_literal,
    split_top_level,
)


def test_find_matching_skips_strings_regex_and_templates():
    src = "{ a: '}', b: /}/g, c: `x ${ {y: 1} } }`, d: { e: 1 } }"
    assert find_matching(src, 0) == len(src) - 1


def test_split_top_level_respects_nesting():
    assert split_top_level("a, fn(b, c), [d, e], {f: 1, g: 2}") == ["a", "fn(b, c)", "[d, e]", "{f: 1, g: 2}"]


def test_parse_class_members_handles_static_getters_and_methods():
    body = """
      static get is() { return 'x-el'; }
      static get properties() { return { a: String }; }
      async load(url) { return fetch(url); }
      get total() { return 1; }
      counter = 0;
    """
    members = {m.name: m for m in parse_class_members(body)}
    assert members["is"].is_static and members["properties"].is_static
    assert members["load"].is_async and members["load"].params == "url"
    assert members["total"].kind == "get"
    assert members["counter"].kind == "field" and members["counter"].value == "0"


def test_parse_object_literal_method_forms():
    entries = {e.key: e for e in parse_object_literal(
        "is: 'x', ready() { this.a = 1; }, tap: function(e) { go(e); }, arrow: (x) => x * 2, n: 3")}
    assert entries["is"].value == "'x'"
    assert entries["ready"].member.body.strip() == "this.a = 1;"
    assert entries["tap"].member.params == "e"
    assert entries["arrow"].member.body == "return x * 2;"
    assert entries["n"].member is None


def test_function_return_expression():
    assert function_return_expression("function() { return []; }") == "[]"
    assert function_return_expression("() => ({a: 1})") == "({a: 1})"
