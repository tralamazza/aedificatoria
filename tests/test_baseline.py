from __future__ import annotations

from pathlib import Path

from aedificatoria import baseline
from aedificatoria.checks import run_checks as run_all_checks
from aedificatoria.cli import main
from aedificatoria.config import Config
from aedificatoria.git import base_worktree, changed_files, resolve_commit
from aedificatoria.model import Context
from aedificatoria.report import verdict


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


def snapshot(repo: Path, config: Config | None = None) -> None:
    cfg = config or Config()
    baseline.write(repo, cfg.baseline_path, baseline.collect(repo, cfg))


def test_baseline_clean_after_snapshot(repo: Path, commit, write):
    write(repo, "app.py", "def add(x: int) -> int:\n    return x + 1\n")
    snapshot(repo)
    commit(repo)

    findings = evaluate(repo, Config())
    assert not any(item.rule == "baseline" for item in findings)
    assert verdict(findings) == "allow"


def test_baseline_detects_signature_change(repo: Path, commit, write):
    write(repo, "service.py", "def handle(x: int) -> int:\n    return x\n")
    snapshot(repo)
    commit(repo)
    write(repo, "service.py", "def handle(x: int, y: int) -> int:\n    return x + y\n")

    findings = evaluate(repo, Config())
    assert any(
        item.rule == "baseline" and "signature changed" in item.message for item in findings
    )


def test_baseline_ignores_private_api_and_examples(repo: Path, commit, write):
    write(repo, "src/pkg/__init__.py", "")
    write(repo, "src/pkg/_internal.py", "def hidden(x: int) -> int:\n    return x\n")
    write(repo, "examples/app.py", "def sample(x: int) -> int:\n    return x\n")
    snapshot(repo)
    commit(repo)
    write(repo, "src/pkg/_internal.py", "def hidden(x: int, y: int) -> int:\n    return x + y\n")
    write(repo, "examples/app.py", "def sample(x: int) -> int:\n" + "".join(
        f"    if x == {index}:\n        return {index}\n" for index in range(8)
    ) + "    return -1\n")

    findings = evaluate(repo, Config(max_complexity=3, complexity_delta=0))
    assert not any(item.rule in {"public-api", "complexity"} for item in findings)
    assert not any(item.rule == "baseline" for item in findings)


def test_baseline_resolves_private_reexport(repo: Path, commit, write):
    write(repo, "src/pkg/_internal.py", "def handle(x: int) -> int:\n    return x\n")
    write(repo, "src/pkg/__init__.py", "from ._internal import handle\n")
    snapshot(repo)
    commit(repo)
    write(
        repo,
        "src/pkg/_internal.py",
        "def handle(x: int, y: int) -> int:\n    return x + y\n",
    )

    findings = evaluate(repo, Config())
    assert any(
        item.rule == "baseline"
        and item.path == "src/pkg/__init__.py"
        and "signature changed" in item.message
        for item in findings
    )


def test_baseline_detects_each_case_shrink(repo: Path, commit, write):
    source = """
test.each([
    [1, 2],
    [2, 3],
])("adds", (a, b) => { expect(a).toBe(b); });
"""
    write(repo, "package.json", '{"name": "app", "private": true}\n')
    write(repo, "src/math.test.ts", source)
    snapshot(repo)
    commit(repo)
    write(repo, "src/math.test.ts", source.replace("    [2, 3],\n", ""))

    findings = evaluate(repo, Config())
    assert any(
        item.rule == "baseline" and "parameterized cases" in item.message
        for item in findings
    )


def test_baseline_blocks_small_complexity_growth(repo: Path, commit, write):
    write(repo, "risk.py", "def score(x: int) -> int:\n    return x\n")
    snapshot(repo)
    commit(repo)
    body = "def score(x: int) -> int:\n" + "".join(
        f"    if x == {index}:\n        return {index}\n" for index in range(4)
    ) + "    return -1\n"
    write(repo, "risk.py", body)

    findings = evaluate(repo, Config(max_complexity=15, complexity_delta=10))
    assert any(item.rule == "baseline" and "complexity grew" in item.message for item in findings)
    assert not any(item.rule == "complexity" for item in findings)


def test_baseline_blocks_api_growth(repo: Path, commit, write):
    write(repo, "service.py", "def handle(x: int) -> int:\n    return x\n")
    snapshot(repo)
    commit(repo)
    write(repo, "service.py", "def handle(x: int) -> int:\n    return x\n\ndef extra() -> None:\n    return None\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "baseline" and "public API grew" in item.message for item in findings)
    assert not any(item.rule == "public-api" for item in findings)


def test_baseline_blocks_new_dependency(repo: Path, commit, write):
    write(repo, "main.py", "import os\n")
    snapshot(repo)
    commit(repo)
    write(repo, "main.py", "import os\nimport requests\n")

    findings = evaluate(repo, Config(allow_new_dependencies=("requests",)))
    assert not any(item.rule == "new-dependency" for item in findings)
    assert any(item.rule == "baseline" and "new dependencies" in item.message for item in findings)


def test_baseline_blocks_new_edge(repo: Path, commit, write):
    write(repo, "a.py", "value = 1\n")
    write(repo, "b.py", "value = 2\n")
    snapshot(repo)
    commit(repo)
    write(repo, "a.py", "import b\nvalue = 1\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "baseline" and "new package edges" in item.message for item in findings)
    assert not any(item.rule == "import-cycle" or item.rule == "forbidden-edge" for item in findings)


def test_baseline_blocks_test_removal(repo: Path, commit, write):
    write(
        repo,
        "tests/test_math.py",
        "def test_a():\n    assert 1 == 1\n    assert 2 == 2\n\ndef test_b():\n    assert 3 == 3\n",
    )
    snapshot(repo)
    commit(repo)
    write(repo, "tests/test_math.py", "def test_a():\n    assert 1 == 1\n    assert 2 == 2\n")

    findings = evaluate(repo, Config(block_removed_tests=False))
    assert any(item.rule == "baseline" and "test `test_b` removed" in item.message for item in findings)


def test_baseline_blocks_assertion_drop_without_per_diff(repo: Path, commit, write):
    write(repo, "tests/test_math.py", "def test_a():\n    assert 1 == 1\n    assert 2 == 2\n")
    snapshot(repo)
    commit(repo)
    write(repo, "tests/test_math.py", "def test_a():\n    assert 1 == 1\n")

    findings = evaluate(repo, Config(block_removed_asserts=False))
    assert any(
        item.rule == "baseline" and "assertions are 1 since baseline, was 2" in item.message
        for item in findings
    )


def test_baseline_file_change_requires_adr(repo: Path, commit, write):
    write(repo, "app.py", "value = 1\n")
    snapshot(repo)
    commit(repo)
    (repo / "aed-baseline.json").write_text(
        (repo / "aed-baseline.json").read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    findings = evaluate(repo, Config())
    assert any(item.rule == "guard-config" and item.severity == "error" for item in findings)


def test_baseline_missing_is_info(repo: Path, commit, write):
    write(repo, "app.py", "value = 1\n")
    commit(repo)

    findings = evaluate(repo, Config())
    assert any(item.rule == "baseline" and item.severity == "info" for item in findings)
    assert verdict(findings) == "allow"


def test_baseline_malformed_is_error(repo: Path, commit, write):
    write(repo, "aed-baseline.json", "{not json\n")
    commit(repo)

    findings = evaluate(repo, Config())
    assert any(item.rule == "baseline" and item.severity == "error" for item in findings)


def test_baseline_growth_caps_allow_configured_growth(repo: Path, commit, write):
    write(repo, "app.py", "def one() -> None:\n    return None\n")
    snapshot(repo)
    commit(repo)
    write(repo, "app.py", "def one() -> None:\n    return None\n\ndef two() -> None:\n    return None\n")

    findings = evaluate(repo, Config(max_baseline_complexity_growth=1, max_baseline_api_growth=1))
    assert not any(item.rule == "baseline" for item in findings)


def test_baseline_cli_writes_deterministically(repo: Path, commit, write):
    write(repo, "app.py", "def add(x: int) -> int:\n    return x + 1\n")
    commit(repo)

    assert main(["baseline", "--path", str(repo)]) == 0
    first = (repo / "aed-baseline.json").read_text(encoding="utf-8")
    assert main(["baseline", "--path", str(repo)]) == 0
    assert (repo / "aed-baseline.json").read_text(encoding="utf-8") == first
