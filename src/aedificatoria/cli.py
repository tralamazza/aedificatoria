"""Command-line interface for Aedificatoria."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import cargo as cargo_tools
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


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    created, existing = init_project(root)
    for path in created:
        print(f"created  {path.relative_to(root)}")
    for path in existing:
        print(f"exists   {path.relative_to(root)}")
    print("next: aed check")
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
        if args.cmd == "adr" and args.adr_cmd == "new":
            return cmd_adr_new(args)
        if args.cmd == "rules":
            return cmd_rules(args)
    except GitError as exc:
        print(f"aed: {exc}", file=sys.stderr)
        if "unknown revision" in str(exc).lower() or "needed a single revision" in str(exc).lower():
            print("aed: commit a baseline first, then re-run `aed check`", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"aed: {exc}", file=sys.stderr)
        return 2
    return 2
