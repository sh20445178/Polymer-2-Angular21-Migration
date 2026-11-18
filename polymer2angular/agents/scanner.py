"""Repository Scanner agent: walks the source repository and parses every Polymer artefact."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..parsing.polymer_parser import parse_html_file, parse_js_file
from ..utils import html_dom as dom

ASSET_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".avif", ".woff", ".woff2", ".ttf", ".eot",
              ".otf", ".json", ".mp3", ".mp4", ".webm", ".pdf", ".txt"}
MANIFESTS = {"bower.json", "package.json", "polymer.json", "manifest.json", "package-lock.json"}


def _iter_files(root: Path, excludes: set[str]):
    for path in sorted(root.rglob("*")):
        rel_parts = path.relative_to(root).parts
        if any(part in excludes or part.startswith(".") for part in rel_parts[:-1]):
            continue
        if path.is_file():
            yield path


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_index(text: str) -> dict:
    """Extract body markup, global styles and head links from the application's index.html."""
    body = re.search(r"<body[^>]*>([\s\S]*?)</body>", text, re.I)
    title = re.search(r"<title>([\s\S]*?)</title>", text, re.I)
    head = re.search(r"<head[^>]*>([\s\S]*?)</head>", text, re.I)
    head_html = head.group(1) if head else ""
    styles = [m.group(1) for m in re.finditer(r"<style[^>]*>([\s\S]*?)</style>", head_html, re.I)]
    links = []
    for el in dom.iter_elements(dom.parse_html(head_html)):
        rel = (el.get("rel") or "").lower()
        if el.tag.lower() == "link" and rel in ("icon", "shortcut icon", "manifest", "apple-touch-icon"):
            links.append(dom.serialize([el]))
    return {
        "body": body.group(1).strip() if body else "",
        "title": title.group(1).strip() if title else "",
        "global_styles": styles,
        "head_links": links,
        "entry_imports": re.findall(r"<link[^>]+rel=[\"']import[\"'][^>]*href=[\"']([^\"']+)", text)
        + re.findall(r"<script[^>]+type=[\"']module[\"'][^>]*src=[\"']([^\"']+)", text),
    }


def scan_repository(source_dir: str, exclude_dirs: list[str]) -> dict:
    root = Path(source_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Source directory not found: {root}")
    excludes = set(exclude_dirs)
    models: list[dict] = []
    files = {"html": [], "js": [], "css": [], "assets": [], "manifests": [], "other": []}
    dependencies: dict[str, str] = {}
    parse_errors: list[str] = []
    index: dict = {}

    for path in _iter_files(root, excludes):
        rel = path.relative_to(root).as_posix()
        ext = path.suffix.lower()
        name = path.name.lower()
        try:
            if name in MANIFESTS:
                files["manifests"].append(rel)
                if name in ("bower.json", "package.json") and "/" not in rel:
                    data = json.loads(_read(path) or "{}")
                    dependencies.update(data.get("dependencies", {}))
                if name == "manifest.json":
                    files["assets"].append(rel)
            elif ext == ".html":
                files["html"].append(rel)
                text = _read(path)
                if rel == "index.html" or (not index and name == "index.html"):
                    index = _parse_index(text)
                if "<dom-module" in text or "Polymer" in text or "<script" in text:
                    found, _ = parse_html_file(text, rel)
                    models.extend(m.to_dict() for m in found)
            elif ext in (".js", ".mjs"):
                files["js"].append(rel)
                text = _read(path)
                if re.search(r"Polymer|PolymerElement|dom-module|Behaviou?r\s*=|dedupingMixin", text):
                    models.extend(m.to_dict() for m in parse_js_file(text, rel))
            elif ext in (".css", ".scss"):
                files["css"].append(rel)
            elif ext in ASSET_EXTS and not (ext == ".json" and re.search(r"config|rc\.json$|lock", name)):
                files["assets"].append(rel)
            else:
                files["other"].append(rel)
        except Exception as exc:  # keep scanning; report the failure
            parse_errors.append(f"{rel}: {exc}")

    versions = sorted({m.get("polymer_version") for m in models if m.get("kind") == "element"})
    return {
        "root": str(root),
        "files": files,
        "models": models,
        "dependencies": dependencies,
        "index": index,
        "polymer_versions": versions,
        "parse_errors": parse_errors,
    }


def scanner_agent(state: dict) -> dict:
    options = state.get("options", {})
    scan = scan_repository(state["source_dir"], options.get("exclude_dirs", []))
    counts = {k: len(v) for k, v in scan["files"].items()}
    return {
        "scan": scan,
        "log": [f"[scanner] scanned {state['source_dir']}: {counts}; found {len(scan['models'])} Polymer definitions "
                f"(versions {', '.join(scan['polymer_versions']) or 'n/a'})"]
        + [f"[scanner] parse error: {e}" for e in scan["parse_errors"]],
    }
