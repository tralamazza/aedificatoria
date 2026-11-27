from __future__ import annotations

from pathlib import Path

from aedificatoria import cargo as cargo_module
from aedificatoria import rust
from aedificatoria.checks import run_checks as run_all_checks
from aedificatoria.config import Config, ForbiddenEdge
from aedificatoria.git import base_worktree, changed_files, resolve_commit
from aedificatoria.model import Context
from aedificatoria.report import verdict


def evaluate(repo: Path, config: Config | None = None):
    cfg = config or Config()
    commit = resolve_commit(repo, "HEAD")
    changed = changed_files(repo, commit)
    with base_worktree(repo, commit) as base_dir:
        context = Context(
            repo=repo,
            base_ref="HEAD",
            base_commit=commit,
            base_dir=base_dir,
            changed=changed,
            config=cfg,
        )
        return run_all_checks(context)


def rules(findings) -> set[str]:
    return {item.rule for item in findings}


def cargo(name: str) -> str:
    return f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n'


RUST_ITEMS = """
// fn commented_out() { assert!(true); }
const GREETING: &str = r#"fn fake() { assert!(true); }"#;

pub struct Widget {
    pub size: i32,
}

impl Widget {
    pub fn render(&self) -> String {
        if self.size > 0 && self.size < 10 {
            "big".to_string()
        } else {
            "small".to_string()
        }
    }

    fn private(&self) -> i32 {
        self.size
    }
}

pub(crate) fn internal() {}

pub trait Render {
    fn render(&self) -> String;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn renders() {
        assert_eq!(Widget { size: 1 }.render(), "small");
        assert!(true);
    }
}
"""


def test_rust_source_analysis():
    tree = rust.parse_text("src/lib.rs", RUST_ITEMS)
    assert tree is not None

    api = rust.public_api(tree)
    assert "Widget" in api
    assert "Widget::render" in api
    assert "Render" in api
    assert "Render::render" in api
    assert "Widget::private" not in api
    assert "internal" not in api
    assert "GREETING" not in api
    assert "tests::renders" not in api

    complexity = rust.complexity(tree)
    assert complexity["Widget::render"] == 3
    assert complexity["tests::renders"] == 1

    info = rust.test_info(tree)
    assert info.functions == frozenset({"tests::renders"})
    assert info.assertions == 2
    assert info.counts == {"tests::renders": 2}
    assert info.skips == frozenset()


def test_rust_struct_field_and_enum_variant_api(repo: Path, commit, write):
    base = "pub struct W {\n    pub size: i32,\n}\n\npub enum C {\n    Red,\n    Blue,\n}\n"
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", base)
    commit(repo)
    write(repo, "src/lib.rs", "pub struct W {\n}\n\npub enum C {\n    Red,\n}\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "public-api" and item.severity == "error" for item in findings)
    evidence = " ".join(item.evidence for item in findings if item.rule == "public-api")
    assert "W::size" in evidence
    assert "C::Blue" in evidence


def test_rust_imports_skip_internal_and_expand_groups():
    tree = rust.parse_text(
        "src/lib.rs",
        """
use std::collections::HashMap;
use serde::{Deserialize, Serialize};
use a::{b::c, d};
use crate::util;
use super::sibling;
use self::local;
extern crate log;
pub fn f() {}
"""
    )
    assert tree is not None
    assert [(ref.module, ref.level) for ref in rust.imports(tree)] == [
        ("std", 0),
        ("serde", 0),
        ("a", 0),
        ("log", 0),
    ]


def test_rust_public_api_break(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "pub fn handle(x: i32) -> i32 {\n    x\n}\n")
    commit(repo)
    write(repo, "src/lib.rs", "pub fn handle(x: i32, y: i32) -> i32 {\n    x + y\n}\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "public-api" and item.severity == "error" for item in findings)


def test_rust_parameter_rename_is_not_api_break(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "pub fn handle(value: i32) -> i32 {\n    value\n}\n")
    commit(repo)
    write(repo, "src/lib.rs", "pub fn handle(x: i32) -> i32 {\n    x\n}\n")

    findings = evaluate(repo, Config())
    assert not any(item.rule == "public-api" for item in findings)


def test_rust_private_signature_change_is_allowed(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "fn helper(x: i32) -> i32 {\n    x\n}\n")
    commit(repo)
    write(repo, "src/lib.rs", "fn helper(x: i32, y: i32) -> i32 {\n    x + y\n}\n")

    findings = evaluate(repo, Config())
    assert not any(item.rule == "public-api" for item in findings)


def test_rust_complexity_ratchet(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "pub fn score(x: i32) -> i32 {\n    x\n}\n")
    commit(repo)
    body = "pub fn score(x: i32) -> i32 {\n" + "".join(
        f"    if x == {index} {{\n        return {index};\n    }}\n" for index in range(12)
    ) + "    -1\n}\n"
    write(repo, "src/lib.rs", body)

    findings = evaluate(repo, Config(max_complexity=10, complexity_delta=5))
    assert any(item.rule == "complexity" and "score" in item.message for item in findings)


def test_rust_inline_test_assertions(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", RUST_ITEMS)
    commit(repo)
    write(repo, "src/lib.rs", RUST_ITEMS.replace("        assert!(true);\n", ""))

    findings = evaluate(repo, Config())
    assert any(
        item.rule == "test-tampering" and "assertions decreased" in item.message for item in findings
    )


def test_rust_inline_test_removed_and_ignored(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", RUST_ITEMS)
    commit(repo)
    write(repo, "src/lib.rs", RUST_ITEMS.replace("    #[test]\n    fn renders()", "    fn renders()"))

    findings = evaluate(repo, Config())
    assert any(item.rule == "test-tampering" and "removed test" in item.message for item in findings)

    write(repo, "src/lib.rs", RUST_ITEMS.replace("    #[test]\n    fn renders()", "    #[test]\n    #[ignore]\n    fn renders()"))
    findings = evaluate(repo, Config())
    assert any(item.rule == "test-tampering" and "skipped" in item.message for item in findings)


def test_rust_test_file_deleted(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "pub fn a() {}\n")
    write(repo, "tests/integration.rs", "#[test]\nfn works() {\n    assert!(true);\n}\n")
    commit(repo)
    (repo / "tests" / "integration.rs").unlink()

    findings = evaluate(repo, Config())
    assert any(item.rule == "test-tampering" and "test file deleted" in item.message for item in findings)


def test_rust_crate_cycle_and_forbidden_edge(repo: Path, commit, write):
    write(repo, "crates/a/Cargo.toml", cargo("a"))
    write(repo, "crates/b/Cargo.toml", cargo("b"))
    write(repo, "crates/a/src/lib.rs", "pub fn a() {}\n")
    write(repo, "crates/b/src/lib.rs", "pub fn b() {}\n")
    commit(repo)

    write(repo, "crates/a/src/lib.rs", "use b::b;\n\npub fn a() {}\n")
    config = Config(forbidden_edges=(ForbiddenEdge("a", "b", "a must not depend on b"),))
    findings = evaluate(repo, config)
    assert any(item.rule == "forbidden-edge" and "a -> b" in item.message for item in findings)

    write(repo, "crates/b/src/lib.rs", "use a::a;\n\npub fn b() {}\n")
    findings = evaluate(repo, Config())
    assert any(item.rule == "import-cycle" for item in findings)


def test_rust_cargo_dependency_allowlist(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "pub fn a() {}\n")
    commit(repo)
    write(repo, "Cargo.toml", cargo("app") + "\n[dependencies]\nserde = \"1\"\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "new-dependency" and "serde" in item.message for item in findings)

    findings = evaluate(repo, Config(allow_new_dependencies=("serde",)))
    assert not any(item.rule == "new-dependency" for item in findings)


def test_rust_use_dependency(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "pub fn a() {}\n")
    commit(repo)
    write(repo, "src/lib.rs", "use rand::Rng;\n\npub fn a() {}\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "new-dependency" and "rand" in item.message for item in findings)


def test_rust_local_modules_are_not_dependencies(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "pub fn a() {}\n")
    write(repo, "src/util.rs", "pub struct Thing;\n")
    commit(repo)
    write(repo, "src/lib.rs", "mod util;\nuse util::Thing;\n\npub fn a() {}\n")

    findings = evaluate(repo, Config())
    assert not any(item.rule == "new-dependency" for item in findings)


def test_rust_dotted_workspace_dependency_is_not_new(repo: Path, commit, write):
    write(repo, "Cargo.toml", cargo("app") + "\n[dependencies]\nserde.workspace = true\n")
    write(repo, "src/lib.rs", "pub fn a() {}\n")
    commit(repo)
    write(repo, "src/lib.rs", "use serde::Serialize;\n\npub fn a() {}\n")

    findings = evaluate(repo, Config())
    assert not any(item.rule == "new-dependency" for item in findings)


def _facts(crates=(), external=(), edges=()):
    return cargo_module.CargoFacts(
        frozenset(crates), frozenset(external), frozenset(edges)
    )


def test_cargo_advisory_reports_declared_cycle(repo: Path, commit, write, monkeypatch):
    write(repo, "README.md", "base\n")
    commit(repo)
    write(repo, "Cargo.toml", '[workspace]\nmembers = ["crates/a", "crates/b"]\nresolver = "2"\n')
    write(repo, "crates/a/Cargo.toml", cargo("a") + '\n[dependencies]\nb = { path = "../b" }\n')
    write(repo, "crates/b/Cargo.toml", cargo("b") + '\n[dependencies]\na = { path = "../a" }\n')

    monkeypatch.setattr(cargo_module, "available", lambda: True)
    monkeypatch.setattr(
        cargo_module,
        "metadata",
        lambda root: _facts(("a", "b"), (), (("a", "b"), ("b", "a"))) if root == repo else _facts(),
    )

    findings = evaluate(repo, Config())
    advisory = [item for item in findings if item.rule == "cargo-advisory"]
    assert len(advisory) == 1
    assert "cycle" in advisory[0].message
    assert advisory[0].severity == "info"
    assert verdict(findings) == "allow"


def test_cargo_advisory_reports_scanner_gap(repo: Path, commit, write, monkeypatch):
    write(repo, "README.md", "base\n")
    commit(repo)
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "pub fn a() {}\n")

    monkeypatch.setattr(cargo_module, "available", lambda: True)
    monkeypatch.setattr(
        cargo_module,
        "metadata",
        lambda root: _facts(("app",), ("serde",)) if root == repo else _facts(),
    )

    findings = evaluate(repo, Config())
    assert any(
        item.rule == "cargo-advisory" and "not seen by the manifest scan" in item.message
        for item in findings
    )


def test_cargo_advisory_reports_scanner_only_dependency(repo: Path, commit, write, monkeypatch):
    write(repo, "README.md", "base\n")
    commit(repo)
    write(repo, "Cargo.toml", cargo("app") + "\n[workspace.dependencies]\nserde = \"1\"\n")
    write(repo, "src/lib.rs", "pub fn a() {}\n")

    monkeypatch.setattr(cargo_module, "available", lambda: True)
    monkeypatch.setattr(
        cargo_module,
        "metadata",
        lambda root: _facts(("app",)) if root == repo else _facts(),
    )

    findings = evaluate(repo, Config())
    assert any(
        item.rule == "cargo-advisory" and "does not attach to a package" in item.message
        for item in findings
    )


def test_cargo_advisory_can_be_disabled(repo: Path, commit, write, monkeypatch):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "pub fn a() {}\n")
    commit(repo)
    write(repo, "src/lib.rs", "pub fn a() {}\npub fn b() {}\n")

    def explode(root):
        raise AssertionError("cargo must not run when disabled")

    monkeypatch.setattr(cargo_module, "available", lambda: True)
    monkeypatch.setattr(cargo_module, "metadata", explode)

    findings = evaluate(repo, Config(cargo_advisory=False))
    assert not any(item.rule == "cargo-advisory" for item in findings)


def test_cargo_advisory_skips_without_cargo(repo: Path, commit, write, monkeypatch):
    write(repo, "Cargo.toml", cargo("app"))
    write(repo, "src/lib.rs", "pub fn a() {}\n")
    commit(repo)
    write(repo, "src/lib.rs", "pub fn a() {}\npub fn b() {}\n")

    def explode(root):
        raise AssertionError("cargo must not run when unavailable")

    monkeypatch.setattr(cargo_module, "available", lambda: False)
    monkeypatch.setattr(cargo_module, "metadata", explode)

    findings = evaluate(repo, Config())
    assert not any(item.rule == "cargo-advisory" for item in findings)


def test_language_defaults_cover_rust():
    config = Config()
    assert config.is_test("crates/app/tests/integration.rs")
    assert config.is_test("src/parser_tests.rs")
    assert config.is_api("src/lib.rs")
    assert config.is_api("crates/app/src/main.rs")


def test_private_traits_and_private_impls_are_not_api():
    source = """
trait Secret { fn hide(&self); }
struct Hidden;
impl Hidden { pub fn hide(&self) {} }
pub trait Visible { fn show(&self); }
pub struct Public;
impl Public { pub fn show(&self) {} }
"""
    tree = rust.parse_text("src/lib.rs", source)
    assert tree is not None
    api = rust.public_api(tree)
    assert "Secret" not in api
    assert "Secret::hide" not in api
    assert "Hidden::hide" not in api
    assert "Visible" in api
    assert "Visible::show" in api
    assert "Public::show" in api
