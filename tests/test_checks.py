from __future__ import annotations

from pathlib import Path

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


def test_pyproject_pytest_config_warns(repo: Path, commit, write):
    write(repo, "pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-q"\n')
    commit(repo)
    write(repo, "pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-q --ignore=tests"\n')
    findings = evaluate(repo, Config())
    assert any(item.rule == "test-config" and item.severity == "warn" for item in findings)
