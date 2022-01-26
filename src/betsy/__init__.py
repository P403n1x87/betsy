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

import ast
import json
import tempfile
import typing as t
from argparse import ArgumentParser
from pathlib import Path

import graphviz

from betsy.assets import INDEX_TEMPLATE

ModulePath = t.Tuple[str, ...]


class ImportVisitor(ast.NodeVisitor):
    def __init__(self, base: ModulePath) -> None:
        super().__init__()
        self.base = base
        self.imports: t.Set[ModulePath] = set()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports |= {tuple(alias.name.split(".")) for alias in node.names}

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module.split(".") if node.module else []
        if not node.level:
            self.imports.add(tuple(module))
        else:
            assert node.level <= len(
                self.base
            ), f"valid relative import from {node.module} at level {node.level} (base {self.base})"
            self.imports.add(tuple(list(self.base)[: -node.level] + module))


def _path_to_module_path(path: Path, root: Path) -> ModulePath:
    module = []

    relpath = path.resolve().relative_to(root.parent)

    if relpath.stem != "__init__":
        module.append(relpath.stem)

    parent = relpath.parent
    while parent.name:
        module.insert(0, parent.name)
        parent = parent.parent

    return tuple(module)


def _get_imports(source: str, base: ModulePath) -> t.Set[ModulePath]:
    visitor = ImportVisitor(base)
    visitor.visit(ast.parse(source))
    return visitor.imports


def get_imports(source_file: Path, root: Path) -> t.Set[str]:
    assert source_file.suffix == ".py"
    with source_file.open() as f:
        return {
            ".".join(module)
            for module in _get_imports(
                f.read(), _path_to_module_path(source_file, root)
            )
        }


def anyprefix(path, prefixes, default):
    return (
        any(path == _ or path.startswith(_ + ".") for _ in prefixes)
        if prefixes is not None
        else default
    )


class DependencyGraph:
    def __init__(
        self,
        root: Path,
        include: t.Optional[t.Set[str]] = None,
        exclude: t.Optional[t.Set[str]] = None,
    ) -> None:
        self.root = root
        self.exclude = exclude
        self.include = include

        data_gen = (
            (".".join(_path_to_module_path(_, root)), get_imports(_, root))
            for _ in root.rglob("*.py")
        )

        self.data = {
            importer: {
                _ for _ in imports if self.is_included(_) and not self.is_excluded(_)
            }
            for importer, imports in data_gen
            if self.is_included(importer) and not self.is_excluded(importer)
        }

        self.nodes = set()
        for importer, imports in self.data.items():
            self.nodes.add(importer)
            self.nodes |= imports

    def is_excluded(self, path):
        return anyprefix(path, self.exclude, False)

    def is_included(self, path):
        return anyprefix(path, self.include, True)

    def to_json(self):
        return json.dumps(
            [
                {"name": importer, "imports": list(imports)}
                for importer, imports in self.data.items()
            ]
        )

    def export(self, file: Path) -> None:
        with file.open() as f:
            f.write(self.to_json())

    def render(self, dest: str) -> None:
        graph = graphviz.Digraph(comment=f"The {self.root.name} package")
        internal_nodes = {_ for _ in self.nodes if _.startswith(self.root.name + ".")}
        # external_nodes = self.nodes - internal_nodes

        graph.attr("node", style="filled", color="lightgreen")
        graph.node(self.root.name)

        internal_edges = graphviz.Digraph(name="internal")
        internal_edges.attr(color="lightblue")
        internal_edges.attr("node", style="filled", color="lightblue")

        graph.attr("node", style="filled", color="lightblue")
        for node in internal_nodes:
            graph.node(node)
        # graph.attr("node", style="filled", color="lightgray")
        # for node in external_nodes:
        #     graph.node(node)

        for importer, imports in self.data.items():
            for imp in imports:
                if imp == self.root.name or imp.startswith(self.root.name + "."):
                    internal_edges.edge(imp, importer)
                # else:
                #     graph.edge(imp, importer)

        graph.subgraph(internal_edges)

        graph.render(dest, view=True)


def main():
    argp = ArgumentParser(
        prog="betsy", description="Incy-wincy Python project dependencies crawler"
    )

    argp.add_argument(
        "root",
        type=Path,
        help="The module or package to crawl",
    )
    argp.add_argument(
        "-o",
        "--output",
        type=Path,
        help="The output file. Defaults to a temporary file",
    )

    def setify(string):
        return {_.strip() for _ in string.split(",")} if string is not None else None

    argp.add_argument(
        "-I",
        "--include",
        type=setify,
        help="List of module paths to include",
    )
    argp.add_argument(
        "-X",
        "--exclude",
        type=setify,
        help="List of module paths to exclude",
    )

    args = argp.parse_args()

    if not args.root.is_dir():
        raise ValueError(f"{args.root} does not exist")
    if not (args.root / "__init__.py").is_file():
        raise ValueError(f"{args.root} is not a Python package")

    graph = DependencyGraph(
        args.root.resolve(), include=args.include, exclude=args.exclude
    )

    if args.output is None:
        output = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False)
        print(f"file://{output.name}")
    else:
        output = open(args.output.with_suffix(".html"), "w")

    with output:
        page = INDEX_TEMPLATE.replace(
            "var classes = []", f"var classes = {graph.to_json()}"
        )
        diameter = 600 + len(graph.data) * 4
        page = page.replace("var diameter = 800", f"var diameter = {diameter}")
        output.write(page)


if __name__ == "__main__":
    main()
