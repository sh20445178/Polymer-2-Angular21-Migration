"""Per-component conversion context shared by the template/script/style converters."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ComponentContext:
    tag: str
    class_name: str
    file_path: str  # e.g. src/app/components/my-el/my-el.ts
    properties: dict[str, dict] = field(default_factory=dict)  # name -> property dict (+ "kind")
    methods: set[str] = field(default_factory=set)
    method_arity: dict[str, int] = field(default_factory=dict)
    model_handlers: set[str] = field(default_factory=set)
    dollar_ids: set[str] = field(default_factory=set)
    registry: dict[str, dict] = field(default_factory=dict)
    style_modules: set[str] = field(default_factory=set)

    # ---- collected while converting the template
    used_components: set[str] = field(default_factory=set)
    material: dict[str, str] = field(default_factory=dict)  # symbol -> module path
    angular_imports: dict[str, str] = field(default_factory=dict)  # symbol -> module path
    two_way_targets: set[str] = field(default_factory=set)
    template_roots: set[str] = field(default_factory=set)
    ref_kinds: dict[str, str] = field(default_factory=dict)  # ref name -> "native" | component class
    extra_computed: list[tuple[str, str]] = field(default_factory=list)
    ajax: list[dict] = field(default_factory=list)
    route_bindings: list[dict] = field(default_factory=list)
    needs_custom_schema: bool = False
    needs_to_attr: bool = False
    external_elements: set[str] = field(default_factory=set)
    host: dict[str, str] = field(default_factory=dict)
    todos: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def is_signal(self, name: str) -> bool:
        return name in self.properties or name not in self.methods

    def handler_args(self, handler: str, args: list[str]) -> list[str]:
        """Polymer passes the event to every handler; Angular type-checks arity, so drop unused args."""
        arity = self.method_arity.get(handler)
        if arity is None or handler in self.model_handlers:
            return args
        return args[:arity]

    def todo(self, message: str) -> None:
        if message not in self.todos:
            self.todos.append(message)
