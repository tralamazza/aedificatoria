from __future__ import annotations

import json
import subprocess
from pathlib import Path

from aedificatoria import history
from aedificatoria.cli import main
from aedificatoria.config import Config
from aedificatoria.git import merge_edited_files


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


def test_history_all_includes_side_branch(repo: Path, commit, write):
    write(repo, "base.py", "value = 1\n")
    commit(repo, "base")
    branch = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "-b", "feature"], check=True
    )
    write(repo, "feature.py", "def feature() -> int:\n    return 1\n")
    commit(repo, "feature work")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", branch], check=True)
    write(repo, "main.py", "value = 2\n")
    commit(repo, "main work")
    subprocess.run(
        ["git", "-C", str(repo), "merge", "-q", "--no-ff", "feature", "-m", "merge feature"],
        check=True,
    )

    first_parent = history.evaluate(repo, Config(), limit=10)
    side_branch = history.evaluate(repo, Config(), limit=10, all_commits=True)
    assert "feature work" not in {point.subject for point in first_parent}
    assert "feature work" in {point.subject for point in side_branch}
    feature = next(point for point in side_branch if point.subject == "feature work")
    assert feature.impact > 0


def test_history_parallel_matches_sequential(repo: Path, commit, write):
    write(repo, "app.py", "value = 1\n")
    commit(repo, "first")
    write(repo, "app.py", "value = 2\n\ndef extra() -> None:\n    return None\n")
    commit(repo, "second")
    write(repo, "app.py", "value = 3\n\ndef extra() -> None:\n    return None\n")
    commit(repo, "third")

    sequential = history.evaluate(repo, Config(), limit=5, jobs=1)
    parallel = history.evaluate(repo, Config(), limit=5, jobs=2)
    assert [point.as_dict() for point in parallel] == [
        point.as_dict() for point in sequential
    ]


def test_history_head_ref(repo: Path, commit, write):
    write(repo, "app.py", "value = 1\n")
    commit(repo, "first")
    write(repo, "app.py", "value = 2\n")
    commit(repo, "second")
    write(repo, "app.py", "value = 3\n")
    commit(repo, "third")

    points = history.evaluate(repo, Config(), limit=5, head="HEAD~1")
    assert [point.subject for point in points] == ["first", "second"]


def test_history_flags_large_merge_as_recoverable(repo: Path, commit, write):
    write(repo, "base.py", "value = 1\n")
    commit(repo, "base")
    branch = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "-b", "feature"], check=True
    )
    for index in range(3):
        write(repo, f"feature_{index}.py", f"value = {index}\n")
    commit(repo, "feature work")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", branch], check=True)
    write(repo, "main.py", "value = 2\n")
    commit(repo, "main work")
    subprocess.run(
        ["git", "-C", str(repo), "merge", "-q", "--no-ff", "feature", "-m", "merge feature"],
        check=True,
    )

    config = Config(max_changed_files=1, max_changed_lines=1)
    points = history.evaluate(repo, config, limit=10)
    merge = next(point for point in points if point.kind == "merge")
    assert any("large merge" in item.message for item in merge.regressions)
    assert any("branch commits remain" in item.message for item in merge.regressions)
    assert not any("internal commits are unavailable" in item.message for item in merge.regressions)

    all_points = history.evaluate(repo, config, limit=10, all_commits=True)
    assert "feature work" in {point.subject for point in all_points}


def test_history_flags_merge_conflict_resolution(repo: Path, commit, write):
    write(repo, "service.py", "value = 1\n")
    commit(repo, "base")
    branch = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "-b", "feature"], check=True
    )
    write(repo, "service.py", "value = 2\n")
    commit(repo, "feature side")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", branch], check=True)
    write(repo, "service.py", "value = 3\n")
    commit(repo, "main side")
    merge = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "merge",
            "-q",
            "--no-ff",
            "feature",
            "-m",
            "merge feature",
        ],
        check=False,
    )
    assert merge.returncode != 0
    write(repo, "service.py", "value = 4\n")
    commit(repo, "merge feature")

    points = history.evaluate(repo, Config(), limit=10)
    point = next(point for point in points if point.kind == "merge")
    assert any("merge-only lines" in item.message for item in point.regressions)
    assert any("potential conflict resolution" in item.message for item in point.regressions)
    assert any("service.py" in item.message for item in point.regressions)


def test_history_auto_merge_does_not_flag_merge_only_lines(repo: Path, commit, write):
    write(repo, "app.py", "a = 1\nb = 2\nc = 3\nd = 4\n")
    commit(repo, "base")
    branch = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "-b", "feature"], check=True
    )
    write(repo, "app.py", "a = 1\nb = 2\nside = 3\nc = 3\nd = 4\n")
    commit(repo, "feature side")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", branch], check=True)
    write(repo, "app.py", "a = 1\nmain = 2\nb = 2\nc = 3\nd = 4\n")
    commit(repo, "main side")
    subprocess.run(
        ["git", "-C", str(repo), "merge", "-q", "--no-ff", "feature", "-m", "merge feature"],
        check=True,
    )

    points = history.evaluate(repo, Config(), limit=10)
    merge = next(point for point in points if point.kind == "merge")
    assert not any("merge-only lines" in item.message for item in merge.regressions)


def test_history_handles_octopus_merge(repo: Path, commit, write):
    write(repo, "base.py", "value = 0\n")
    commit(repo, "base")
    branch = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()
    heads = []
    for name in ("one", "two", "three"):
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", branch], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", name], check=True
        )
        write(repo, f"{name}.py", f"value = {name!r}\n")
        commit(repo, name)
        heads.append(name)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", branch], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "merge", "-q", "--no-ff", *heads, "-m", "octopus merge"],
        check=True,
    )

    points = history.evaluate(repo, Config(), limit=10)
    merge = next(point for point in points if point.kind == "merge")
    assert merge.parents == 4
    assert not any("merge-only lines" in item.message for item in merge.regressions)

    all_points = history.evaluate(repo, Config(), limit=10, all_commits=True)
    assert {"one", "two", "three"} <= {point.subject for point in all_points}


def test_merge_edited_files_supports_octopus(repo: Path, commit, write):
    write(repo, "base.py", "value = 0\n")
    commit(repo, "base")
    branch = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()
    parents = [subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()]
    for name in ("one", "two", "three"):
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", branch], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "-b", name], check=True
        )
        write(repo, f"{name}.py", f"value = {name!r}\n")
        commit(repo, name)
        parents.append(
            subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        )
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", branch], check=True)
    write(repo, "one.py", "value = 'one'\n")
    write(repo, "two.py", "value = 'two'\n")
    write(repo, "three.py", "value = 'three'\n")
    write(repo, "merge_only.py", "value = 'resolved'\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    tree = subprocess.check_output(
        ["git", "-C", str(repo), "write-tree"], text=True
    ).strip()
    command = ["git", "-C", str(repo), "commit-tree", tree]
    for parent in parents:
        command.extend(["-p", parent])
    command.extend(["-m", "octopus merge"])
    merge = subprocess.check_output(command, text=True).strip()

    assert merge_edited_files(repo, merge) == ["merge_only.py"]


def test_history_rebase_merge_is_linear(repo: Path, commit, write):
    write(repo, "base.py", "value = 0\n")
    commit(repo, "base")
    branch = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], text=True
    ).strip()
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-q", "-b", "feature"], check=True
    )
    write(repo, "one.py", "value = 1\n")
    commit(repo, "feature one")
    write(repo, "two.py", "value = 2\n")
    commit(repo, "feature two")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", branch], check=True)
    write(repo, "main.py", "value = 3\n")
    commit(repo, "main advance")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "feature"], check=True)
    subprocess.run(["git", "-C", str(repo), "rebase", "-q", branch], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", branch], check=True)
    subprocess.run(["git", "-C", str(repo), "merge", "-q", "--ff-only", "feature"], check=True)

    points = history.evaluate(repo, Config(), limit=10)
    assert {"base", "main advance", "feature one", "feature two"} <= {
        point.subject for point in points
    }
    feature = [point for point in points if point.subject.startswith("feature")]
    assert feature
    assert all(point.kind == "commit" for point in feature)
    assert not any(
        "large merge" in item.message or "merge-only" in item.message
        for point in points
        for item in point.regressions
    )


def test_history_marks_squash_and_flags_oversized_atomic(repo: Path, commit, write):
    write(repo, "README.md", "base\n")
    write(repo, "seed.py", "value = 0\n")
    commit(repo, "base")
    for index in range(3):
        write(repo, f"module_{index}.py", f"value = {index}\n")
    commit(repo, "feat: bulk change (#42)")

    points = history.evaluate(repo, Config(max_changed_files=1, max_changed_lines=1), limit=5)
    assert points[0].regressions == ()
    point = points[-1]
    assert point.kind == "squash"
    assert point.pr == "42"
    assert point.files == 3
    assert point.lines > 1
    assert any("large atomic squash #42" in item.message for item in point.regressions)
    assert "[squash #42]" in history.render_text(points)


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
