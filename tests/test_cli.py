from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from aedificatoria.cli import main


def test_cli_init_and_adr(repo: Path):
    assert main(["init", "--path", str(repo)]) == 0
    assert (repo / "aed.toml").exists()
    first = list((repo / "docs" / "decisions").glob("0001-*.md"))
    assert len(first) == 1

    assert main(["adr", "new", "Use a queue", "--path", str(repo)]) == 0
    second = list((repo / "docs" / "decisions").glob("0002-*.md"))
    assert len(second) == 1
    assert "## Decision" in second[0].read_text(encoding="utf-8")

    assert main(["rules", "--path", str(repo)]) == 0


def test_cli_check_json_blocks_one_way_door(repo: Path, commit, write, capsys):
    write(repo, "README.md", "base\n")
    commit(repo)
    write(repo, "migrations/0001_init.sql", "create table t;\n")

    code = main(["check", "--path", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2
    assert payload["verdict"] == "block"
    assert any(item["rule"] == "one-way-door" for item in payload["findings"])


def test_cli_staged_checks_index_not_worktree(repo: Path, commit, write, capsys):
    strong = "def test_one():\n    assert 1 == 1\n    assert 2 == 2\n"
    weak = "def test_one():\n    assert 1 == 1\n"
    write(repo, "tests/test_app.py", strong)
    commit(repo)

    write(repo, "tests/test_app.py", weak)
    subprocess.run(["git", "-C", str(repo), "add", "tests/test_app.py"], check=True)
    write(repo, "tests/test_app.py", strong)

    assert main(["check", "--path", str(repo), "--json"]) == 0
    capsys.readouterr()
    assert main(["check", "--path", str(repo), "--staged", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "block"
    assert any(item["rule"] == "test-tampering" for item in payload["findings"])


def test_cli_staged_checks_staged_config(repo: Path, commit, write, capsys):
    strong = "[aed]\nblock_removed_tests = true\n"
    weak = "[aed]\nblock_removed_tests = false\n"
    write(repo, "aed.toml", strong)
    commit(repo)

    write(repo, "aed.toml", weak)
    subprocess.run(["git", "-C", str(repo), "add", "aed.toml"], check=True)
    write(repo, "aed.toml", strong)

    assert main(["check", "--path", str(repo), "--json"]) == 0
    capsys.readouterr()
    assert main(["check", "--path", str(repo), "--staged", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert any(item["rule"] == "guard-config" for item in payload["findings"])


def test_cli_staged_honors_hook_index(repo: Path, commit, write, capsys, monkeypatch, tmp_path):
    strong = "def test_one():\n    assert 1 == 1\n    assert 2 == 2\n"
    weak = "def test_one():\n    assert 1 == 1\n"
    write(repo, "tests/test_app.py", strong)
    commit(repo)

    alt = tmp_path / "next-index"
    env = {**os.environ, "GIT_INDEX_FILE": str(alt)}
    subprocess.run(["git", "-C", str(repo), "read-tree", "HEAD"], env=env, check=True)
    write(repo, "tests/test_app.py", weak)
    subprocess.run(
        ["git", "-C", str(repo), "add", "tests/test_app.py"], env=env, check=True
    )
    write(repo, "tests/test_app.py", strong)
    monkeypatch.setenv("GIT_INDEX_FILE", str(alt))

    assert main(["check", "--path", str(repo), "--staged", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert any(item["rule"] == "test-tampering" for item in payload["findings"])


def test_cli_check_allows_clean_tree(repo: Path, commit, write, capsys):
    write(repo, "README.md", "base\n")
    commit(repo)

    code = main(["check", "--path", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["verdict"] == "allow"
    assert payload["changed"]["files"] == 0


def test_cli_doctor_json(capsys):
    from aedificatoria import doctor

    code = main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    names = {item["name"] for item in payload["tools"]}
    assert {"python", "git", "tree-sitter", "cargo", "gh", "glab"} <= names
    assert payload["required_missing"] == []
    assert doctor.ok(doctor.collect())


def test_cli_doctor_reports_missing_required(monkeypatch, capsys):
    from aedificatoria import doctor

    monkeypatch.setattr(
        doctor,
        "collect",
        lambda: [doctor.ToolStatus("git", "binary", True, False, "", "", False)],
    )
    code = main(["doctor"])
    output = capsys.readouterr().out
    assert code == 1
    assert "[missing] git" in output
    assert "required: missing git" in output


def test_doctor_optional_missing_is_not_a_failure():
    from aedificatoria import doctor

    tools = [doctor.ToolStatus("glab", "binary", False, False, "", "GitLab MR audits", True)]
    assert doctor.ok(tools)
    assert "optional: missing glab" in doctor.render_text(tools)
