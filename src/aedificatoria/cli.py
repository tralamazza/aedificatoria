"""Command-line interface for Aedificatoria."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import doctor as doctor_tools
from . import forge
from .config import Config, ConfigError, load_config
from .git import GitError, base_worktree, changed_files, index_tree, repo_root, resolve_commit
from .model import ChangedFile, Context
from .paths import ConfinedPathError
from .report import exit_code, json_payload, render_text


def _add_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", default=".", help="Repository or project directory (default: .)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aed",
        description="Architecture guard for coding agents: make design constraints executable.",
    )
    parser.add_argument("--version", action="version", version=f"aedificatoria {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="Check changes against a base commit")
    _add_path(check)
    check.add_argument("--base", default=None, help="Base revision (default: aed.toml or HEAD)")
    check.add_argument("--config", default=None, help="Config path (default: aed.toml)")
    check.add_argument(
        "--staged",
        action="store_true",
        help="Check the staged index instead of the working tree",
    )
    check.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    init = sub.add_parser("init", help="Create aed.toml and the first ADR")
    _add_path(init)

    baseline_cmd = sub.add_parser("baseline", help="Write the persistent metric baseline")
    _add_path(baseline_cmd)

    hist = sub.add_parser("history", help="Audit first-parent history for metric regressions")
    _add_path(hist)
    hist.add_argument("--limit", type=int, default=20, help="First-parent commits to inspect (default: 20)")
    hist.add_argument("--since", default=None, help="Only commits after this revision")
    hist.add_argument("--head", default=None, help="Tip revision to inspect (default: HEAD)")
    hist.add_argument(
        "--github-pr",
        type=int,
        default=None,
        help="Audit a GitHub PR through the API (requires gh and auth)",
    )
    hist.add_argument(
        "--gitlab-mr",
        type=int,
        default=None,
        help="Audit a GitLab MR through the API (requires glab and auth)",
    )
    hist.add_argument(
        "--repo",
        default=None,
        help="GitHub owner/repo or GitLab full project path (default: origin)",
    )
    hist.add_argument(
        "--all",
        action="store_true",
        help="Analyze all reachable commits with parent-aware comparisons, not only first-parent",
    )
    hist.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    hist.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=1,
        help="Parallel workers for local history (default: 1; try CPU count)",
    )

    adr = sub.add_parser("adr", help="Architecture decision records")
    adr_sub = adr.add_subparsers(dest="adr_cmd", required=True)
    new = adr_sub.add_parser("new", help="Create a numbered ADR")
    new.add_argument("title")
    _add_path(new)
    new.add_argument(
        "--status",
        default="proposed",
        choices=("proposed", "rejected", "superseded"),
    )

    rules = sub.add_parser("rules", help="Show the active rules and thresholds")
    _add_path(rules)

    doctor = sub.add_parser("doctor", help="Check required and optional installed tools")
    doctor.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def _run_check(
    repo: Path,
    head_dir: Path,
    base_ref: str,
    commit: str,
    changed: list[ChangedFile],
    config: Config,
    config_path: str,
):
    from .checks import run_checks

    with base_worktree(repo, commit) as base_dir:
        context = Context(
            repo=head_dir,
            base_ref=base_ref,
            base_commit=commit,
            base_dir=base_dir,
            changed=changed,
            config=config,
            config_path=config_path,
        )
        return run_checks(context)


def cmd_check(args: argparse.Namespace) -> int:
    repo = repo_root(Path(args.path))
    config_path = args.config or "aed.toml"
    if args.staged:
        with index_tree(repo) as head_dir:
            config = load_config(head_dir, config_path)
            base_ref = args.base or config.base
            commit = resolve_commit(repo, base_ref)
            changed = changed_files(repo, commit, staged=True)
            findings = _run_check(
                repo, head_dir, base_ref, commit, changed, config, config_path
            )
    else:
        config = load_config(repo, config_path)
        base_ref = args.base or config.base
        commit = resolve_commit(repo, base_ref)
        changed = changed_files(repo, commit)
        findings = _run_check(repo, repo, base_ref, commit, changed, config, config_path)

    if args.json:
        print(json.dumps(json_payload(findings, commit, changed), indent=2))
    else:
        print(render_text(findings, commit, changed))
    return exit_code(findings)


def cmd_baseline(args: argparse.Namespace) -> int:
    from . import baseline as baseline_store

    root = repo_root(Path(args.path))
    config = load_config(root)
    if not config.baseline_path:
        print("aed: baseline_path is empty; baseline is disabled", file=sys.stderr)
        return 2
    try:
        data = baseline_store.collect(root, config)
        path = baseline_store.write(root, config.baseline_path, data)
    except baseline_store.BaselineError as exc:
        print(f"aed: {exc}", file=sys.stderr)
        return 2
    print(path.relative_to(root) if path.is_relative_to(root) else path)
    return 0


def _history_github(root: Path, config, args: argparse.Namespace):
    from . import github as github_audit
    from . import history as history_audit

    if args.since or args.head:
        print("aed: --github-pr cannot be combined with --since/--head", file=sys.stderr)
        return None
    slug = args.repo or github_audit.slug_from_remote(root)
    if not slug:
        print("aed: cannot determine GitHub repository; pass --repo owner/name", file=sys.stderr)
        return None
    client = github_audit.GitHubClient(slug)
    points = history_audit.evaluate_forge(
        root, config, args.github_pr, max(1, args.limit), client
    )
    source = {"github_pr": args.github_pr, "repo": slug, "api": "github"}
    header = f"PR #{args.github_pr} via GitHub API ({slug}; retention not guaranteed)"
    return points, source, header


def _history_gitlab(root: Path, config, args: argparse.Namespace):
    from . import gitlab as gitlab_audit
    from . import history as history_audit

    if args.since or args.head:
        print("aed: --gitlab-mr cannot be combined with --since/--head", file=sys.stderr)
        return None
    project = args.repo or gitlab_audit.project_path_from_remote(root)
    if not project:
        print("aed: cannot determine GitLab project; pass --repo group/project", file=sys.stderr)
        return None
    client = gitlab_audit.GitLabClient(project, cwd=root)
    points = history_audit.evaluate_forge(
        root, config, args.gitlab_mr, max(1, args.limit), client
    )
    source = {"gitlab_mr": args.gitlab_mr, "repo": project, "api": "gitlab"}
    header = f"MR !{args.gitlab_mr} via GitLab API ({project}; retention not guaranteed)"
    return points, source, header


def cmd_history(args: argparse.Namespace) -> int:
    from . import history as history_audit

    root = repo_root(Path(args.path))
    config = load_config(root)
    if args.github_pr is not None and args.gitlab_mr is not None:
        print("aed: --github-pr and --gitlab-mr are mutually exclusive", file=sys.stderr)
        return 2
    if args.github_pr is not None:
        prepared = _history_github(root, config, args)
    elif args.gitlab_mr is not None:
        prepared = _history_gitlab(root, config, args)
    else:
        points = history_audit.evaluate(
            root,
            config,
            limit=max(1, args.limit),
            since=args.since,
            head=args.head or "HEAD",
            jobs=max(1, args.jobs),
            all_commits=args.all,
        )
        source = None
        header = "first-parent commits"
        prepared = (points, source, header)
    if prepared is None:
        return 2
    points, source, header = prepared
    if args.json:
        print(json.dumps(history_audit.json_payload(points, source), indent=2))
    else:
        print(history_audit.render_text(points, header))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    from .adr import init_project

    root = Path(args.path).resolve()
    created, existing = init_project(root)
    for path in created:
        print(f"created  {path.relative_to(root)}")
    for path in existing:
        print(f"exists   {path.relative_to(root)}")
    print("next: aed baseline; aed check")
    return 0


def cmd_adr_new(args: argparse.Namespace) -> int:
    from .adr import create_adr

    root = Path(args.path).resolve()
    config = load_config(root)
    path = create_adr(root, args.title, status=args.status, config=config)
    print(path.relative_to(root))
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    from . import cargo as cargo_tools
    from . import languages as language_adapters

    root = Path(args.path).resolve()
    config = load_config(root)
    print(f"base: {config.base}")
    print(f"adr_dir: {config.adr_dir}")
    print(f"source_roots: {', '.join(config.source_roots)}")
    print(f"languages: {', '.join(language_adapters.names())}")
    cargo_state = "available" if cargo_tools.available() else "unavailable"
    print(f"cargo_advisory: {str(config.cargo_advisory).lower()} (cargo {cargo_state}; info only)")
    print(f"baseline: {config.baseline_path or '(disabled)'}")
    print(
        "baseline caps: "
        f"complexity +{config.max_baseline_complexity_growth}, "
        f"api +{config.max_baseline_api_growth}, "
        f"deps +{config.max_baseline_dependency_growth}, "
        f"edges +{config.max_baseline_edge_growth}"
    )
    print(
        "thresholds: "
        f"max_changed_files={config.max_changed_files}, "
        f"max_changed_lines={config.max_changed_lines}, "
        f"max_complexity={config.max_complexity}, "
        f"complexity_delta={config.complexity_delta}"
    )
    print("one_way_doors:")
    for pattern in config.one_way_doors:
        print(f"  {pattern}")
    if config.forbidden_edges:
        print("forbidden_edges:")
        for edge in config.forbidden_edges:
            reason = f"  # {edge.reason}" if edge.reason else ""
            print(f"  {edge.source} -> {edge.target}{reason}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    tools = doctor_tools.collect()
    if args.json:
        print(json.dumps(doctor_tools.payload(tools), indent=2))
    else:
        print(doctor_tools.render_text(tools))
    return 0 if doctor_tools.ok(tools) else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "baseline":
            return cmd_baseline(args)
        if args.cmd == "history":
            return cmd_history(args)
        if args.cmd == "adr" and args.adr_cmd == "new":
            return cmd_adr_new(args)
        if args.cmd == "rules":
            return cmd_rules(args)
        if args.cmd == "doctor":
            return cmd_doctor(args)
    except forge.ForgeError as exc:
        print(f"aed: {exc}", file=sys.stderr)
        return 2
    except GitError as exc:
        print(f"aed: {exc}", file=sys.stderr)
        if "unknown revision" in str(exc).lower() or "needed a single revision" in str(exc).lower():
            print("aed: commit a baseline first, then re-run `aed check`", file=sys.stderr)
        return 2
    except (ConfigError, ConfinedPathError, ValueError) as exc:
        print(f"aed: {exc}", file=sys.stderr)
        return 2
    except RecursionError:
        print("aed: input nesting is too deep", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"aed: {exc}", file=sys.stderr)
        return 2
    return 2
