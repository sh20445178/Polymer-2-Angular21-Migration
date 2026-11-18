"""Naming helpers shared across agents."""

from __future__ import annotations

import re


def kebab_to_camel(name: str) -> str:
    parts = re.split(r"[-_](?=[a-zA-Z0-9])", name)
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def kebab_to_pascal(name: str) -> str:
    camel = kebab_to_camel(name)
    return camel[:1].upper() + camel[1:]


def camel_to_kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def event_to_output(event: str) -> str:
    """`user-selected` -> `userSelected`; `value-changed` -> `valueChange`."""
    if event.endswith("-changed"):
        return kebab_to_camel(event[: -len("-changed")]) + "Change"
    return kebab_to_camel(event)


def safe_identifier(name: str) -> str:
    name = re.sub(r"[^\w$]", "_", name)
    return name if not name[:1].isdigit() else "_" + name
