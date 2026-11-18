"""Command line interface: `polymer2angular migrate|analyze|graph`."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import MigrationOptions


def _resolve_source(source: str, branch: str | None) -> str:
    """Accept a local path or a git URL (cloned into a temporary directory)."""
    if re.match(r"^(https?://|git@|ssh://)", source) or source.endswith(".git"):
        target = tempfile.mkdtemp(prefix="p2a-src-")
        cmd = ["git", "clone", "--depth", "1"] + (["--branch", branch] if branch else []) + [source, target]
        subprocess.run(cmd, check=True)
        return target
    return str(Path(source).resolve())


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--source", "-s", required=True, help="Polymer application path or git URL")
    p.add_argument("--branch", help="Branch to clone when --source is a git URL")
    p.add_argument("--exclude", action="append", default=[], help="Extra directory names to ignore (repeatable)")


def cmd_migrate(args) -> int:
    from .graph import run_migration

    source = _resolve_source(args.source, args.branch)
    output = str(Path(args.output).resolve())
    options = MigrationOptions.from_env(
        project_name=args.project_name or re.sub(r"[^a-z0-9-]", "-", Path(output).name.lower()) or "migrated-app",
        llm_provider=args.llm_provider, llm_model=args.llm_model, temperature=args.temperature,
        max_fix_iterations=args.max_fix_iterations, verify_build=args.verify_build, dry_run=args.dry_run,
    )
    options.exclude_dirs += args.exclude
    if options.llm_provider == "none":
        options.llm_provider = None
    if options.llm_provider and not options.llm_model:
        print("error: --llm-model (or $P2A_LLM_MODEL) is required when an LLM provider is set", file=sys.stderr)
        return 2
    if options.llm_provider:
        from .config import get_llm

        try:
            get_llm(options.to_dict())
        except Exception as exc:
            print(f"error: cannot initialise LLM provider '{options.llm_provider}': {exc}", file=sys.stderr)
            return 2

    def show(node: str, line: str) -> None:
        print(line, flush=True)

    state = run_migration(source, output, options.to_dict(), on_event=show)
    review = state.get("review", {})
    build = state.get("build") or {}
    print("\nSummary:")
    print(f"  components : {len(state.get('components', {}))}")
    print(f"  review     : {review.get('errors', 0)} errors, {review.get('warnings', 0)} warnings")
    print(f"  build      : {build.get('status', 'not run')}")
    if state.get("report_path"):
        print(f"  report     : {state['report_path']}")
    failed = [t for t, c in state.get("components", {}).items() if c.get("status") == "failed"]
    return 1 if failed or build.get("status") in ("failed", "install-failed") else 0


def cmd_analyze(args) -> int:
    from .agents.analyzer import analyze
    from .agents.planner import build_plan
    from .agents.scanner import scan_repository

    options = MigrationOptions.from_env()
    source = _resolve_source(args.source, args.branch)
    scan = scan_repository(source, options.exclude_dirs + args.exclude)
    result = analyze(scan)
    plan, _ = build_plan(result["elements"], result["analysis"])
    summary = {
        "source": source,
        "files": {k: len(v) for k, v in scan["files"].items()},
        "parse_errors": scan["parse_errors"],
        **{k: v for k, v in result["analysis"].items() if k != "package_dependencies"},
        "plan": {t: {k: e[k] for k in ("class_name", "ts_path", "kinds", "outputs")} for t, e in plan["entries"].items()},
        "order": plan["order"],
    }
    print(json.dumps(summary, indent=2))
    return 0


def cmd_graph(args) -> int:
    from .graph import build_graph

    print(build_graph().get_graph().draw_mermaid())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="polymer2angular",
                                     description="LangGraph multi-agent migrator: Polymer -> Angular 21")
    sub = parser.add_subparsers(dest="command", required=True)

    m = sub.add_parser("migrate", help="Scan a Polymer repository and generate an Angular 21 workspace")
    _add_common(m)
    m.add_argument("--output", "-o", required=True, help="Output directory for the Angular workspace")
    m.add_argument("--project-name", help="npm/Angular project name (default: output folder name)")
    m.add_argument("--llm-provider", help="azure_openai | openai | anthropic | ollama | google_genai | none "
                                          "(default: $P2A_LLM_PROVIDER; unset = rule-based only)")
    m.add_argument("--llm-model", help="Model / Azure deployment name (default: $P2A_LLM_MODEL)")
    m.add_argument("--temperature", type=float, help="LLM temperature (omit for model default)")
    m.add_argument("--max-fix-iterations", type=int, default=2, help="Max reviewer/build -> fixer loops")
    m.add_argument("--verify-build", action="store_true", help="Run npm install + ng build and feed errors back")
    m.add_argument("--dry-run", action="store_true", help="Run all agents but do not write files")
    m.set_defaults(func=cmd_migrate)

    a = sub.add_parser("analyze", help="Scan + analyze + plan only; print JSON")
    _add_common(a)
    a.set_defaults(func=cmd_analyze)

    g = sub.add_parser("graph", help="Print the agent graph as Mermaid")
    g.set_defaults(func=cmd_graph)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
