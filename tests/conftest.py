"""Shared test fixtures for CKG tests."""

import json
import os
import subprocess
import sys

import pytest
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_PROJECT = FIXTURES_DIR / "sample_project"
SAMPLE_PKG = SAMPLE_PROJECT / "sample_pkg"


@pytest.fixture(scope="session", autouse=True)
def _ensure_fixture_git():
    """Reconstruct the sample-project git history if it is missing.

    The fixture's nested ``.git`` is intentionally not committed to the
    repository (embedded git repos break packaging and publishing). When
    tests run from a fresh clone, this reconstructs the exact history the
    co-edit tests depend on:

        d2f789f initial: core, helpers, db modules
        d6e7c27 fix: update core and db together (co-edit)
        352b7a6 refactor: helpers + db updated together
    """
    git_dir = SAMPLE_PROJECT / ".git"
    if git_dir.exists():
        return

    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": "CKG Tests",
        "GIT_AUTHOR_EMAIL": "tests@ckg.local",
        "GIT_COMMITTER_NAME": "CKG Tests",
        "GIT_COMMITTER_EMAIL": "tests@ckg.local",
    })

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(SAMPLE_PROJECT), *args],
            check=True, capture_output=True, env=env,
        )

    # 1) initial commit — the whole tree
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "initial: core, helpers, db modules")

    # 2) co-edit pair #1 — core.py + utils/db.py
    _append(git, "sample_pkg/core.py", "# changed with db.py")
    _append(git, "sample_pkg/utils/db.py", "# updated with core.py")
    git("commit", "-q", "-am", "fix: update core and db together (co-edit)")

    # 3) co-edit pair #2 — utils/db.py + utils/helpers.py
    _append(git, "sample_pkg/utils/db.py", "# touched with helpers.py")
    _append(git, "sample_pkg/utils/helpers.py", "# changed with db.py")
    git("commit", "-q", "-am", "refactor: helpers + db updated together")


def _append(git, rel: str, line: str) -> None:
    """Append a comment line to a fixture file and stage it."""
    target = SAMPLE_PROJECT / rel
    with target.open("a", encoding="utf-8") as fh:
        fh.write(f"\n{line}\n")
    git("add", rel)


@pytest.fixture(scope="module")
def sample_project_path() -> Path:
    """Path to the sample project fixture (git repo with Python code)."""
    assert SAMPLE_PROJECT.is_dir(), f"Fixture not found: {SAMPLE_PROJECT}"
    assert (SAMPLE_PROJECT / ".git").is_dir(), "Fixture not a git repo"
    return SAMPLE_PROJECT


@pytest.fixture(scope="module")
def sample_pkg_path() -> Path:
    """Path to the Python package within the fixture."""
    p = SAMPLE_PKG
    assert p.is_dir(), f"Package not found: {p}"
    return p


@pytest.fixture(scope="module")
def sample_graph(sample_pkg_path) -> dict:
    """Parse the sample project into a code graph (module-scoped, cached)."""
    from ckg.graph.parser import parse_tree
    return parse_tree(sample_pkg_path, pkg_root="sample_pkg")


@pytest.fixture(scope="module")
def sample_graph_enriched(sample_pkg_path, sample_graph) -> dict:
    """Parsed graph with enriched node text."""
    from ckg.graph.builder import enrich_node_text
    nodes = list(sample_graph["nodes"].values())
    for node in nodes:
        enrich_node_text(sample_pkg_path, node, "sample_pkg")
    return {"nodes": {n["id"]: n for n in nodes}, "edges": sample_graph["edges"],
            "meta": sample_graph["meta"]}


@pytest.fixture
def sample_cg(sample_graph_enriched):
    """CodeGraph object built from the enriched sample graph."""
    from ckg.graph.builder import CodeGraph
    return CodeGraph.from_dict(sample_graph_enriched)


def load_graph_json(path: Path) -> dict:
    """Load a cached graph JSON (if it exists)."""
    if path.exists():
        return json.loads(path.read_text())
    return {"nodes": {}, "edges": []}
