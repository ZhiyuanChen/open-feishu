# open-feishu
# Copyright (C) 2024-Present  Zhiyuan Chen

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Generate the code reference pages and navigation."""

from pathlib import Path

import mkdocs_gen_files

project = "feishu"

nav = mkdocs_gen_files.Nav()

root = Path(__file__).parent.parent
src = root / project


def depth_and_name_key(path):
    depth = len(path.parts)
    if "utils" in path.parts:
        return (depth, 1, path.parts)
    if "third_party" in path.parts:
        return (depth, 2, path.parts)
    if "exceptions" in path.parts:
        return (depth, 3, path.parts)
    return (depth, 0, path.parts)


for path in sorted(src.rglob("*.py"), key=depth_and_name_key):
    if path.name.startswith("_") and path.name != "__init__.py":
        continue
    module_path = path.relative_to(root).with_suffix("")
    readme_path = path.relative_to(root).with_suffix(".md")
    doc_path = path.relative_to(src).with_suffix(".md")
    full_doc_path = Path(doc_path)

    parts = tuple(module_path.parts)

    if parts[-1] == "__init__":
        parts = parts[:-1]
        doc_path = doc_path.with_name("index.md")
        full_doc_path = full_doc_path.with_name("index.md")
        readme_path = readme_path.with_name("README.md")
    elif parts[-1] == "__main__":
        continue

    with mkdocs_gen_files.open(full_doc_path, "w") as fd:
        if readme_path.exists():
            with open(readme_path, encoding="utf8") as rd:
                fd.write(rd.read())
        if len(parts) == 1:
            nav[parts] = doc_path.as_posix()
        else:
            nav[parts[1:]] = doc_path.as_posix()
            ident = ".".join(parts)
            fd.write(f"::: {ident}")

        mkdocs_gen_files.set_edit_path(full_doc_path, path.relative_to(root))

nav[("about", "index")] = "about/index.md"
nav[("about", "license")] = "about/license.md"
nav[("about", "privacy")] = "about/privacy.md"

with mkdocs_gen_files.open("SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
