from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from aedificatoria import forge, gitlab, history
from aedificatoria.cli import main
from aedificatoria.config import Config


def test_parse_project_path():
    assert gitlab.parse_project_path("git@gitlab.com:group/sub/repo.git") == "group/sub/repo"
    assert gitlab.parse_project_path("https://gitlab.com/group/sub/repo.git") == "group/sub/repo"
    assert gitlab.parse_project_path("ssh://git@gitlab.com/group/repo.git") == "group/repo"
    assert gitlab.parse_project_path("https://gitlab.com/group/repo/") == "group/repo"
    assert gitlab.parse_project_path("not-a-remote") == "not-a-remote"


def test_project_path_from_remote(repo: Path):
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "git@gitlab.com:group/sub/repo.git",
        ],
        check=True,
    )
    assert gitlab.project_path_from_remote(repo) == "group/sub/repo"


def test_glab_client_paginates_and_flattens(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = b'[[{"id":"a"}],[{"id":"b"}]]'
        stderr = b""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(gitlab.subprocess, "run", fake_run)
    client = gitlab.GitLabClient("group/repo")
    payload = client._json("projects/group%2Frepo/merge_requests/1/commits", paginate=True)
    assert payload == [{"id": "a"}, {"id": "b"}]
    assert "--paginate" in calls[0]
    assert client.encoded == "group%2Frepo"


def test_glab_client_requires_glab():
    with pytest.raises(forge.ForgeError):
        gitlab.GitLabClient("group/repo", glab="/nonexistent/glab")._json("projects/group%2Frepo")


class FakeClient:
    def __init__(self, roots: dict[str, Path]):
        self.roots = roots

    def pr_commits(self, mr: int, limit: int):
        commits = [
            forge.ForgeCommit("a" * 40, "2026-01-01T00:00:00Z", "first", (), 1, 1),
            forge.ForgeCommit("b" * 40, "2026-01-02T00:00:00Z", "second", ("a" * 40,), 1, 2),
        ]
        return commits[-limit:]

    @contextmanager
    def tarball(self, sha: str):
        yield self.roots[sha]


def _roots(tmp_path: Path) -> dict[str, Path]:
    first = tmp_path / "first"
    first.mkdir()
    (first / "app.py").write_text("def one() -> int:\n    return 1\n", encoding="utf-8")
    second = tmp_path / "second"
    second.mkdir()
    (second / "app.py").write_text(
        "def one() -> int:\n    return 1\n\ndef two() -> int:\n    return 2\n",
        encoding="utf-8",
    )
    return {"a" * 40: first, "b" * 40: second}


def test_evaluate_gitlab_with_fake_client(repo: Path, tmp_path: Path):
    points = history.evaluate_forge(repo, Config(), 7, 10, FakeClient(_roots(tmp_path)))
    assert [point.subject for point in points] == ["first", "second"]
    assert any("public API grew" in item.message for item in points[1].regressions)


def test_history_gitlab_cli(repo: Path, tmp_path: Path, monkeypatch, capsys):
    roots = _roots(tmp_path)

    def fake_client(project: str, cwd=None):
        return FakeClient(roots)

    monkeypatch.setattr(gitlab, "GitLabClient", fake_client)
    code = main(
        [
            "history",
            "--gitlab-mr",
            "7",
            "--repo",
            "group/sub/repo",
            "--path",
            str(repo),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["source"] == {"gitlab_mr": 7, "repo": "group/sub/repo", "api": "gitlab"}
    assert len(payload["commits"]) == 2
