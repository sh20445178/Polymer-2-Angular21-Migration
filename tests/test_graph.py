import json
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from polymer2angular.config import MigrationOptions
from polymer2angular.graph import build_graph, run_migration

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _run(example: str, out: Path, **opts) -> dict:
    options = MigrationOptions(project_name=example, **opts).to_dict()
    return run_migration(str(EXAMPLES / example), str(out), options)


def test_graph_has_all_agents():
    nodes = set(build_graph().get_graph().nodes)
    assert {"scanner", "analyzer", "planner", "component_migrator", "reviewer", "fixer", "assembler", "writer",
            "build_verifier", "reporter"} <= nodes


def test_end_to_end_polymer2_shop(tmp_path):
    state = _run("polymer2-shop", tmp_path)
    assert state["review"]["errors"] == 0
    assert set(state["components"]) == {"my-app", "my-products", "my-cart", "my-view404"}
    for rel in ("package.json", "angular.json", "src/main.ts", "src/app/app.ts", "src/app/app.config.ts",
                "src/app/components/my-app/my-app.ts", "src/app/pages/my-cart/my-cart.html",
                "src/styles/_shared-styles.scss", "public/images/logo.svg", "MIGRATION_REPORT.md"):
        assert (tmp_path / rel).exists(), rel
    pkg = json.loads((tmp_path / "package.json").read_text())
    assert pkg["dependencies"]["@angular/core"].startswith("^21.")
    assert "@angular/material" in pkg["dependencies"]
    assert "provideZonelessChangeDetection()" in (tmp_path / "src/app/app.config.ts").read_text()
    app_html = (tmp_path / "src/app/components/my-app/my-app.html").read_text()
    assert "@switch (page())" in app_html and '[(items)]="cartItems"' in app_html


def test_end_to_end_polymer3_todo_dry_run(tmp_path):
    state = _run("polymer3-todo", tmp_path / "out", dry_run=True)
    assert state["review"]["errors"] == 0
    assert not (tmp_path / "out").exists()
    assert "log(...args)" in state["components"]["todo-input"]["ts"]  # mixin inlined


def test_llm_refinement_and_fix_loop(tmp_path, monkeypatch):
    """Fake LLM: refinement output that drops the public API must be rejected; fixer loop stays bounded."""
    bad = json.dumps({"ts": "export class Broken {}", "html": "<p></p>", "scss": "", "notes": []})
    fake = FakeListChatModel(responses=[bad] * 50)
    monkeypatch.setattr("polymer2angular.agents.analyzer.get_llm", lambda options: fake)
    monkeypatch.setattr("polymer2angular.agents.component_migrator.get_llm", lambda options: fake)
    monkeypatch.setattr("polymer2angular.agents.fixer.get_llm", lambda options: fake)
    logs = []
    options = MigrationOptions(llm_provider="fake", llm_model="fake", max_fix_iterations=1).to_dict()
    state = run_migration(str(EXAMPLES / "polymer3-todo"), str(tmp_path), options,
                          on_event=lambda node, line: logs.append(line))
    assert any("LLM output rejected" in line for line in logs)
    assert all(not c["llm_refined"] for c in state["components"].values())
    assert state["review"]["errors"] == 0


def test_review_fix_loop_is_bounded(tmp_path, monkeypatch):
    src = tmp_path / "src-app"
    src.mkdir()
    (src / "x-broken.html").write_text(
        '<dom-module id="x-broken"><template><p>[[missing(name)]]</p></template><script>'
        "class XBroken extends Polymer.Element { static get is() { return 'x-broken'; } "
        "static get properties() { return { name: String }; } }"
        "customElements.define(XBroken.is, XBroken);</script></dom-module>")
    fake = FakeListChatModel(responses=["not json"] * 50)
    for mod in ("analyzer", "component_migrator", "fixer"):
        monkeypatch.setattr(f"polymer2angular.agents.{mod}.get_llm", lambda options: fake)
    logs = []
    options = MigrationOptions(llm_provider="fake", llm_model="fake", max_fix_iterations=2).to_dict()
    state = run_migration(str(src), str(tmp_path / "out"), options, on_event=lambda n, line: logs.append(line))
    assert [line for line in logs if line.startswith("[fixer] iteration")] == ["[fixer] iteration 1",
                                                                               "[fixer] iteration 2"]
    assert state["review"]["errors"] == 1
    assert state["components"]["x-broken"]["status"] == "needs-fix"
    assert (tmp_path / "out" / "MIGRATION_REPORT.md").exists()
