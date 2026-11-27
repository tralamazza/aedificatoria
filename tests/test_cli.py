from __future__ import annotations

import json
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


def test_cli_check_allows_clean_tree(repo: Path, commit, write, capsys):
    write(repo, "README.md", "base\n")
    commit(repo)

    code = main(["check", "--path", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["verdict"] == "allow"
    assert payload["changed"]["files"] == 0
