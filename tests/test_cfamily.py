from __future__ import annotations

from pathlib import Path

from aedificatoria import cfamily
from aedificatoria.checks import run_checks as run_all_checks
from aedificatoria.config import Config
from aedificatoria.git import base_worktree, changed_files, resolve_commit
from aedificatoria.model import Context, TestInfo as Info


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


def test_c_header_api_excludes_static_and_includes_struct_fields():
    tree = cfamily.parse_text(
        "include/foo.h",
        '#include <stdio.h>\nstatic int hidden(int x);\nint add(int x, int y);\n'
        "typedef struct Point { int x; } Point;\nstruct S { int a; };\n",
    )
    assert tree is not None
    api = cfamily.public_api(tree)
    assert "add" in api
    assert "hidden" not in api
    assert "Point" in api
    assert "Point::x" in api
    assert "S::a" in api


def test_cpp_namespace_class_public_api():
    tree = cfamily.parse_text(
        "include/foo.hpp",
        "namespace ns { class W { private: int secret; public: int size; "
        "int render(int x) const; }; int f(const char* s); }\n",
    )
    assert tree is not None
    api = cfamily.public_api(tree)
    assert "ns::W" in api
    assert "ns::W::size" in api
    assert "ns::W::render" in api
    assert "ns::W::secret" not in api
    assert "ns::f" in api


def test_cpp_complexity():
    tree = cfamily.parse_text(
        "include/foo.hpp",
        "inline int score(int x) { if (x > 0) return 1; "
        "for (int i = 0; i < x; ++i) { x += i; } return 0; }\n",
    )
    assert tree is not None
    assert cfamily.complexity(tree)["score"] == 3


def test_gtest_and_catch_test_inventory():
    tree = cfamily.parse_text(
        "tests/test_math.cpp",
        'TEST(Math, adds) { EXPECT_EQ(1, 1); ASSERT_TRUE(true); }\n'
        'TEST_CASE("case", "[tag]") { REQUIRE(1 == 1); }\n'
        'TEST_CASE("skip", "[.]") { REQUIRE(1 == 1); }\n',
    )
    assert tree is not None
    info = cfamily.test_info(tree)
    assert info.functions == frozenset({"Math > adds", "case", "skip"})
    assert info.counts == {"Math > adds": 2, "case": 1, "skip": 1}
    assert info.skips == frozenset({"skip"})


def test_include_component_graph_and_external_dependency(tmp_path: Path):
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "src" / "lib" / "a.cpp").write_text(
        '#include "../core/b.h"\n#include <vector>\n#include <fmt/format.h>\n'
    )
    (tmp_path / "src" / "core" / "b.h").write_text("int b();\n")
    config = Config()
    edges, packages = cfamily.package_edges(tmp_path, config)
    assert ("lib", "core") in edges
    assert packages == {"lib", "core"}
    imports = cfamily.project_imports(tmp_path, config)
    assert "fmt" in imports
    assert "vector" not in imports


def test_c_public_api_break(repo: Path, commit, write):
    write(repo, "include/api.h", "int add(int x);\n")
    commit(repo)
    write(repo, "include/api.h", "int add(int x, int y);\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "public-api" and item.severity == "error" for item in findings)


def test_cpp_test_assertion_removal(repo: Path, commit, write):
    write(
        repo,
        "tests/test_math.cpp",
        "TEST(Math, adds) { EXPECT_EQ(1, 1); EXPECT_EQ(2, 2); }\n",
    )
    commit(repo)
    write(repo, "tests/test_math.cpp", "TEST(Math, adds) { EXPECT_EQ(1, 1); }\n")

    findings = evaluate(repo, Config())
    assert any(
        item.rule == "test-tampering" and "assertions decreased" in item.message
        for item in findings
    )


def test_cpp_external_include_is_new_dependency(repo: Path, commit, write):
    write(repo, "src/main.cpp", "#include <vector>\nint main() { return 0; }\n")
    commit(repo)
    write(repo, "src/main.cpp", "#include <vector>\n#include <fmt/format.h>\nint main() { return 0; }\n")

    findings = evaluate(repo, Config())
    assert any(item.rule == "new-dependency" and "fmt" in item.message for item in findings)


def test_malformed_cpp_does_not_crash():
    tree = cfamily.parse_text("src/broken.cpp", "int main( {")
    assert tree is not None
    assert isinstance(cfamily.public_api(tree), dict)
    assert isinstance(cfamily.complexity(tree), dict)
    assert isinstance(cfamily.test_info(tree), Info)


def test_cpp_test_includes_are_not_dependencies(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_math.cpp").write_text(
        "#include <gtest/gtest.h>\nTEST(Math, adds) { EXPECT_EQ(1, 1); }\n",
        encoding="utf-8",
    )
    config = Config()
    assert "gtest" not in cfamily.project_imports(tmp_path, config)
    assert cfamily.local_packages(tmp_path, config) == set()


def test_c_header_with_guard_and_export_macro_is_api():
    source = """
#ifndef API_H
#define API_H
#ifdef __cplusplus
extern "C" {
#endif

#define API_EXPORT(type) type
API_EXPORT(int) add(int x, int y);

#ifdef __cplusplus
}
#endif
#endif
"""
    tree = cfamily.parse_text("include/api.h", source)
    assert tree is not None
    api = cfamily.public_api(tree)
    assert "add" in api
