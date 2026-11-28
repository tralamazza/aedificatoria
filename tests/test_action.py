from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aedificatoria import _action
from aedificatoria.model import ChangedFile, Finding
from aedificatoria.report import render_annotations, render_summary


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _init_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return _git(path, "symbolic-ref", "--short", "HEAD")


def test_event_base_prefers_merge_group_and_pr():
    assert (
        _action.event_base(
            {"merge_group": {"base_sha": "merge"}, "before": "push"}
        )
        == "merge"
    )
    assert (
        _action.event_base(
            {"pull_request": {"base": {"sha": "pr"}}, "before": "push"}
        )
        == "pr"
    )
    assert _action.event_base({"before": "push"}) == "push"
    assert _action.event_base({}) == ""


def test_resolve_base_explicit_commit(repo: Path, commit, write):
    write(repo, "a.txt", "x\n")
    commit(repo)
    head = _git(repo, "rev-parse", "HEAD")
    assert _action.resolve_base(repo, {}, default_branch="main", explicit="HEAD") == head


def test_resolve_base_fetches_event_commit(tmp_path: Path):
    source = tmp_path / "source"
    _init_repo(source)
    (source / "a.txt").write_text("one\n", encoding="utf-8")
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "one")
    first = _git(source, "rev-parse", "HEAD")
    (source / "a.txt").write_text("two\n", encoding="utf-8")
    _git(source, "commit", "-qam", "two")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", f"file://{source}", str(clone)],
        check=True,
    )
    unresolved = subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", f"{first}^{{commit}}"],
        capture_output=True,
    )
    assert unresolved.returncode != 0

    assert _action.resolve_base(clone, {"before": first}, default_branch="main") == first
    subprocess.run(
        ["git", "-C", str(clone), "cat-file", "-e", f"{first}^{{commit}}"],
        check=True,
    )


def test_resolve_base_explicit_branch_fetches(tmp_path: Path):
    source = tmp_path / "source"
    branch = _init_repo(source)
    (source / "a.txt").write_text("one\n", encoding="utf-8")
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "one")
    head = _git(source, "rev-parse", "HEAD")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", f"file://{source}", str(clone)],
        check=True,
    )
    _git(clone, "checkout", "--detach")
    _git(clone, "branch", "-D", branch)

    assert (
        _action.resolve_base(clone, {}, default_branch=branch, explicit=branch) == head
    )
    assert (
        _action.resolve_base(
            clone, {}, default_branch=branch, explicit=f"origin/{branch}"
        )
        == head
    )


def test_resolve_base_merge_base_fallback(tmp_path: Path):
    source = tmp_path / "source"
    branch = _init_repo(source)
    (source / "a.txt").write_text("one\n", encoding="utf-8")
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "one")
    first = _git(source, "rev-parse", "HEAD")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", f"file://{source}", str(clone)],
        check=True,
    )
    _git(clone, "config", "user.email", "test@example.com")
    _git(clone, "config", "user.name", "Test")
    (clone / "b.txt").write_text("b\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-qm", "local")

    assert _action.resolve_base(clone, {}, default_branch=branch) == first


def test_resolve_base_without_remote_raises(repo: Path):
    with pytest.raises(_action.ActionError):
        _action.resolve_base(repo, {}, default_branch="main")


def test_resolve_base_rejects_option_like_input(repo: Path):
    with pytest.raises(_action.ActionError):
        _action.resolve_base(repo, {}, default_branch="main", explicit="--upload-pack=x")


def test_outputs_writes_step_outputs(tmp_path: Path):
    report = tmp_path / "aed.json"
    report.write_text(
        json.dumps(
            {
                "verdict": "review",
                "base": "abc123",
                "counts": {"error": 0, "warn": 2, "info": 1},
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "github-output"
    assert (
        _action.main(
            [
                "outputs",
                "--json",
                str(report),
                "--exit-code",
                "1",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    lines = output.read_text(encoding="utf-8").splitlines()
    assert "verdict=review" in lines
    assert "base=abc123" in lines
    assert "warnings=2" in lines
    assert "exit-code=1" in lines
    assert f"json={report.resolve()}" in lines


def test_outputs_missing_report_returns_two(tmp_path: Path):
    code = _action.main(
        [
            "outputs",
            "--json",
            str(tmp_path / "missing.json"),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert code == 2


def test_annotations_escape_and_rank():
    findings = [
        Finding("one-way-door", "error", "bad\nline", "migrations/x.sql", 3),
        Finding("complexity", "warn", "a,b: c", "src/x.py", 10),
        Finding("baseline", "info", "note", "aed-baseline.json"),
    ]
    text = render_annotations(findings)
    assert (
        "::error title=aed%3A one-way-door,file=migrations/x.sql,line=3::bad%0Aline"
        in text
    )
    assert "::warning title=aed%3A complexity,file=src/x.py,line=10::a,b: c" in text
    assert "::notice title=aed%3A baseline,file=aed-baseline.json::note" in text


def test_annotations_cap_errors():
    findings = [
        Finding("complexity", "error", f"e{index}", "src/a.py", index)
        for index in range(12)
    ]
    text = render_annotations(findings)
    assert text.count("::error ") == 10
    assert "2 additional finding(s) omitted" in text


def test_summary_has_verdict_remedy_and_escaping():
    findings = [
        Finding("one-way-door", "error", "changed | protected", "migrations/1.sql", 4)
    ]
    changed = [ChangedFile("M", "migrations/1.sql", added=1)]
    text = render_summary(findings, "a" * 40, changed)
    assert text.startswith("## Aedificatoria: BLOCK")
    assert "| ERROR | `one-way-door` | migrations/1.sql:4 | changed \\| protected |" in text
    assert "accepted ADR" in text


def test_summary_clean():
    text = render_summary([], "b" * 40, [])
    assert text.startswith("## Aedificatoria: ALLOW")
    assert "No findings." in text
