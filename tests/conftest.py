"""Shared test fixtures for CKG tests."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SAMPLE_PROJECT = Path(__file__).parent / "fixtures" / "sample_project"

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "CKG Tests",
    "GIT_AUTHOR_EMAIL": "tests@ckg.local",
    "GIT_COMMITTER_NAME": "CKG Tests",
    "GIT_COMMITTER_EMAIL": "tests@ckg.local",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` with a fixed identity and no user configuration."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=GIT_ENV,
    ).stdout


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def write_files(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


@pytest.fixture(scope="session")
def sample_project_path(tmp_path_factory) -> Path:
    """A git repository holding a copy of the sample project.

    History (the tracked fixture files themselves are never modified):

        initial: core, helpers, db modules
        fix: update core and db together        → co-edit core.py + utils/db.py
        refactor: helpers + db updated together → co-edit utils/db.py + utils/helpers.py
    """
    repo = tmp_path_factory.mktemp("fixture") / "sample_project"
    shutil.copytree(SAMPLE_PROJECT, repo)
    git(repo, "init", "-q")
    commit_all(repo, "initial: core, helpers, db modules")

    def touch(rel: str) -> None:
        with (repo / "sample_pkg" / rel).open("a", encoding="utf-8") as fh:
            fh.write("\n# edited\n")

    touch("core.py")
    touch("utils/db.py")
    commit_all(repo, "fix: update core and db together")
    touch("utils/db.py")
    touch("utils/helpers.py")
    commit_all(repo, "refactor: helpers + db updated together")
    return repo


@pytest.fixture(scope="session")
def sample_pkg_path(sample_project_path) -> Path:
    """The Python package inside the sample project."""
    return sample_project_path / "sample_pkg"


@pytest.fixture(scope="session")
def sample_graph(sample_pkg_path) -> dict:
    """The sample package as a graph dict, as ``ckg build`` produces it."""
    from ckg.graph.builder import build_graph

    return build_graph(sample_pkg_path, pkg_root="sample_pkg")


@pytest.fixture
def sample_cg(sample_graph):
    """CodeGraph object for the sample package."""
    from ckg.graph.builder import CodeGraph

    return CodeGraph.from_dict(sample_graph)
