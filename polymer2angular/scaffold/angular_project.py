"""Generate the Angular 21 workspace files around the migrated components."""

from __future__ import annotations

import json

from ..transform.context import ComponentContext
from ..transform.script import relative_import
from ..transform.styles import IRON_FLEX_SCSS, convert_css
from ..transform.template import TemplateConverter
from ..utils import html_dom as dom
from .compat import POLYMER_COMPAT_TS

ANGULAR_VERSION = "^21.2.0"
TYPESCRIPT_VERSION = "~5.9.2"
MATERIAL_THEME = "@angular/material/prebuilt-themes/azure-blue.css"


def _json(data: dict) -> str:
    return json.dumps(data, indent=2) + "\n"


def package_json(name: str, uses_material: bool) -> str:
    deps = {
        "@angular/common": ANGULAR_VERSION,
        "@angular/compiler": ANGULAR_VERSION,
        "@angular/core": ANGULAR_VERSION,
        "@angular/forms": ANGULAR_VERSION,
        "@angular/platform-browser": ANGULAR_VERSION,
        "@angular/router": ANGULAR_VERSION,
        "rxjs": "~7.8.0",
        "tslib": "^2.8.0",
    }
    if uses_material:
        deps["@angular/cdk"] = ANGULAR_VERSION
        deps["@angular/material"] = ANGULAR_VERSION
    return _json({
        "name": name,
        "version": "0.0.0",
        "private": True,
        "scripts": {"ng": "ng", "start": "ng serve", "build": "ng build", "watch": "ng build --watch --configuration development"},
        "dependencies": dict(sorted(deps.items())),
        "devDependencies": {
            "@angular/build": ANGULAR_VERSION,
            "@angular/cli": ANGULAR_VERSION,
            "@angular/compiler-cli": ANGULAR_VERSION,
            "typescript": TYPESCRIPT_VERSION,
        },
    })


def angular_json(name: str, uses_material: bool) -> str:
    styles = ([MATERIAL_THEME] if uses_material else []) + ["src/styles.scss"]
    return _json({
        "$schema": "./node_modules/@angular/cli/lib/config/schema.json",
        "version": 1,
        "newProjectRoot": "projects",
        "projects": {
            name: {
                "projectType": "application",
                "schematics": {"@schematics/angular:component": {"style": "scss"}},
                "root": "",
                "sourceRoot": "src",
                "prefix": "app",
                "architect": {
                    "build": {
                        "builder": "@angular/build:application",
                        "options": {
                            "browser": "src/main.ts",
                            "tsConfig": "tsconfig.app.json",
                            "inlineStyleLanguage": "scss",
                            "assets": [{"glob": "**/*", "input": "public"}],
                            "styles": styles,
                            "stylePreprocessorOptions": {"includePaths": ["src/styles"]},
                        },
                        "configurations": {
                            "production": {
                                "budgets": [
                                    {"type": "initial", "maximumWarning": "1MB", "maximumError": "2MB"},
                                    {"type": "anyComponentStyle", "maximumWarning": "8kB", "maximumError": "16kB"},
                                ],
                                "outputHashing": "all",
                            },
                            "development": {"optimization": False, "extractLicenses": False, "sourceMap": True},
                        },
                        "defaultConfiguration": "production",
                    },
                    "serve": {
                        "builder": "@angular/build:dev-server",
                        "configurations": {
                            "production": {"buildTarget": f"{name}:build:production"},
                            "development": {"buildTarget": f"{name}:build:development"},
                        },
                        "defaultConfiguration": "development",
                    },
                },
            }
        },
    })


TSCONFIG = _json({
    "compileOnSave": False,
    "compilerOptions": {
        "strict": False,
        "noImplicitAny": False,
        "noImplicitOverride": True,
        "noFallthroughCasesInSwitch": True,
        "skipLibCheck": True,
        "isolatedModules": True,
        "experimentalDecorators": True,
        "importHelpers": True,
        "target": "ES2022",
        "module": "preserve",
    },
    "angularCompilerOptions": {
        "enableI18nLegacyMessageIdFormat": False,
        "strictInjectionParameters": True,
        "strictInputAccessModifiers": True,
        "typeCheckHostBindings": True,
        "strictTemplates": True,
    },
})

TSCONFIG_APP = _json({
    "extends": "./tsconfig.json",
    "compilerOptions": {"outDir": "./out-tsc/app", "types": []},
    "include": ["src/**/*.ts"],
    "exclude": ["src/**/*.spec.ts"],
})

MAIN_TS = """import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';

bootstrapApplication(App, appConfig).catch((err) => console.error(err));
"""

APP_CONFIG_TS = """import {
  ApplicationConfig,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import { provideHttpClient, withFetch } from '@angular/common/http';
import { provideRouter } from '@angular/router';

import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideRouter(routes),
    provideHttpClient(withFetch()),
  ],
};
"""

GITIGNORE = "/node_modules\n/dist\n/out-tsc\n/.angular\n.DS_Store\n"


def routes_ts(uses_router: bool) -> str:
    body = (
        "  // Polymer <app-location>/<app-route> are emulated in the migrated components via\n"
        "  // Router events; the wildcard keeps every URL navigable. Replace with real routes\n"
        "  // (e.g. loadComponent) when refactoring page components.\n"
        "  { path: '**', children: [] },\n"
        if uses_router else ""
    )
    return "import { Routes } from '@angular/router';\n\nexport const routes: Routes = [\n" + body + "];\n"


def build_root_app(index_body: str, root_tags: list[str], registry: dict) -> dict[str, str]:
    """Create src/app/app.{ts,html,scss} hosting the original index.html body."""
    ctx = ComponentContext(tag="app-root", class_name="App", file_path="src/app/app.ts", registry=registry)
    nodes = [n for n in dom.parse_html(index_body or "") if not (isinstance(n, dom.Element) and n.tag.lower() in ("script", "noscript", "style"))]
    has_known = any(el.tag.lower() in registry for el in dom.iter_elements(nodes))
    if not has_known:
        nodes = []
        for tag in root_tags:
            nodes += [dom.Text("\n"), dom.Element(tag)]
        nodes.append(dom.Text("\n"))
    html = TemplateConverter(ctx).convert(dom.serialize(nodes))
    imports, import_lines = [], []
    for tag in sorted(ctx.used_components):
        spec = registry[tag]
        imports.append(spec["class_name"])
        import_lines.append(f"import {{ {spec['class_name']} }} from '{relative_import('src/app/app.ts', spec['import_path'])}';")
    schema = ctx.needs_custom_schema
    core = "ChangeDetectionStrategy, Component" + (", CUSTOM_ELEMENTS_SCHEMA" if schema else "")
    ts = (f"import {{ {core} }} from '@angular/core';\n" + "".join(l + "\n" for l in import_lines) + "\n"
          "@Component({\n  selector: 'app-root',\n"
          + (f"  imports: [{', '.join(imports)}],\n" if imports else "")
          + "  templateUrl: './app.html',\n  styleUrl: './app.scss',\n"
          "  changeDetection: ChangeDetectionStrategy.OnPush,\n"
          + ("  schemas: [CUSTOM_ELEMENTS_SCHEMA],\n" if schema else "")
          + "})\nexport class App {}\n")
    return {"src/app/app.ts": ts, "src/app/app.html": html, "src/app/app.scss": ":host {\n  display: block;\n}\n"}


def build_index_html(title: str, head_links: list[str], uses_material: bool) -> str:
    extra = list(head_links)
    if uses_material:
        extra.append('<link rel="preconnect" href="https://fonts.gstatic.com">')
        extra.append('<link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500&display=swap" rel="stylesheet">')
        extra.append('<link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">')
    links = "".join(f"  {l}\n" for l in extra)
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n"
            f"  <title>{title}</title>\n  <base href=\"/\">\n"
            "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"{links}</head>\n<body>\n  <app-root></app-root>\n</body>\n</html>\n")


def build_global_styles(global_css: list[str], todos: list[str]) -> str:
    out = ["/* Global styles migrated from index.html <custom-style>/<style> blocks. */"]
    out += [convert_css(c, None, todos, component=False) for c in global_css]
    return "\n".join(o for o in out if o.strip()) + "\n"


def scaffold_project(*, name: str, title: str, components: dict[str, dict], registry: dict, style_modules: dict,
                     index_body: str, head_links: list[str], global_css: list[str], root_tags: list[str],
                     known_mixins: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Return ({path: content}, todos) for every non-component project file."""
    todos: list[str] = []
    uses_material = any(c.get("material") for c in components.values())
    uses_router = any(c.get("uses_router") for c in components.values())
    files = {
        "package.json": package_json(name, uses_material),
        "angular.json": angular_json(name, uses_material),
        "tsconfig.json": TSCONFIG,
        "tsconfig.app.json": TSCONFIG_APP,
        ".gitignore": GITIGNORE,
        "src/main.ts": MAIN_TS,
        "src/index.html": build_index_html(title, head_links, uses_material),
        "src/styles.scss": build_global_styles(global_css, todos),
        "src/app/app.config.ts": APP_CONFIG_TS,
        "src/app/app.routes.ts": routes_ts(uses_router),
        "src/app/shared/polymer-compat.ts": POLYMER_COMPAT_TS,
        "src/styles/_iron-flex-layout.scss": IRON_FLEX_SCSS,
    }
    for module_id, module in style_modules.items():
        css = "\n".join(convert_css(s, known_mixins, todos) for s in module.get("styles", []))
        files[f"src/styles/_{module_id}.scss"] = f"/* Shared styles migrated from Polymer style module '{module_id}'. */\n" + css
    files.update(build_root_app(index_body, root_tags, registry))
    return files, todos
