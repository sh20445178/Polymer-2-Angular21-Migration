"""LangGraph wiring of the multi-agent Polymer -> Angular 21 migration workflow.

    START -> scanner -> analyzer -> planner --(Send x N)--> component_migrator -> reviewer
    reviewer --(errors & LLM & budget)--> fixer -> reviewer
    reviewer --> assembler -> writer --(verify_build)--> build_verifier --(errors & LLM & budget)--> fixer
                                     \\-> reporter -> END            \\-> reporter -> END
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents.analyzer import analyzer_agent
from .agents.assembler import assembler_agent
from .agents.build_verifier import build_verifier_agent
from .agents.component_migrator import component_migrator_agent
from .agents.fixer import fixer_agent
from .agents.planner import planner_agent
from .agents.reporter import reporter_agent
from .agents.reviewer import reviewer_agent
from .agents.scanner import scanner_agent
from .agents.writer import writer_agent
from .config import llm_enabled
from .state import MigrationState


# --------------------------------------------------------------------------- supervisor routing


def dispatch_components(state: MigrationState) -> list[Send]:
    """Fan out one component-migrator agent per Polymer element (executed in parallel)."""
    plan = state["plan"]
    style_modules = sorted(state.get("style_modules", {}))
    known_mixins = state.get("analysis", {}).get("known_mixins", {})
    sends = [
        Send("component_migrator", {
            "tag": tag,
            "element": state["elements"][tag],
            "plan_entry": plan["entries"][tag],
            "registry": state["registry"],
            "style_modules": style_modules,
            "known_mixins": known_mixins,
            "options": state.get("options", {}),
        })
        for tag in plan["order"]
    ]
    return sends or [Send("reviewer", state)]


def _can_fix(state: MigrationState) -> bool:
    options = state.get("options", {})
    return llm_enabled(options) and state.get("fix_iteration", 0) < options.get("max_fix_iterations", 2)


def route_after_review(state: MigrationState) -> str:
    if state.get("review", {}).get("errors", 0) and _can_fix(state):
        return "fixer"
    return "assembler"


def route_after_fix(state: MigrationState) -> str:
    return "reviewer"


def route_after_write(state: MigrationState) -> str:
    options = state.get("options", {})
    if options.get("verify_build") and not options.get("dry_run"):
        return "build_verifier"
    return "reporter"


def route_after_build(state: MigrationState) -> str:
    build = state.get("build") or {}
    if build.get("status") == "failed" and build.get("errors") and _can_fix(state):
        return "fixer"
    return "reporter"


def _reset_build_flag(state: MigrationState) -> dict:
    """After a build-driven fix, re-run review/assembly/write/build with a clean build state."""
    return {"build": {}}


# --------------------------------------------------------------------------- graph


def build_graph(checkpointer=None):
    graph = StateGraph(MigrationState)
    graph.add_node("scanner", scanner_agent)
    graph.add_node("analyzer", analyzer_agent)
    graph.add_node("planner", planner_agent)
    graph.add_node("component_migrator", component_migrator_agent)
    graph.add_node("reviewer", reviewer_agent)
    graph.add_node("fixer", fixer_agent)
    graph.add_node("post_fix", _reset_build_flag)
    graph.add_node("assembler", assembler_agent)
    graph.add_node("writer", writer_agent)
    graph.add_node("build_verifier", build_verifier_agent)
    graph.add_node("reporter", reporter_agent)

    graph.add_edge(START, "scanner")
    graph.add_edge("scanner", "analyzer")
    graph.add_edge("analyzer", "planner")
    graph.add_conditional_edges("planner", dispatch_components, ["component_migrator", "reviewer"])
    graph.add_edge("component_migrator", "reviewer")
    graph.add_conditional_edges("reviewer", route_after_review, {"fixer": "fixer", "assembler": "assembler"})
    graph.add_edge("fixer", "post_fix")
    graph.add_conditional_edges("post_fix", route_after_fix, {"reviewer": "reviewer"})
    graph.add_edge("assembler", "writer")
    graph.add_conditional_edges("writer", route_after_write,
                                {"build_verifier": "build_verifier", "reporter": "reporter"})
    graph.add_conditional_edges("build_verifier", route_after_build, {"fixer": "fixer", "reporter": "reporter"})
    graph.add_edge("reporter", END)
    return graph.compile(checkpointer=checkpointer)


def run_migration(source_dir: str, output_dir: str, options: dict, checkpointer=None, thread_id: str = "migration",
                  on_event=None) -> dict:
    """Execute the graph, streaming agent log lines to `on_event(node, line)`; returns the final state."""
    app = build_graph(checkpointer)
    initial: MigrationState = {"source_dir": source_dir, "output_dir": output_dir, "options": options,
                               "components": {}, "log": []}
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 200}
    final_state: dict = {}
    for mode, chunk in app.stream(initial, config=config, stream_mode=["updates", "values"]):
        if mode == "values":
            final_state = chunk
        elif on_event:
            for node, payload in chunk.items():
                for line in (payload or {}).get("log", []) if isinstance(payload, dict) else []:
                    on_event(node, line)
    return final_state
