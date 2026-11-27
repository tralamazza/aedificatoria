from __future__ import annotations

from pathlib import Path

from aedificatoria import jsts
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


def package(name: str, extra: str = "") -> str:
    return f'{{"name": "{name}", "private": true{extra}}}\n'


TS_SOURCE = """
import type { Foo } from "@acme/lib";

export interface Shape { area(): number; label?: string }
export type ID = string | number;
export enum Color { Red, Blue }

export function f(x: Shape): number {
    if (x) return 1;
    for (const c of []) { if (c) return 2; }
    return 0;
}

export const arrow = async (x: number): Promise<number> => {
    return x ? 1 : 0;
};
export const expr = (x: number) => x && x > 1;

export default class W extends Base implements I {
    private secret = 1;
    #hidden() { return 1; }
    static make(x?: number): W { return new W(); }
    render(a: number): { ok: boolean } { return { ok: !!a }; }
}
"""


def test_typescript_source_analysis():
    tree = jsts.parse_text("src/lib.ts", TS_SOURCE)
    assert tree is not None

    api = jsts.public_api(tree)
    assert "Shape" in api
    assert "ID" in api
    assert "Color" in api
    assert "default" in api
    assert api["f"] == "fn:(x:Shape):number"
    assert api["arrow"] == "fn:(x:number):Promise<number>"
    assert "W.make" in api
    assert "W.render" in api
    assert "W.secret" not in api
    assert "W.#hidden" not in api

    complexity = jsts.complexity(tree)
    assert complexity["f"] == 4
    assert complexity["arrow"] == 2
    assert complexity["expr"] == 2
    assert complexity["W.render"] == 1

    assert [(ref.module, ref.level) for ref in jsts.imports(tree)] == [("@acme/lib", 0)]


def test_javascript_tests_and_imports():
    source = """
import x from "./local";
import y from "pkg";
const z = require("legacy");
describe("math", () => {
    test.skip("adds", () => { expect(1).toBe(1); });
    it("subs", function () { assert(2 === 2); });
});
"""
    tree = jsts.parse_text("src/math.test.js", source)
    assert tree is not None
    info = jsts.test_info(tree)
    assert info.functions == frozenset({"math > adds", "math > subs"})
    assert info.assertions == 2
    assert info.skips == frozenset({"math > adds"})
    assert [(ref.module, ref.level) for ref in jsts.imports(tree)] == [("pkg", 0), ("legacy", 0)]


def test_jsx_is_parsed_with_tsx_grammar():
    source = "export function App({ x }: { x: number }) { return <div>{x > 0 && <span>{x}</span>}</div>; }"
    tree = jsts.parse_text("src/App.tsx", source)
    assert tree is not None
    assert "App" in jsts.public_api(tree)
    assert jsts.complexity(tree)["App"] == 2


def test_ambient_declarations_are_api():
    source = """
export declare function f(x: number): string;
export declare class C { method(): void; }
export declare const x: number;
export = legacy;
"""
    tree = jsts.parse_text("types/index.d.ts", source)
    assert tree is not None
    api = jsts.public_api(tree)
    assert api["f"] == "fn:(x:number):string"
    assert api["C.method"] == "():void"
    assert api["x"] == "const:number"
    assert "export=" in api


def test_parse_errors_do_not_crash():
    tree = jsts.parse_text("src/broken.ts", "export function ( {")
    assert tree is not None
    assert isinstance(jsts.public_api(tree), dict)
    assert isinstance(jsts.complexity(tree), dict)
    assert jsts.test_info(tree).assertions == 0


def test_ts_public_api_break(repo: Path, commit, write):
    write(repo, "package.json", package("app"))
    write(repo, "src/lib.ts", "export function handle(x: number): number {\n    return x;\n}\n")
    commit(repo)
    write(repo, "src/lib.ts", "export function handle(x: number, y: number): number {\n    return x + y;\n}\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "public-api" and item.severity == "error" for item in findings)


def test_ts_interface_member_removal(repo: Path, commit, write):
    write(repo, "package.json", package("app"))
    write(repo, "src/types.ts", "export interface Shape {\n    area(): number;\n    label: string;\n}\n")
    commit(repo)
    write(repo, "src/types.ts", "export interface Shape {\n    area(): number;\n}\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "public-api" and item.severity == "error" for item in findings)


def test_ts_complexity_ratchet(repo: Path, commit, write):
    write(repo, "package.json", package("app"))
    write(repo, "src/lib.ts", "export function score(x: number): number {\n    return x;\n}\n")
    commit(repo)
    body = "export function score(x: number): number {\n" + "".join(
        f"    if (x === {index}) {{ return {index}; }}\n" for index in range(12)
    ) + "    return -1;\n}\n"
    write(repo, "src/lib.ts", body)

    findings = evaluate(repo, Config(max_complexity=10, complexity_delta=5))
    assert any(item.rule == "complexity" and "score" in item.message for item in findings)


def test_ts_inline_test_tampering(repo: Path, commit, write):
    source = """
import { add } from "./math";
describe("math", () => {
    test("adds", () => { expect(add(1, 2)).toBe(3); expect(add(2, 2)).toBe(4); });
    test("subs", () => { expect(add(2, 1)).toBe(1); });
});
"""
    write(repo, "package.json", package("app"))
    write(repo, "src/math.test.ts", source)
    commit(repo)

    write(repo, "src/math.test.ts", source.replace("expect(add(2, 2)).toBe(4); ", ""))
    findings = evaluate(repo, Config())
    assert any(item.rule == "test-tampering" and "assertions decreased" in item.message for item in findings)

    write(repo, "src/math.test.ts", source.replace('test("subs", () => { expect(add(2, 1)).toBe(1); });', ""))
    findings = evaluate(repo, Config())
    assert any(item.rule == "test-tampering" and "removed test" in item.message for item in findings)

    write(repo, "src/math.test.ts", source.replace('test("subs"', 'test.skip("subs"'))
    findings = evaluate(repo, Config())
    assert any(item.rule == "test-tampering" and "skipped" in item.message for item in findings)


def test_ts_test_file_deleted(repo: Path, commit, write):
    write(repo, "package.json", package("app"))
    write(repo, "src/a.test.ts", 'test("works", () => { expect(1).toBe(1); });\n')
    commit(repo)
    (repo / "src" / "a.test.ts").unlink()

    findings = evaluate(repo, Config())
    assert any(item.rule == "test-tampering" and "test file deleted" in item.message for item in findings)


def test_js_workspace_cycle_and_forbidden_edge(repo: Path, commit, write):
    write(repo, "package.json", package("root"))
    write(repo, "packages/a/package.json", package("@acme/a"))
    write(repo, "packages/b/package.json", package("@acme/b"))
    write(repo, "packages/a/src/index.ts", "export const a = 1;\n")
    write(repo, "packages/b/src/index.ts", "export const b = 1;\n")
    commit(repo)

    write(repo, "packages/a/src/index.ts", "import { b } from '@acme/b';\nexport const a = b;\n")
    config = Config(forbidden_edges=(ForbiddenEdge("@acme/a", "@acme/b", "a must not depend on b"),))
    findings = evaluate(repo, config)
    assert any(item.rule == "forbidden-edge" and "@acme/a -> @acme/b" in item.message for item in findings)

    write(repo, "packages/b/src/index.ts", "import { a } from '@acme/a';\nexport const b = a;\n")
    findings = evaluate(repo, Config())
    assert any(item.rule == "import-cycle" for item in findings)


def test_js_package_dependency_allowlist(repo: Path, commit, write):
    write(repo, "package.json", package("app"))
    write(repo, "src/lib.ts", "export const a = 1;\n")
    commit(repo)
    write(repo, "package.json", '{"name": "app", "private": true, "dependencies": {"lodash": "^4"}}\n')

    findings = evaluate(repo, Config())
    assert any(item.rule == "new-dependency" and "lodash" in item.message for item in findings)

    findings = evaluate(repo, Config(allow_new_dependencies=("lodash",)))
    assert not any(item.rule == "new-dependency" for item in findings)


def test_js_import_dependency_and_builtins(repo: Path, commit, write):
    write(repo, "package.json", package("app"))
    write(repo, "src/lib.ts", "export const a = 1;\n")
    commit(repo)
    write(
        repo,
        "src/lib.ts",
        "import React from 'react';\nimport fs from 'node:fs';\nimport path from 'path';\nimport { x } from './x';\nexport const a = React;\n",
    )

    findings = evaluate(repo, Config())
    assert any(item.rule == "new-dependency" and "react" in item.message for item in findings)
    assert not any(item.rule == "new-dependency" and "fs" in item.message for item in findings)
    assert not any(item.rule == "new-dependency" and "path" in item.message for item in findings)


def test_js_local_workspace_dependency_is_not_new(repo: Path, commit, write):
    write(repo, "package.json", package("root"))
    write(repo, "packages/a/package.json", package("@acme/a"))
    write(repo, "packages/b/package.json", package("@acme/b"))
    write(repo, "packages/a/src/index.ts", "export const a = 1;\n")
    write(repo, "packages/b/src/index.ts", "export const b = 1;\n")
    commit(repo)
    write(repo, "packages/a/src/index.ts", "import { b } from '@acme/b';\nexport const a = b;\n")

    findings = evaluate(repo, Config())
    assert not any(item.rule == "new-dependency" for item in findings)


def test_config_defaults_cover_js_ts():
    config = Config()
    assert config.is_test("src/math.test.ts")
    assert config.is_test("src/math.spec.tsx")
    assert config.is_test("src/__tests__/math.ts")
    assert config.is_api("src/lib.ts")
    assert config.is_api("src/App.tsx")
    assert config.is_api("scripts/build.mjs")
    assert config.is_test_config("jest.config.ts")
    assert config.is_test_config("vitest.config.mts")
    assert verdict([]) == "allow"
