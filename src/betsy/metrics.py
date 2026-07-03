# This file is part of "betsy" which is released under GPL.
#
# See file LICENCE or go to http://www.gnu.org/licenses/ for full license
# details.
#
# Betsy is a Python static dependency visualiser tool.
#
# Copyright (c) 2022 Gabriele N. Tornetta <phoenix1987@gmail.com>.
# All rights reserved.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Robert Martin-style component coupling metrics for a :class:`~betsy.DependencyGraph`.

These are exposed as a standalone, public API so that consumers of the
library can compute afferent/efferent coupling, instability, abstractness
and the (signed) distance from the main sequence without generating a
visualisation.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

from betsy import DependencyGraph

_ABSTRACT_BASE_NAMES = {"ABC", "ABCMeta", "Protocol"}
_ABSTRACT_METHOD_DECORATOR_NAMES = {"abstractmethod", "abstractproperty", "abstractclassmethod", "abstractstaticmethod"}


def _name_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_abstract_class(node: ast.ClassDef) -> bool:
    if any(_name_of(base) in _ABSTRACT_BASE_NAMES for base in node.bases):
        return True

    for keyword in node.keywords:
        if keyword.arg == "metaclass" and _name_of(keyword.value) == "ABCMeta":
            return True

    for child in node.body:
        if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if any(_name_of(_) in _ABSTRACT_METHOD_DECORATOR_NAMES for _ in child.decorator_list):
            return True

    return False


def _class_stats(source: str) -> tuple[int, int]:
    """Return (total classes, abstract classes) defined at any nesting level."""
    total = abstract = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef):
            total += 1
            abstract += _is_abstract_class(node)
    return total, abstract


def _module_class_stats(module: str, root: Path) -> tuple[int, int]:
    base = root.parent / Path(*module.split("."))
    for candidate in (base / "__init__.py", base.with_suffix(".py")):
        if candidate.is_file():
            return _class_stats(candidate.read_text())
    return 0, 0


@dataclass(frozen=True)
class ModuleMetrics:
    """Coupling metrics for a single module.

    Attributes:
        ca: Afferent coupling -- number of modules in the graph that depend on this one.
        ce: Efferent coupling -- number of modules this one depends on.
        i: Instability, ``ce / (ca + ce)``. ``0.0`` for a module with no incoming or outgoing edges.
        a: Abstractness, the fraction of classes defined in the module that are abstract.
            ``0.0`` for a module with no classes.
        d: Signed distance from the main sequence, ``a + i - 1``.
            Ranges over ``[-1, 1]``. Negative values sit in the "zone of pain"
            (concrete and stable); positive values sit in the "zone of
            uselessness" (abstract and unstable).
    """

    name: str
    ca: int
    ce: int
    i: float
    a: float
    d: float


def compute_metrics(graph: DependencyGraph) -> dict[str, ModuleMetrics]:
    """Compute :class:`ModuleMetrics` for every module in ``graph``.

    Only modules that were actually parsed from source (i.e. keys of
    ``graph.data``) get an entry: external/unresolved imports have no
    class information to derive abstractness from.
    """
    afferent: dict[str, int] = {name: 0 for name in graph.data}
    for importer, imports in graph.data.items():
        for imported in imports:
            if imported in afferent and imported != importer:
                afferent[imported] += 1

    metrics: dict[str, ModuleMetrics] = {}
    for name, imports in graph.data.items():
        ca = afferent[name]
        # Only count dependencies on other modules in the graph: external
        # (e.g. stdlib/third-party) imports carry no coupling information
        # of their own, so including them would skew instability.
        ce = len({_ for _ in imports if _ in graph.data} - {name})
        i = ce / (ca + ce) if ca + ce else 0.0

        total_classes, abstract_classes = _module_class_stats(name, graph.root)
        a = abstract_classes / total_classes if total_classes else 0.0

        metrics[name] = ModuleMetrics(name=name, ca=ca, ce=ce, i=i, a=a, d=a + i - 1)

    return metrics
