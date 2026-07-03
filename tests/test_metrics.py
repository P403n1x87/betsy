import ast
from pathlib import Path

import pytest

from betsy import DependencyGraph
from betsy.metrics import ModuleMetrics, _is_abstract_class, compute_metrics


def _classdef(source: str) -> ast.ClassDef:
    (node,) = ast.parse(source).body
    assert isinstance(node, ast.ClassDef)
    return node


# -- _is_abstract_class -------------------------------------------------------


def test_is_abstract_class_plain():
    assert _is_abstract_class(_classdef("class Foo:\n    pass\n")) is False


def test_is_abstract_class_inherits_abc():
    assert _is_abstract_class(_classdef("class Foo(ABC):\n    pass\n")) is True


def test_is_abstract_class_inherits_module_qualified_abc():
    assert _is_abstract_class(_classdef("class Foo(abc.ABC):\n    pass\n")) is True


def test_is_abstract_class_inherits_protocol():
    assert _is_abstract_class(_classdef("class Foo(Protocol):\n    pass\n")) is True


def test_is_abstract_class_metaclass_abcmeta():
    assert _is_abstract_class(_classdef("class Foo(metaclass=ABCMeta):\n    pass\n")) is True


def test_is_abstract_class_has_abstractmethod():
    source = "class Foo:\n    @abstractmethod\n    def bar(self): ...\n"
    assert _is_abstract_class(_classdef(source)) is True


def test_is_abstract_class_has_qualified_abstractmethod():
    source = "class Foo:\n    @abc.abstractmethod\n    def bar(self): ...\n"
    assert _is_abstract_class(_classdef(source)) is True


def test_is_abstract_class_unrelated_base():
    assert _is_abstract_class(_classdef("class Foo(Bar):\n    pass\n")) is False


# -- compute_metrics -----------------------------------------------------------


@pytest.fixture
def pkg_root(tmp_path: Path) -> Path:
    root = tmp_path.resolve()
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # mod_a is depended upon by both mod_b and mod_c (high afferent coupling,
    # no imports of its own -> stable) and defines one abstract class.
    (pkg / "mod_a.py").write_text("from abc import ABC\n\nclass Base(ABC):\n    pass\n")
    (pkg / "mod_b.py").write_text("import pkg.mod_a\n")
    (pkg / "mod_c.py").write_text("import pkg.mod_a\n")
    return pkg


def test_compute_metrics_returns_entry_per_parsed_module(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    metrics = compute_metrics(graph)
    assert metrics.keys() == graph.data.keys()


def test_compute_metrics_afferent_efferent_coupling(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    metrics = compute_metrics(graph)

    assert metrics["pkg.mod_a"].ca == 2
    assert metrics["pkg.mod_a"].ce == 0
    assert metrics["pkg.mod_b"].ca == 0
    assert metrics["pkg.mod_b"].ce == 1


def test_compute_metrics_instability_stable_module(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    metrics = compute_metrics(graph)

    # Depended upon by everyone, depends on nothing -> maximally stable.
    assert metrics["pkg.mod_a"].i == 0.0


def test_compute_metrics_instability_unstable_module(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    metrics = compute_metrics(graph)

    # Depends on something, nothing depends on it -> maximally unstable.
    assert metrics["pkg.mod_b"].i == 1.0


def test_compute_metrics_isolated_module_has_zero_instability(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    metrics = compute_metrics(graph)

    assert metrics["pkg"].ca == 0
    assert metrics["pkg"].ce == 0
    assert metrics["pkg"].i == 0.0


def test_compute_metrics_abstractness(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    metrics = compute_metrics(graph)

    assert metrics["pkg.mod_a"].a == 1.0
    assert metrics["pkg.mod_b"].a == 0.0


def test_compute_metrics_module_with_no_classes_has_zero_abstractness(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    metrics = compute_metrics(graph)

    assert metrics["pkg.mod_b"].a == 0.0


def test_compute_metrics_signed_distance_from_main_sequence(pkg_root: Path):
    graph = DependencyGraph(pkg_root)
    metrics = compute_metrics(graph)

    # a=1.0, i=0.0 -> d = 1 + 0 - 1 = 0 (on the main sequence)
    assert metrics["pkg.mod_a"].d == pytest.approx(0.0)
    # a=0.0, i=1.0 -> d = 0 + 1 - 1 = 0 (on the main sequence too)
    assert metrics["pkg.mod_b"].d == pytest.approx(0.0)


def test_compute_metrics_zone_of_pain(tmp_path: Path):
    # Concrete (a=0) and stable (i=0, i.e. depended-upon, no deps) -> negative D.
    root = tmp_path.resolve() / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "core.py").write_text("")
    (root / "consumer.py").write_text("import pkg.core\n")

    graph = DependencyGraph(root)
    metrics = compute_metrics(graph)

    assert metrics["pkg.core"].d < 0


def test_compute_metrics_zone_of_uselessness(tmp_path: Path):
    # Abstract (a=1) and unstable (i=1, i.e. no dependents, has deps) -> positive D.
    root = tmp_path.resolve() / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("")
    (root / "leaf.py").write_text("")
    (root / "unused_abstract.py").write_text("from abc import ABC\nimport pkg.leaf\n\nclass Base(ABC):\n    pass\n")

    graph = DependencyGraph(root)
    metrics = compute_metrics(graph)

    assert metrics["pkg.unused_abstract"].d > 0


def test_module_metrics_is_frozen_dataclass():
    metrics = ModuleMetrics(name="pkg", ca=0, ce=0, i=0.0, a=0.0, d=-1.0)
    with pytest.raises(AttributeError):
        metrics.ca = 1
