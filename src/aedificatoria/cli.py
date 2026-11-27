"""Command-line interface for Aedificatoria."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import baseline as baseline_store
from . import cargo as cargo_tools
from . import forge
from . import github as github_audit
from . import gitlab as gitlab_audit
from . import history as history_audit
from . import languages as language_adapters
from .adr import create_adr, init_project
from .checks import run_checks
from .config import load_config
from .git import GitError, base_worktree, changed_files, repo_root, resolve_commit
from .model import Context
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

    check = sub.add_parser("check", help="Check working-tree changes against a base commit")
    _add_path(check)
    check.add_argument("--base", default=None, help="Base revision (default: aed.toml or HEAD)")
    check.add_argument("--config", default=None, help="Config path (default: aed.toml)")
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
    hist.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    adr = sub.add_parser("adr", help="Architecture decision records")
    adr_sub = adr.add_subparsers(dest="adr_cmd", required=True)
    new = adr_sub.add_parser("new", help="Create a numbered ADR")
    new.add_argument("title")
    _add_path(new)
    new.add_argument(
        "--status",
        default="proposed",
        choices=("proposed", "accepted", "rejected", "superseded"),
    )

    rules = sub.add_parser("rules", help="Show the active rules and thresholds")
    _add_path(rules)
    return parser


def cmd_check(args: argparse.Namespace) -> int:
    repo = repo_root(Path(args.path))
    config = load_config(repo, args.config)
    base_ref = args.base or config.base
    commit = resolve_commit(repo, base_ref)
    changed = changed_files(repo, commit)
    config_path = args.config or "aed.toml"
    with base_worktree(repo, commit) as base_dir:
        context = Context(
            repo=repo,
            base_ref=base_ref,
            base_commit=commit,
            base_dir=base_dir,
            changed=changed,
            config=config,
            config_path=config_path,
        )
        findings = run_checks(context)

    if args.json:
        print(json.dumps(json_payload(findings, commit, changed), indent=2))
    else:
        print(render_text(findings, commit, changed))
    return exit_code(findings)


def cmd_baseline(args: argparse.Namespace) -> int:
    root = repo_root(Path(args.path))
    config = load_config(root)
    if not config.baseline_path:
        print("aed: baseline_path is empty; baseline is disabled", file=sys.stderr)
        return 2
    data = baseline_store.collect(root, config)
    path = baseline_store.write(root, config.baseline_path, data)
    print(path.relative_to(root) if path.is_relative_to(root) else path)
    return 0


def _history_github(root: Path, config, args: argparse.Namespace):
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
            root, config, limit=max(1, args.limit), since=args.since, head=args.head or "HEAD"
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
    root = Path(args.path).resolve()
    created, existing = init_project(root)
    for path in created:
        print(f"created  {path.relative_to(root)}")
    for path in existing:
        print(f"exists   {path.relative_to(root)}")
    print("next: aed baseline; aed check")
    return 0


def cmd_adr_new(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    config = load_config(root)
    path = create_adr(root, args.title, status=args.status, config=config)
    print(path.relative_to(root))
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
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
    except forge.ForgeError as exc:
        print(f"aed: {exc}", file=sys.stderr)
        return 2
    except GitError as exc:
        print(f"aed: {exc}", file=sys.stderr)
        if "unknown revision" in str(exc).lower() or "needed a single revision" in str(exc).lower():
            print("aed: commit a baseline first, then re-run `aed check`", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"aed: {exc}", file=sys.stderr)
        return 2
    return 2
