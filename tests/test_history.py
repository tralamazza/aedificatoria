from __future__ import annotations

import json
from pathlib import Path

from aedificatoria import history
from aedificatoria.cli import main
from aedificatoria.config import Config


def test_history_detects_complexity_regression(repo: Path, commit, write):
    write(repo, "risk.py", "def score(x: int) -> int:\n    return x\n")
    commit(repo, "base")
    write(
        repo,
        "risk.py",
        "def score(x: int) -> int:\n    if x < 0:\n        return 0\n    if x > 10:\n        return 10\n    return x\n",
    )
    commit(repo, "grow complexity")

    points = history.evaluate(repo, Config(), limit=5)
    assert len(points) == 2
    assert points[0].regressions == ()
    assert points[1].complexity > points[0].complexity
    assert any("complexity grew" in item.message for item in points[1].regressions)
    assert "WHERE IT WENT BAD" in history.render_text(points)


def test_history_detects_new_dependency(repo: Path, commit, write):
    write(repo, "main.py", "import os\n")
    commit(repo, "base")
    write(repo, "main.py", "import os\nimport requests\n")
    commit(repo, "add requests")

    points = history.evaluate(repo, Config(), limit=5)
    assert any("new dependencies" in item.message for item in points[-1].regressions)


def test_history_detects_new_cycle(repo: Path, commit, write):
    write(repo, "a.py", "value = 1\n")
    write(repo, "b.py", "import a\nvalue = 2\n")
    commit(repo, "base")
    write(repo, "a.py", "import b\nvalue = 1\n")
    commit(repo, "close the cycle")

    points = history.evaluate(repo, Config(), limit=5)
    assert any("new package cycle" in item.message for item in points[-1].regressions)


def test_history_cli_json(repo: Path, commit, write, capsys):
    write(repo, "app.py", "def one() -> None:\n    return None\n")
    commit(repo, "base")
    write(repo, "app.py", "def one() -> None:\n    return None\n\ndef two() -> None:\n    return None\n")
    commit(repo, "add two")

    assert main(["history", "--path", str(repo), "--json", "--limit", "5"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "history"
    assert len(payload["commits"]) == 2
    assert set(payload["commits"][0]["metrics"]) == {
        "complexity",
        "api",
        "dependencies",
        "edges",
        "tests",
        "assertions",
    }
    assert payload["worst"][0]["commit"] == payload["commits"][-1]["commit"]
