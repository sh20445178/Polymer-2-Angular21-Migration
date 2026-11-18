"""Minimal, formatting-preserving HTML tree used for template transformation.

Python's html.parser lowercases names and normalises markup; templates need to
round-trip with their original whitespace and attribute names, so this small
tokenizer builds a simple tree instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}
RAW_TEXT_TAGS = {"style", "script"}

_TAG_RE = re.compile(
    r"<(/?)([A-Za-z][\w:.-]*)((?:\s+[^\s=>/\"']+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>\"']+))?|\s*/(?!>))*)\s*(/?)>",
    re.S,
)
_ATTR_RE = re.compile(r"([^\s=>/\"']+)(?:\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>\"']+))?", re.S)


@dataclass
class Node:
    pass


@dataclass
class Text(Node):
    text: str


@dataclass
class Comment(Node):
    text: str


@dataclass
class Raw(Node):
    text: str


@dataclass
class Element(Node):
    tag: str
    attrs: list[list] = field(default_factory=list)  # [[name, value|None], ...]
    children: list[Node] = field(default_factory=list)
    self_closing: bool = False

    def get(self, name: str, default=None):
        for n, v in self.attrs:
            if n == name:
                return v if v is not None else ""
        return default

    def has(self, name: str) -> bool:
        return any(n == name for n, _ in self.attrs)

    def remove(self, name: str):
        self.attrs = [a for a in self.attrs if a[0] != name]

    def set(self, name: str, value):
        for a in self.attrs:
            if a[0] == name:
                a[1] = value
                return
        self.attrs.append([name, value])

    def text_content(self) -> str:
        return "".join(c.text if isinstance(c, (Text, Raw)) else c.text_content() if isinstance(c, Element) else ""
                       for c in self.children)


@dataclass
class Block(Node):
    """Angular control-flow block, e.g. `@if (cond) { ... }`."""

    header: str
    children: list[Node] = field(default_factory=list)


def parse_attrs(text: str) -> list[list]:
    attrs = []
    for m in _ATTR_RE.finditer(text or ""):
        value = m.group(2)
        if value is not None and value[:1] in "\"'":
            value = value[1:-1]
        attrs.append([m.group(1), value])
    return attrs


def parse_html(src: str) -> list[Node]:
    root = Element("#root")
    stack = [root]
    i = 0
    n = len(src)
    while i < n:
        lt = src.find("<", i)
        if lt == -1:
            stack[-1].children.append(Text(src[i:]))
            break
        if lt > i:
            stack[-1].children.append(Text(src[i:lt]))
        if src.startswith("<!--", lt):
            end = src.find("-->", lt)
            end = n if end == -1 else end + 3
            stack[-1].children.append(Comment(src[lt:end]))
            i = end
            continue
        if src.startswith("<!", lt):
            end = src.find(">", lt)
            end = n if end == -1 else end + 1
            stack[-1].children.append(Raw(src[lt:end]))
            i = end
            continue
        m = _TAG_RE.match(src, lt)
        if not m:
            stack[-1].children.append(Text("<"))
            i = lt + 1
            continue
        closing, tag, attr_text, self_close = m.group(1), m.group(2), m.group(3), m.group(4)
        i = m.end()
        if closing:
            for depth in range(len(stack) - 1, 0, -1):
                if stack[depth].tag.lower() == tag.lower():
                    del stack[depth:]
                    break
            continue
        el = Element(tag, parse_attrs(attr_text), [], bool(self_close))
        stack[-1].children.append(el)
        if tag.lower() in RAW_TEXT_TAGS and not self_close:
            close = re.compile(rf"</{tag}\s*>", re.I).search(src, i)
            end = close.start() if close else n
            el.children.append(Raw(src[i:end]))
            i = close.end() if close else n
        elif not self_close and tag.lower() not in VOID_TAGS:
            stack.append(el)
    return root.children


def _attr_to_str(name: str, value) -> str:
    if value is None:
        return name
    quote = '"' if '"' not in value else "'"
    return f"{name}={quote}{value}{quote}"


def serialize(nodes: list[Node], indent: str = "") -> str:
    out = []
    for node in nodes:
        if isinstance(node, (Text, Comment, Raw)):
            out.append(node.text)
        elif isinstance(node, Block):
            out.append(f"{node.header} {{{serialize(node.children)}}}")
        elif isinstance(node, Element):
            attrs = "".join(" " + _attr_to_str(n, v) for n, v in node.attrs)
            if node.tag.lower() in VOID_TAGS:
                out.append(f"<{node.tag}{attrs}>")
            elif node.self_closing and not node.children:
                out.append(f"<{node.tag}{attrs} />")
            else:
                out.append(f"<{node.tag}{attrs}>{serialize(node.children)}</{node.tag}>")
    return "".join(out)


def iter_elements(nodes: list[Node]):
    for node in nodes:
        if isinstance(node, (Element, Block)):
            if isinstance(node, Element):
                yield node
            yield from iter_elements(node.children)


def find_first(nodes: list[Node], tag: str) -> Element | None:
    for el in iter_elements(nodes):
        if el.tag.lower() == tag:
            return el
    return None
