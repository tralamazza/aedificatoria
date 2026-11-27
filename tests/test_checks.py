from __future__ import annotations

import subprocess
import warnings
from pathlib import Path

from aedificatoria import pyast
from aedificatoria.checks import run_checks as run_all_checks
from aedificatoria.config import Config, ForbiddenEdge, load_config, matches
from aedificatoria.git import base_worktree, changed_files, resolve_commit
from aedificatoria.model import Context


def evaluate(repo: Path, config: Config | None = None):
    cfg = config or Config()
    commit = resolve_commit(repo, "HEAD")
    changed = changed_files(repo, commit)
    with base_worktree(repo, commit) as base_dir:
        context = Context(
            repo=repo,
            base_ref="HEAD",
            base_commit=commit,
            base_dir=base_dir,
            changed=changed,
            config=cfg,
        )
        return run_all_checks(context)


def rules(findings) -> set[str]:
    return {item.rule for item in findings}


def test_glob_matching():
    assert matches("src/a.py", ("src/**/*.py",))
    assert matches("src/pkg/b.py", ("src/**/*.py",))
    assert not matches("tests/test_a.py", ("src/**/*.py",))
    assert matches("migrations/0001.sql", ("migrations/**",))
    assert matches("src/migrations/0001.sql", ("**/migrations/**",))
    assert matches("foo.proto", ("*.proto",))
    assert matches("src/foo.proto", ("*.proto",))
    assert matches("tests/test_thing.py", ("**/test_*.py",))


def test_tracked_source_files_skips_symlink(repo: Path, commit, write, tmp_path: Path):
    from aedificatoria.git import tracked_source_files

    write(repo, "README.md", "base\n")
    outside = tmp_path / "outside.py"
    outside.write_text("def outside():\n    return 1\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "leak.py").symlink_to(outside)
    commit(repo)

    files = tracked_source_files(repo, Config(), (".py",))
    assert "src/leak.py" not in files


def test_rename_numstat_keeps_churn(repo: Path, commit, write):
    write(repo, "src/old.py", "def f():\n    return 1\n")
    commit(repo)
    (repo / "src" / "lib").mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "mv", "src/old.py", "src/lib/new.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    write(repo, "src/lib/new.py", "def f():\n    return 2\n")

    changed = changed_files(repo, resolve_commit(repo, "HEAD"))
    moved = next(item for item in changed if item.path == "src/lib/new.py")
    assert moved.added + moved.deleted > 0


def test_gitignored_sources_are_not_scanned(repo: Path, commit, write):
    from aedificatoria.config import Config
    from aedificatoria.git import tracked_source_files

    write(repo, ".gitignore", ".tox/\n")
    write(repo, "app.py", "import requests\n")
    write(repo, ".tox/vendor.py", "import tox_only\n")
    commit(repo)

    files = tracked_source_files(repo, Config(), (".py",))
    assert "app.py" in files
    assert ".tox/vendor.py" not in files


def test_parse_text_suppresses_syntax_warnings():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tree = pyast.parse_text("old.py", 'value = "\\s"\n')
    assert tree is not None
    assert not any(issubclass(item.category, SyntaxWarning) for item in caught)


def test_bom_python_is_valid():
    source = "\ufeffdef f(x: int) -> int:\n    return x\n"
    assert pyast.parse_text("bom.py", source) is not None
    assert pyast.syntax_error(source) is None


def test_one_way_door_rename_is_caught(repo: Path, commit, write):
    write(repo, "migrations/0001.sql", "create table t;\n")
    commit(repo)
    (repo / "archive").mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "mv", "migrations/0001.sql", "archive/0001.sql"],
        check=True,
        capture_output=True,
        text=True,
    )

    findings = evaluate(repo, Config())
    assert any(item.rule == "one-way-door" and item.severity == "error" for item in findings)


def test_proposed_adr_does_not_authorize_one_way_door(repo: Path, commit, write):
    write(repo, "README.md", "base\n")
    commit(repo)
    write(repo, "migrations/0001.sql", "create table t;\n")
    write(
        repo,
        "docs/decisions/0002-proposed.md",
        "# 0002. Proposed\n\n- Status: proposed\n\n## Decision\n\nLater.\n",
    )

    findings = evaluate(repo, Config())
    assert any(item.rule == "one-way-door" and item.severity == "error" for item in findings)


def test_one_way_door_requires_adr(repo: Path, commit, write):
    write(repo, "README.md", "base\n")
    commit(repo)
    write(repo, "migrations/0001_init.sql", "create table t;\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "one-way-door" and item.severity == "error" for item in findings)

    write(
        repo,
        "docs/decisions/0002-add-migration.md",
        "# 0002. Add migration\n\n- Status: accepted\n\n## Decision\n\nAdd the table.\n",
    )
    findings = evaluate(repo, Config())
    assert not any(item.rule == "one-way-door" and item.severity == "error" for item in findings)


def test_test_assertion_removal(repo: Path, commit, write):
    write(
        repo,
        "tests/test_math.py",
        "def test_a():\n    assert 1 == 1\n    assert 2 == 2\n\ndef test_b():\n    assert True\n",
    )
    commit(repo)
    write(repo, "tests/test_math.py", "def test_a():\n    assert 1 == 1\n\ndef test_b():\n    assert True\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "test-tampering" and "assertions" in item.message for item in findings)


def test_test_removal_and_skip(repo: Path, commit, write):
    base = "def test_a():\n    assert 1 == 1\n\ndef test_b():\n    assert True\n"
    write(repo, "tests/test_math.py", base)
    commit(repo)

    write(repo, "tests/test_math.py", "def test_a():\n    assert 1 == 1\n")
    findings = evaluate(repo, Config())
    assert any(item.rule == "test-tampering" and "removed test" in item.message for item in findings)

    write(
        repo,
        "tests/test_math.py",
        "import pytest\n\ndef test_a():\n    pytest.skip('later')\n    assert 1 == 1\n    assert 2 == 2\n\ndef test_b():\n    assert True\n",
    )
    findings = evaluate(repo, Config())
    assert any(item.rule == "test-tampering" and "skipped" in item.message for item in findings)


def test_import_cycle_and_forbidden_edge(repo: Path, commit, write):
    write(repo, "a.py", "import b\n")
    write(repo, "b.py", "value = 1\n")
    commit(repo)
    write(repo, "b.py", "import a\nvalue = 1\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "import-cycle" for item in findings)

    write(repo, "api.py", "value = 1\n")
    write(repo, "db.py", "value = 1\n")
    commit(repo, "layers")
    write(repo, "api.py", "import db\nvalue = 1\n")
    config = Config(forbidden_edges=(ForbiddenEdge("api", "db", "API must use the service layer"),))
    findings = evaluate(repo, config)
    assert any(item.rule == "forbidden-edge" and "api" in item.message for item in findings)


def test_complexity_ratchet(repo: Path, commit, write):
    write(repo, "risk.py", "def score(x):\n    return x\n")
    commit(repo)
    body = "def score(x):\n" + "".join(
        f"    if x == {index}:\n        return {index}\n" for index in range(12)
    ) + "    return -1\n"
    write(repo, "risk.py", body)

    findings = evaluate(repo, Config(max_complexity=10, complexity_delta=5))
    assert any(item.rule == "complexity" and "score" in item.message for item in findings)


def test_python_init_reexports_resolve(repo: Path, write):
    write(repo, "src/pkg/_internal.py", "def handle(x: int) -> int:\n    return x\n")
    write(
        repo,
        "src/pkg/__init__.py",
        "from ._internal import handle as run\n__all__ = [\"run\"]\n",
    )

    surface = pyast._resolved_public_api(repo, "src/pkg/__init__.py", Config())
    assert surface["run"] == "(x:int)->int"


def test_public_api_resolves_private_reexport(repo: Path, commit, write):
    write(repo, "src/pkg/_internal.py", "def handle(x: int) -> int:\n    return x\n")
    write(repo, "src/pkg/__init__.py", "from ._internal import handle\n")
    commit(repo)
    write(
        repo,
        "src/pkg/_internal.py",
        "def handle(x: int, y: int) -> int:\n    return x + y\n",
    )

    findings = evaluate(repo, Config())
    assert any(
        item.rule == "public-api"
        and item.path == "src/pkg/__init__.py"
        and "signature changes" in item.evidence
        for item in findings
    )


def test_complexity_existing_over_limit_unchanged_is_not_flagged(repo: Path, commit, write):
    body = "def score(x):\n" + "".join(
        f"    if x == {index}:\n        return {index}\n" for index in range(8)
    ) + "    return -1\n"
    write(repo, "risk.py", body)
    commit(repo)
    write(repo, "risk.py", body + "\n# no complexity change\n")

    findings = evaluate(repo, Config(max_complexity=5, complexity_delta=1))
    assert not any(item.rule == "complexity" and "score" in item.message for item in findings)


def test_public_api_break_requires_adr(repo: Path, commit, write):
    write(repo, "service.py", "def handle(x: int) -> int:\n    return x\n")
    commit(repo)
    write(repo, "service.py", "def handle(x: int, y: int) -> int:\n    return x + y\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "public-api" and item.severity == "error" for item in findings)

    write(
        repo,
        "docs/decisions/0002-handle-signature.md",
        "# 0002. Handle signature\n\n- Status: accepted\n\n## Decision\n\nRequire y.\n",
    )
    findings = evaluate(repo, Config())
    assert not any(item.rule == "public-api" and item.severity == "error" for item in findings)


def test_new_dependency_allowlist(repo: Path, commit, write):
    write(repo, "main.py", "import os\n")
    commit(repo)
    write(repo, "main.py", "import os\nimport requests\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "new-dependency" and "requests" in item.message for item in findings)

    findings = evaluate(repo, Config(allow_new_dependencies=("requests",)))
    assert not any(item.rule == "new-dependency" for item in findings)


def test_change_amplification_and_adr_justification(repo: Path, commit, write):
    write(repo, "README.md", "base\n")
    commit(repo)
    for index in range(30):
        write(repo, f"module_{index}.py", "value = 1\n")

    config = Config(max_changed_files=25, max_changed_lines=10_000)
    findings = evaluate(repo, config)
    assert any(item.rule == "change-amplification" and item.severity == "error" for item in findings)

    write(
        repo,
        "docs/decisions/0002-big-refactor.md",
        "# 0002. Big refactor\n\n- Status: accepted\n\n## Decision\n\nSplit the modules.\n",
    )
    findings = evaluate(repo, config)
    assert any(item.rule == "change-amplification" and item.severity == "warn" for item in findings)
    assert not any(item.rule == "change-amplification" and item.severity == "error" for item in findings)


def test_adr_quality_and_test_config(repo: Path, commit, write):
    write(repo, "conftest.py", "# base\n")
    commit(repo)
    write(repo, "conftest.py", "# changed\n")
    write(repo, "docs/decisions/0002-empty.md", "# 0002. Empty\n\nNo status yet.\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "adr-quality" for item in findings)
    assert any(item.rule == "test-config" and item.severity == "warn" for item in findings)


def test_report_has_stable_rules(repo: Path, commit, write):
    write(repo, "README.md", "base\n")
    commit(repo)
    write(repo, "migrations/0001_init.sql", "create table t;\n")
    findings = evaluate(repo, Config())
    assert {"one-way-door"} <= rules(findings)


def test_load_config_thresholds(tmp_path: Path):
    (tmp_path / "aed.toml").write_text(
        '[aed]\nbase = "HEAD"\n[thresholds]\nmax_changed_files = 3\nmax_changed_lines = 40\nmax_complexity = 7\ncomplexity_delta = 2\n',
        encoding="utf-8",
    )
    from aedificatoria.config import load_config

    config = load_config(tmp_path)
    assert config.max_changed_files == 3
    assert config.max_changed_lines == 40
    assert config.max_complexity == 7
    assert config.complexity_delta == 2


def test_guard_config_change_requires_adr(repo: Path, commit, write):
    write(repo, "aed.toml", "[aed]\nblock_removed_tests = true\n")
    write(repo, "tests/test_math.py", "def test_a():\n    assert 1 == 1\n")
    commit(repo)

    write(repo, "aed.toml", "[aed]\nblock_removed_tests = false\n")
    (repo / "tests" / "test_math.py").unlink()
    findings = evaluate(repo, load_config(repo))
    assert any(item.rule == "guard-config" and item.severity == "error" for item in findings)
    assert not any(item.rule == "test-tampering" for item in findings)

    write(
        repo,
        "docs/decisions/0002-disable-test-guard.md",
        "# 0002. Disable test guard\n\n- Status: accepted\n\n## Decision\n\nDisable for one migration.\n",
    )
    findings = evaluate(repo, load_config(repo))
    assert any(item.rule == "guard-config" and item.severity == "warn" for item in findings)


def test_guard_config_allowlist_bypass_requires_adr(repo: Path, commit, write):
    write(repo, "aed.toml", "[aed]\n")
    write(repo, "main.py", "import os\n")
    commit(repo)

    write(repo, "aed.toml", '[aed]\nallow_new_dependencies = ["requests"]\n')
    write(repo, "main.py", "import os\nimport requests\n")
    findings = evaluate(repo, load_config(repo))
    assert not any(item.rule == "new-dependency" for item in findings)
    assert any(item.rule == "guard-config" and item.severity == "error" for item in findings)


def test_assertion_movement_between_tests_is_blocked(repo: Path, commit, write):
    base = "def test_a():\n    assert 1 == 1\n    assert 2 == 2\n\ndef test_b():\n    assert 3 == 3\n"
    head = "def test_a():\n    assert 1 == 1\n\ndef test_b():\n    assert 3 == 3\n    assert 4 == 4\n"
    write(repo, "tests/test_math.py", base)
    commit(repo)
    write(repo, "tests/test_math.py", head)

    findings = evaluate(repo, Config())
    messages = [item.message for item in findings if item.rule == "test-tampering"]
    assert any("test `test_a` assertions decreased from 2 to 1" in message for message in messages)
    assert not any(message.startswith("test assertions decreased") for message in messages)


def test_check_ignores_hook_git_environment(repo: Path, commit, write, monkeypatch):
    write(repo, "README.md", "base\n")
    commit(repo)
    write(repo, "migrations/0001_init.sql", "create table t;\n")
    monkeypatch.setenv("GIT_INDEX_FILE", ".git/index")
    monkeypatch.setenv("GIT_DIR", ".git")

    findings = evaluate(repo, Config())
    assert any(item.rule == "one-way-door" for item in findings)


def test_pyproject_non_pytest_change_does_not_warn(repo: Path, commit, write):
    base = '[project]\nname = "app"\n\n[tool.pytest.ini_options]\naddopts = "-q"\n'
    write(repo, "pyproject.toml", base)
    commit(repo)
    write(repo, "pyproject.toml", '[project]\nname = "app"\nlicense = {text = "MIT"}\n\n[tool.pytest.ini_options]\naddopts = "-q"\n')

    findings = evaluate(repo, Config())
    assert not any(item.rule == "test-config" for item in findings)


def test_pyproject_pytest_config_warns(repo: Path, commit, write):
    write(repo, "pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-q"\n')
    commit(repo)
    write(repo, "pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-q --ignore=tests"\n')
    findings = evaluate(repo, Config())
    assert any(item.rule == "test-config" and item.severity == "warn" for item in findings)


def test_syntax_error_on_new_python_failure(repo: Path, commit, write):
    write(repo, "mod.py", "def score(x):\n    return x\n")
    commit(repo)
    write(repo, "mod.py", "def score(x):\n    return (x\n")

    findings = evaluate(repo, Config())
    syntax = [item for item in findings if item.rule == "syntax-error"]
    assert syntax
    assert syntax[0].severity == "error"
    assert syntax[0].path == "mod.py"
    assert syntax[0].line == 2


def test_syntax_error_ignores_pre_existing_failure(repo: Path, commit, write):
    write(repo, "mod.py", "def score(x):\n    return (x\n")
    commit(repo)
    write(repo, "mod.py", "def score(x):\n    return (x + 1\n")

    findings = evaluate(repo, Config())
    assert not any(item.rule == "syntax-error" for item in findings)


def test_syntax_error_ignores_empty_python_file(repo: Path, commit, write):
    write(repo, "mod.py", "def score(x):\n    return x\n")
    commit(repo)
    write(repo, "mod.py", "")
    assert pyast.parse_text("mod.py", "") is not None

    findings = evaluate(repo, Config())
    assert not any(item.rule == "syntax-error" for item in findings)


def test_syntax_error_on_new_rust_failure(repo: Path, commit, write):
    write(repo, "src/lib.rs", "pub fn value() {}\n")
    commit(repo)
    write(repo, "src/lib.rs", "pub fn value( {\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "syntax-error" and item.path == "src/lib.rs" for item in findings)


def test_syntax_error_on_new_javascript_failure(repo: Path, commit, write):
    write(repo, "web/app.js", "const value = 1;\n")
    commit(repo)
    write(repo, "web/app.js", "const value = ;\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "syntax-error" and item.path == "web/app.js" for item in findings)


def test_syntax_error_on_new_typescript_failure(repo: Path, commit, write):
    write(repo, "web/app.ts", "const value: number = 1;\n")
    commit(repo)
    write(repo, "web/app.ts", "const value: number = ;\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "syntax-error" and item.path == "web/app.ts" for item in findings)


def test_syntax_error_on_new_cpp_failure(repo: Path, commit, write):
    write(repo, "src/main.cpp", "int main() { return 0; }\n")
    commit(repo)
    write(repo, "src/main.cpp", "int main( { return 0; }\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "syntax-error" and item.path == "src/main.cpp" for item in findings)


def test_python_split_reexports_resolve(tmp_path: Path):
    root = tmp_path / "root"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "_sub.py").write_text(
        "def a():\n    return 1\n\ndef b():\n    return 2\n", encoding="utf-8"
    )
    (root / "pkg" / "__init__.py").write_text(
        "from ._sub import a\nfrom ._sub import b\n", encoding="utf-8"
    )
    surface = pyast._resolved_public_api(root, "pkg/__init__.py", Config())
    assert {"a", "b"} <= set(surface)


def test_metric_excluded_python_imports_are_ignored(tmp_path: Path):
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "docs" / "conf.py").write_text("import sphinx\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("import os\n", encoding="utf-8")
    config = Config()
    assert "sphinx" not in pyast.project_imports(root, config)
    edges, packages = pyast.package_edges(root, config)
    assert "docs" not in packages
    assert not any("docs" in edge for edge in edges)
