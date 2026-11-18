"""LangGraph state schema shared by all agents."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_dicts(left: dict | None, right: dict | None) -> dict:
    """Reducer used for fan-out results: shallow merge keyed by component tag."""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class MigrationState(TypedDict, total=False):
    source_dir: str
    output_dir: str
    options: dict[str, Any]

    scan: dict[str, Any]
    elements: dict[str, dict]
    behaviors: dict[str, dict]
    style_modules: dict[str, dict]
    analysis: dict[str, Any]
    plan: dict[str, Any]
    registry: dict[str, dict]

    components: Annotated[dict[str, dict], merge_dicts]
    project_files: dict[str, str]
    review: dict[str, Any]
    fix_iteration: int
    build: dict[str, Any]
    written_files: list[str]
    report_path: str

    log: Annotated[list[str], operator.add]


class ComponentTask(TypedDict):
    """Payload sent (via langgraph.types.Send) to each parallel component-migrator agent."""

    tag: str
    element: dict
    plan_entry: dict
    registry: dict
    style_modules: list[str]
    known_mixins: dict[str, str]
    options: dict[str, Any]
