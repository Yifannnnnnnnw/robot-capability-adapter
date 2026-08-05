"""Isolated loader for statically validated generated modules.

Generated G2/G3 modules use one exact relative import from their private
``_kinematics`` support module.  They must not be imported under the public
``generated_capability_package`` name because a user module with that name may
already exist.  Instead, every load gets a fresh synthetic package namespace
whose search path is pinned to the validated package directory.  All temporary
``sys.modules`` entries are removed before this function returns, including on
an import failure.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import types
import uuid
from pathlib import Path
from types import ModuleType


class GeneratedModuleLoadError(RuntimeError):
    """Raised when a generated module cannot be loaded in isolation."""


def load_isolated_generated_module(
    package_root: str | Path,
    module_name: str,
    *,
    namespace_label: str,
) -> ModuleType:
    """Load one generated layer in a one-use synthetic package namespace.

    The synthetic package deliberately has no loader, so its ``__init__.py`` is
    not executed.  Relative imports resolve only through the generated package
    directory recorded in ``__path__``.  Returning the module after namespace
    cleanup is safe because imported helper functions remain referenced by the
    module globals; generated code is statically forbidden from lazy imports.
    """

    if module_name not in {"g1", "g2", "g3"}:
        raise GeneratedModuleLoadError(
            f"generated module name is not loadable: {module_name!r}"
        )

    root = Path(package_root).resolve()
    package_dir = root / "generated_capability_package"
    module_path = package_dir / f"{module_name}.py"
    support_path = package_dir / "_kinematics.py"
    unsafe_path = (
        package_dir.is_symlink()
        or module_path.is_symlink()
        or (module_name in {"g2", "g3"} and support_path.is_symlink())
    )
    if (
        unsafe_path
        or not package_dir.is_dir()
        or not module_path.is_file()
        or package_dir.resolve() != package_dir
        or module_path.resolve().parent != package_dir
        or (
            module_name in {"g2", "g3"}
            and (
                not support_path.is_file()
                or support_path.resolve().parent != package_dir
            )
        )
    ):
        raise GeneratedModuleLoadError(
            "generated module is missing or has an unsafe filesystem binding: "
            f"generated_capability_package/{module_name}.py"
        )

    while True:
        synthetic_package = f"_soarm_{namespace_label}_{uuid.uuid4().hex}"
        if not any(
            name == synthetic_package or name.startswith(synthetic_package + ".")
            for name in sys.modules
        ):
            break

    package_module = types.ModuleType(synthetic_package)
    package_module.__file__ = str(package_dir / "__init__.py")
    package_module.__package__ = synthetic_package
    package_module.__path__ = [str(package_dir)]
    package_spec = importlib.machinery.ModuleSpec(
        synthetic_package,
        loader=None,
        is_package=True,
    )
    package_spec.submodule_search_locations = [str(package_dir)]
    package_module.__spec__ = package_spec

    qualified_name = f"{synthetic_package}.{module_name}"
    spec = importlib.util.spec_from_file_location(qualified_name, module_path)
    if spec is None or spec.loader is None:
        raise GeneratedModuleLoadError(
            f"cannot create import spec for generated module {module_name!r}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[synthetic_package] = package_module
    sys.modules[qualified_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        for loaded_name in tuple(sys.modules):
            if loaded_name == synthetic_package or loaded_name.startswith(
                synthetic_package + "."
            ):
                sys.modules.pop(loaded_name, None)
