from __future__ import annotations

import io
import json
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from aedificatoria import github, history
from aedificatoria.cli import main
from aedificatoria.config import Config


def test_parse_slug():
    assert github.parse_slug("git@github.com:acme/aed.git") == "acme/aed"
    assert github.parse_slug("https://github.com/acme/aed.git") == "acme/aed"
    assert github.parse_slug("ssh://git@github.com/acme/aed") == "acme/aed"
    assert github.parse_slug("https://github.com/acme/aed/") == "acme/aed"
    assert github.parse_slug("not-a-remote") == ""


def test_slug_from_remote(repo: Path):
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:acme/aed.git"],
        check=True,
    )
    assert github.slug_from_remote(repo) == "acme/aed"


def test_extract_tarball(tmp_path: Path):
    source = tmp_path / "acme-aed-sha"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    archive = tmp_path / "tree.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="acme-aed-sha")

    root = github.extract_tarball(archive, tmp_path / "out")
    assert (root / "app.py").read_text(encoding="utf-8") == "value = 1\n"


def test_extract_tarball_rejects_traversal(tmp_path: Path):
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("../evil.py")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))

    with pytest.raises(github.GitHubError):
        github.extract_tarball(archive, tmp_path / "out")


class FakeClient:
    def __init__(self, roots: dict[str, Path]):
        self.roots = roots

    def pr_commits(self, pr: int, limit: int):
        commits = [
            github.PrCommit("a" * 40, "2026-01-01T00:00:00Z", "first", (), 1, 1),
            github.PrCommit("b" * 40, "2026-01-02T00:00:00Z", "second", ("a" * 40,), 1, 2),
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


def test_evaluate_github_with_fake_client(repo: Path, tmp_path: Path):
    points = history.evaluate_github(repo, Config(), 7, 10, FakeClient(_roots(tmp_path)))
    assert [point.subject for point in points] == ["first", "second"]
    assert points[0].regressions == ()
    assert any("public API grew" in item.message for item in points[1].regressions)
    assert points[1].files == 1
    assert points[1].lines == 2


def test_history_github_cli(repo: Path, tmp_path: Path, monkeypatch, capsys):
    roots = _roots(tmp_path)

    def fake_client(slug: str):
        return FakeClient(roots)

    monkeypatch.setattr(github, "GitHubClient", fake_client)
    code = main(
        [
            "history",
            "--github-pr",
            "7",
            "--repo",
            "acme/aed",
            "--path",
            str(repo),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["source"] == {"github_pr": 7, "repo": "acme/aed", "api": "github"}
    assert len(payload["commits"]) == 2
