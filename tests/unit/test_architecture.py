"""Enforces the layering: inner layers never import outer ones."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "preauth"

# package -> packages it must never import
FORBIDDEN = {
    "domain": ("preauth.rules", "preauth.recommendation", "preauth.application", "preauth.infrastructure",
               "preauth.api", "preauth.agent_tools", "sqlalchemy", "fastapi"),
    "rules": ("preauth.recommendation", "preauth.application", "preauth.infrastructure", "preauth.api",
              "preauth.agent_tools", "sqlalchemy", "fastapi"),
    "recommendation": ("preauth.application", "preauth.infrastructure", "preauth.api", "preauth.agent_tools",
                       "sqlalchemy", "fastapi"),
    "application": ("preauth.api", "preauth.agent_tools", "fastapi"),
    "infrastructure": ("preauth.application", "preauth.api", "preauth.agent_tools", "fastapi"),
    "agent_tools": ("preauth.api", "preauth.infrastructure", "fastapi", "sqlalchemy"),
}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_layer_does_not_import_outer_layers(package):
    violations = []
    for file in (SRC / package).rglob("*.py"):
        for name in _imports(file):
            if any(name == f or name.startswith(f + ".") for f in FORBIDDEN[package]):
                violations.append(f"{file.relative_to(SRC)} imports {name}")
    assert not violations, "\n".join(violations)


def test_routes_contain_no_business_logic():
    """Route modules may only reach the application through service calls; no direct DB or domain rules."""
    for file in (SRC / "api" / "routes").glob("*.py"):
        imports = _imports(file)
        assert not any(i.startswith(("sqlalchemy", "preauth.infrastructure", "preauth.rules",
                                     "preauth.recommendation", "preauth.domain.case_state")) for i in imports), file
