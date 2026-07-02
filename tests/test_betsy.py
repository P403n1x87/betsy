import json
import sys
from pathlib import Path

import pytest

from betsy import (
    DependencyGraph,
    ImportVisitor,
    _get_imports,
    _is_module,
    _path_to_module_path,
    anyprefix,
    get_imports,
    main,
)


@pytest.fixture
def pkg_root(tmp_path: Path) -> Path:
    root = tmp_path.resolve()
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod_a.py").write_text(
        "import pkg.mod_b\n"
        "from . import mod_c\n"
        "from .mod_c import thing\n"
    )
    (pkg / "mod_b.py").write_text("")
    (pkg / "mod_c.py").write_text("")

    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("from .. import mod_a\n")
    (sub / "mod_d.py").write_text("from ..mod_a import x\nfrom . import mod_e\n")
    (sub / "mod_e.py").write_text("")

    return pkg


# -- anyprefix ---------------------------------------------------------------


def test_anyprefix_matches_exact():
    assert anyprefix("pkg.mod_a", {"pkg.mod_a"}, False) is True


def test_anyprefix_matches_prefix():
    assert anyprefix("pkg.mod_a", {"pkg"}, False) is True


def test_anyprefix_no_match():
    assert anyprefix("pkg.mod_a", {"other"}, False) is False


def test_anyprefix_does_not_match_partial_component():
    # "pk" is a prefix of the string but not of a dotted component
    assert anyprefix("pkg.mod_a", {"pk"}, False) is False


def test_anyprefix_none_uses_default():
    assert anyprefix("pkg.mod_a", None, True) is True
    assert anyprefix("pkg.mod_a", None, False) is False


# -- ImportVisitor / _get_imports --------------------------------------------


def test_visit_import_records_dotted_module():
    visitor = ImportVisitor(base=("pkg", "mod_a"))
    visitor.visit(__import__("ast").parse("import pkg.mod_b\n"))
    assert visitor.imports == {("pkg", "mod_b")}


def test_visit_import_records_multiple_names():
    visitor = ImportVisitor(base=("pkg", "mod_a"))
    visitor.visit(__import__("ast").parse("import pkg.mod_b, os\n"))
    assert visitor.imports == {("pkg", "mod_b"), ("os",)}


def test_visit_import_from_absolute():
    visitor = ImportVisitor(base=("pkg", "mod_a"))
    visitor.visit(__import__("ast").parse("from pkg.sub import mod_e\n"))
    assert visitor.imports == {("pkg", "sub")}


def test_visit_import_from_relative_level_1_from_module():
    # from a non-package module, "." refers to the containing package
    visitor = ImportVisitor(base=("pkg", "mod_a"), is_package=False)
    visitor.visit(__import__("ast").parse("from .mod_c import thing\n"))
    assert visitor.imports == {("pkg", "mod_c")}


def test_visit_import_from_relative_level_2_from_submodule():
    visitor = ImportVisitor(base=("pkg", "sub", "mod_d"), is_package=False)
    visitor.visit(__import__("ast").parse("from ..mod_a import x\n"))
    assert visitor.imports == {("pkg", "mod_a")}


def test_visit_import_from_relative_within_package_init():
    # "from .. import mod_a" carries no module in the AST, so only the
    # resolved parent package is recorded, not the imported name itself.
    visitor = ImportVisitor(base=("pkg", "sub"), is_package=True)
    visitor.visit(__import__("ast").parse("from .. import mod_a\n"))
    assert visitor.imports == {("pkg",)}


def test_visit_import_from_invalid_level_raises():
    visitor = ImportVisitor(base=("pkg", "mod_a"), is_package=False)
    with pytest.raises(AssertionError):
        visitor.visit(__import__("ast").parse("from ...toohigh import x\n"))


def test_get_imports_helper():
    imports = _get_imports("import pkg.mod_b\n", base=("pkg", "mod_a"))
    assert imports == {("pkg", "mod_b")}


# -- _path_to_module_path -----------------------------------------------------


def test_path_to_module_path_module(pkg_root: Path):
    assert _path_to_module_path(pkg_root / "mod_a.py", pkg_root) == ("pkg", "mod_a")


def test_path_to_module_path_package_init(pkg_root: Path):
    assert _path_to_module_path(pkg_root / "sub" / "__init__.py", pkg_root) == (
        "pkg",
        "sub",
    )


def test_path_to_module_path_nested_module(pkg_root: Path):
    assert _path_to_module_path(pkg_root / "sub" / "mod_d.py", pkg_root) == (
        "pkg",
        "sub",
        "mod_d",
    )


# -- _is_module ----------------------------------------------------------------


def test_is_module_true_for_module_file(pkg_root: Path):
    assert _is_module(("pkg", "mod_a"), pkg_root) is True


def test_is_module_true_for_package_dir(pkg_root: Path):
    assert _is_module(("pkg", "sub"), pkg_root) is True


def test_is_module_false_for_unknown(pkg_root: Path):
    assert _is_module(("pkg", "does_not_exist"), pkg_root) is False


# -- get_imports -----------------------------------------------------------


def test_get_imports_rejects_non_python_file(tmp_path: Path):
    not_py = tmp_path / "not_python.txt"
    not_py.write_text("import os\n")
    with pytest.raises(AssertionError):
        get_imports(not_py, tmp_path)


def test_get_imports_top_level_module(pkg_root: Path):
    imports = get_imports(pkg_root / "mod_a.py", pkg_root)
    assert imports == {"pkg", "pkg.mod_b", "pkg.mod_c"}


def test_get_imports_package_init(pkg_root: Path):
    imports = get_imports(pkg_root / "sub" / "__init__.py", pkg_root)
    assert imports == {"pkg"}


def test_get_imports_nested_module(pkg_root: Path):
    imports = get_imports(pkg_root / "sub" / "mod_d.py", pkg_root)
    assert imports == {"pkg.mod_a", "pkg.sub"}


# -- DependencyGraph ------------------------------------------------------------


def test_dependency_graph_nodes(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    assert graph.nodes == {
        "pkg",
        "pkg.mod_a",
        "pkg.mod_b",
        "pkg.mod_c",
        "pkg.sub",
        "pkg.sub.mod_d",
        "pkg.sub.mod_e",
    }


def test_dependency_graph_data(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    assert graph.data == {
        "pkg": set(),
        "pkg.mod_a": {"pkg", "pkg.mod_b", "pkg.mod_c"},
        "pkg.mod_b": set(),
        "pkg.mod_c": set(),
        "pkg.sub": {"pkg"},
        "pkg.sub.mod_d": {"pkg.mod_a", "pkg.sub"},
        "pkg.sub.mod_e": set(),
    }


def test_dependency_graph_depth(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    assert graph.depth == 2


def test_dependency_graph_include_filters_importers(pkg_root: Path):
    graph = DependencyGraph(pkg_root, include={"pkg.sub"})
    assert graph.data.keys() == {"pkg.sub", "pkg.sub.mod_d", "pkg.sub.mod_e"}


def test_dependency_graph_exclude_filters_importers(pkg_root: Path):
    graph = DependencyGraph(pkg_root, exclude={"pkg.sub"})
    assert graph.data.keys() == {"pkg", "pkg.mod_a", "pkg.mod_b", "pkg.mod_c"}


def test_dependency_graph_include_filters_imports(pkg_root: Path):
    graph = DependencyGraph(pkg_root, include={"pkg.mod_a", "pkg.mod_b"})
    assert graph.data["pkg.mod_a"] == {"pkg.mod_b"}


def test_dependency_graph_to_json(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    payload = json.loads(graph.to_json())
    assert {entry["name"] for entry in payload} == graph.nodes | {
        importer for importer in graph.data
    } - (graph.nodes - graph.data.keys())
    # every importer entry carries its recorded imports
    by_name = {entry["name"]: set(entry["imports"]) for entry in payload}
    assert by_name["pkg.mod_a"] == {"pkg", "pkg.mod_b", "pkg.mod_c"}


# -- main / CLI -----------------------------------------------------------------


def test_main_rejects_missing_root(tmp_path, monkeypatch):
    missing = tmp_path / "does_not_exist"
    monkeypatch.setattr(sys, "argv", ["betsy", str(missing)])
    with pytest.raises(ValueError):
        main()


def test_main_rejects_non_package(tmp_path, monkeypatch):
    not_pkg = tmp_path / "not_a_package"
    not_pkg.mkdir()
    monkeypatch.setattr(sys, "argv", ["betsy", str(not_pkg)])
    with pytest.raises(ValueError):
        main()


def test_main_writes_output_file(pkg_root: Path, tmp_path: Path, monkeypatch):
    output = tmp_path / "out.html"
    monkeypatch.setattr(
        sys, "argv", ["betsy", str(pkg_root), "-o", str(output)]
    )
    main()

    content = output.read_text()
    assert "pkg.mod_a" in content
    assert "var classes = []" not in content
