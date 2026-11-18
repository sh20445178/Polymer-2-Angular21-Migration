"""Build Verifier agent: installs dependencies and runs `ng build`, mapping compiler errors to components."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ERROR_RE = re.compile(r"\[ERROR\]\s+(?P<msg>[^\n]+)(?:\n(?!\s*✘)[^\n]*)*?\n\s+(?P<file>src/[^\s:]+):(?P<line>\d+):(?P<col>\d+)")


def parse_build_errors(output: str) -> list[dict]:
    errors = [{"message": m.group("msg").strip(), "file": m.group("file"), "line": int(m.group("line"))}
              for m in ERROR_RE.finditer(output)]
    if not errors:
        for m in re.finditer(r"(?P<file>src/[^\s:(]+)[:(](?P<line>\d+)[:,]\d+\)?:?\s*-?\s*error\s+(?P<msg>[^\n]+)", output):
            errors.append({"message": m.group("msg").strip(), "file": m.group("file"), "line": int(m.group("line"))})
    return errors


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "") + "\n" + (proc.stderr or "")


def verify_build(output_dir: str, timeout: int = 900) -> dict:
    out = Path(output_dir)
    npm = shutil.which("npm")
    if not npm:
        return {"status": "skipped", "reason": "npm not found on PATH", "errors": []}
    if not (out / "node_modules").exists():
        code, log = _run([npm, "install", "--no-audit", "--no-fund", "--loglevel=error"], out, timeout)
        if code != 0:
            return {"status": "install-failed", "errors": [], "output": log[-4000:]}
    code, log = _run([npm, "exec", "--", "ng", "build", "--configuration", "development"], out, timeout)
    errors = parse_build_errors(log)
    status = "passed" if code == 0 else "failed"
    return {"status": status, "errors": errors, "output": log[-6000:]}


def build_verifier_agent(state: dict) -> dict:
    options = state.get("options", {})
    result = verify_build(state["output_dir"], options.get("install_timeout", 900))
    by_file: dict[str, list[dict]] = {}
    for err in result.get("errors", []):
        by_file.setdefault(err["file"], []).append(err)
    updated = {}
    for tag, comp in state.get("components", {}).items():
        errs = [e for path in (comp.get("ts_path"), comp.get("html_path"), comp.get("scss_path"))
                for e in by_file.get(path, [])]
        if errs or comp.get("build_errors"):
            updated[tag] = {**comp, "build_errors": errs}
    summary = f"[build] ng build {result['status']}"
    if result.get("errors"):
        summary += f" with {len(result['errors'])} error(s)"
    return {"build": result, "components": updated, "log": [summary]}
