"""Lightweight, dependency-free JavaScript scanning helpers.

These are not a full JS parser; they understand enough lexical structure
(strings, template literals, comments, regex literals, bracket nesting) to
reliably slice Polymer element definitions apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
CLOSERS = set(OPEN_TO_CLOSE.values())
_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KEYWORDS = ("return", "typeof", "case", "in", "of", "delete", "void", "throw", "new")


def _skip_string(src: str, i: int) -> int:
    """`i` points at the opening quote; returns index just after the closing quote."""
    quote = src[i]
    i += 1
    n = len(src)
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        if c == "\n" and quote != "`":
            return i + 1
        if quote == "`" and c == "$" and i + 1 < n and src[i + 1] == "{":
            end = find_matching(src, i + 1)
            i = end + 1 if end != -1 else n
            continue
        i += 1
    return n


def _is_regex_start(src: str, i: int) -> bool:
    j = i - 1
    while j >= 0 and src[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    if src[j] in _REGEX_PRECEDERS:
        return True
    word_end = j + 1
    while j >= 0 and (src[j].isalnum() or src[j] in "_$"):
        j -= 1
    return src[j + 1:word_end] in _REGEX_KEYWORDS


def _skip_regex(src: str, i: int) -> int:
    i += 1
    in_class = False
    n = len(src)
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            return i
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            i += 1
            while i < n and src[i].isalpha():
                i += 1
            return i
        i += 1
    return n


def skip_non_code(src: str, i: int) -> int:
    """If `i` starts a string/comment/regex, return index after it, else `i`."""
    c = src[i]
    if c in "'\"`":
        return _skip_string(src, i)
    if c == "/" and i + 1 < len(src):
        nxt = src[i + 1]
        if nxt == "/":
            end = src.find("\n", i)
            return len(src) if end == -1 else end
        if nxt == "*":
            end = src.find("*/", i + 2)
            return len(src) if end == -1 else end + 2
        if _is_regex_start(src, i):
            return _skip_regex(src, i)
    return i


def find_matching(src: str, open_idx: int) -> int:
    """Return index of the bracket matching the one at `open_idx` (or -1)."""
    stack = [OPEN_TO_CLOSE[src[open_idx]]]
    i = open_idx + 1
    n = len(src)
    while i < n:
        j = skip_non_code(src, i)
        if j != i:
            i = j
            continue
        c = src[i]
        if c in OPEN_TO_CLOSE:
            stack.append(OPEN_TO_CLOSE[c])
        elif c in CLOSERS:
            if not stack or stack[-1] != c:
                return -1
            stack.pop()
            if not stack:
                return i
        i += 1
    return -1


def split_top_level(src: str, sep: str = ",") -> list[str]:
    """Split on `sep` occurring at bracket depth 0 (outside strings/comments)."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    n = len(src)
    while i < n:
        j = skip_non_code(src, i)
        if j != i:
            i = j
            continue
        c = src[i]
        if c in OPEN_TO_CLOSE:
            depth += 1
        elif c in CLOSERS:
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(src[start:i])
            start = i + 1
        i += 1
    parts.append(src[start:])
    return [p for p in (s.strip() for s in parts) if p]


def find_in_code(src: str, pattern: str | re.Pattern, start: int = 0) -> re.Match | None:
    """Find the first regex match that does not start inside a string/comment."""
    rx = re.compile(pattern) if isinstance(pattern, str) else pattern
    i = start
    n = len(src)
    while i < n:
        j = skip_non_code(src, i)
        if j != i:
            i = j
            continue
        m = rx.match(src, i)
        if m:
            return m
        i += 1
    return None


def find_expression_end(src: str, start: int) -> int:
    """Index where the JS expression starting at `start` ends (exclusive).

    Stops at `;`, `,` (depth 0), an unmatched closer, or a newline that is not
    obviously continued by an operator.
    """
    depth = 0
    i = start
    n = len(src)
    while i < n:
        j = skip_non_code(src, i)
        if j != i:
            i = j
            continue
        c = src[i]
        if c in OPEN_TO_CLOSE:
            depth += 1
        elif c in CLOSERS:
            if depth == 0:
                return i
            depth -= 1
        elif depth == 0 and c in ";,":
            return i
        elif depth == 0 and c == "\n":
            before = src[start:i].rstrip()
            after = src[i:].lstrip()
            continues = (before and before[-1] in "+-*/%&|?:=(,<>!.") or (
                after and (after[0] in ".?:+-*/%&|<>=" and not after.startswith(("++", "--")))
            )
            if not continues:
                return i
        i += 1
    return n


def extract_template_literal(src: str, backtick_idx: int) -> tuple[str, int]:
    """Return (content, end_index) for a template literal starting at backtick_idx."""
    end = _skip_string(src, backtick_idx)
    return src[backtick_idx + 1:end - 1], end


def dedent_block(text: str) -> str:
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    indents = [len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()]
    pad = min(indents) if indents else 0
    return "\n".join(ln[pad:] if ln.strip() else "" for ln in lines)


@dataclass
class JsMember:
    name: str
    kind: str  # method | get | set | field
    params: str = ""
    body: str = ""
    is_static: bool = False
    is_async: bool = False
    value: str = ""  # for fields

    def to_dict(self) -> dict:
        return self.__dict__.copy()


_IDENT = re.compile(r"[A-Za-z_$][\w$]*")


def parse_class_members(body: str) -> list[JsMember]:
    """Parse the members of an ES class body (text between the braces)."""
    members: list[JsMember] = []
    i = 0
    n = len(body)
    while i < n:
        j = skip_non_code(body, i)
        if j != i:
            i = j
            continue
        if body[i] in " \t\r\n;":
            i += 1
            continue
        is_static = is_async = False
        kind = "method"
        while True:
            m = re.match(r"(static|async|get|set)\s+(?=[\w$\[*'\"])", body[i:])
            if not m:
                break
            word = m.group(1)
            if word == "static":
                is_static = True
            elif word == "async":
                is_async = True
            else:
                kind = word
            i += m.end()
        if i < n and body[i] == "*":
            i += 1
        if i >= n:
            break
        if body[i] == "[":
            end = find_matching(body, i)
            name = body[i:end + 1]
            i = end + 1
        elif body[i] in "'\"":
            end = _skip_string(body, i)
            name = body[i + 1:end - 1]
            i = end
        else:
            m = _IDENT.match(body, i)
            if not m:
                i += 1
                continue
            name = m.group(0)
            i = m.end()
        while i < n and body[i] in " \t\r\n":
            i += 1
        if i < n and body[i] == "(":
            p_end = find_matching(body, i)
            params = body[i + 1:p_end]
            k = p_end + 1
            while k < n and body[k] != "{":
                k += 1
            b_end = find_matching(body, k)
            if b_end == -1:
                break
            members.append(JsMember(name, kind, params.strip(), body[k + 1:b_end], is_static, is_async))
            i = b_end + 1
        else:
            value = ""
            if i < n and body[i] == "=":
                end = find_expression_end(body, i + 1)
                value = body[i + 1:end].strip()
                i = end
            members.append(JsMember(name, "field", value=value, is_static=is_static))
    return members


_OBJ_METHOD = re.compile(r"^(async\s+)?(?:(get|set)\s+)?\*?\s*([\w$]+|'[^']*'|\"[^\"]*\")\s*\(", re.S)
_OBJ_KEY = re.compile(r"^(['\"]?)([\w$\-.]+)\1\s*:\s*", re.S)
_FUNC_VALUE = re.compile(r"^(async\s+)?function\s*[\w$]*\s*\(", re.S)
_ARROW_VALUE = re.compile(r"^(async\s+)?(\([^)]*\)|[\w$]+)\s*=>\s*", re.S)


@dataclass
class ObjectEntry:
    key: str
    value: str = ""
    member: JsMember | None = None
    shorthand: bool = False


def _member_from_function(name: str, text: str, is_async: bool = False) -> JsMember | None:
    p_start = text.find("(")
    p_end = find_matching(text, p_start)
    if p_end == -1:
        return None
    rest = text[p_end + 1:]
    b_rel = rest.find("{")
    if b_rel == -1:
        return None
    b_start = p_end + 1 + b_rel
    b_end = find_matching(text, b_start)
    return JsMember(name, "method", text[p_start + 1:p_end].strip(), text[b_start + 1:b_end], is_async=is_async)


def parse_object_literal(inner: str) -> list[ObjectEntry]:
    """Parse the entries of an object literal (text between the braces)."""
    entries: list[ObjectEntry] = []
    for raw in split_top_level(inner, ","):
        raw = _strip_leading_comments(raw)
        if not raw or raw.startswith("..."):
            continue
        m = _OBJ_METHOD.match(raw)
        if m and not _OBJ_KEY.match(raw):
            name = m.group(3).strip("'\"")
            member = _member_from_function(name, raw[m.start(3):], bool(m.group(1)))
            if member:
                if m.group(2):
                    member.kind = m.group(2)
                entries.append(ObjectEntry(name, raw, member))
            continue
        m = _OBJ_KEY.match(raw)
        if not m:
            if _IDENT.fullmatch(raw):
                entries.append(ObjectEntry(raw, raw, shorthand=True))
            continue
        key, value = m.group(2), raw[m.end():].strip()
        member = None
        fm = _FUNC_VALUE.match(value)
        if fm:
            member = _member_from_function(key, value, bool(fm.group(1)))
        else:
            am = _ARROW_VALUE.match(value)
            if am:
                params = am.group(2).strip()
                params = params[1:-1] if params.startswith("(") else params
                rest = value[am.end():].strip()
                if rest.startswith("{"):
                    body = rest[1:find_matching(rest, 0)]
                else:
                    body = f"return {rest};"
                member = JsMember(key, "method", params.strip(), body, is_async=bool(am.group(1)))
        entries.append(ObjectEntry(key, value, member))
    return entries


def _strip_leading_comments(text: str) -> str:
    text = text.strip()
    while text.startswith(("//", "/*")):
        end = skip_non_code(text, 0)
        text = text[end:].strip()
    return text


def object_inner(src: str, brace_idx: int) -> str:
    end = find_matching(src, brace_idx)
    return src[brace_idx + 1:end] if end != -1 else ""


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"`":
        return value[1:-1]
    return value


def function_return_expression(value: str) -> str | None:
    """For `function(){ return X; }` or `() => X` return `X`; else None."""
    value = value.strip()
    am = _ARROW_VALUE.match(value)
    if am:
        rest = value[am.end():].strip()
        if not rest.startswith("{"):
            return rest.rstrip(";")
        body = rest[1:find_matching(rest, 0)]
    elif _FUNC_VALUE.match(value):
        b = value.find("{")
        body = value[b + 1:find_matching(value, b)]
    else:
        return None
    m = re.search(r"\breturn\s+", body)
    if not m:
        return None
    end = find_expression_end(body, m.end())
    return body[m.end():end].strip()


@dataclass
class ImportStmt:
    specifiers: list[str] = field(default_factory=list)
    source: str = ""
    raw: str = ""


def parse_es_imports(src: str) -> list[ImportStmt]:
    out: list[ImportStmt] = []
    for m in re.finditer(r"^\s*import\s+(?:([\w${},*\s]*?)\s+from\s+)?['\"]([^'\"]+)['\"]\s*;?", src, re.M):
        spec = m.group(1) or ""
        names = re.findall(r"[\w$]+(?:\s+as\s+[\w$]+)?", spec.replace("{", " ").replace("}", " "))
        out.append(ImportStmt([n.split(" as ")[-1].strip() for n in names if n != "as"], m.group(2), m.group(0).strip()))
    return out
