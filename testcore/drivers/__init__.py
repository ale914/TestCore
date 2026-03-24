# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Bundled instrument drivers.

Driver resolution: folder name = driver name.
    IADD vsg dryrun        → testcore.drivers.dryrun
    IADD awg agilent33500  → testcore.drivers.agilent33500

External drivers (file path) are still supported:
    IADD vsg ./my_custom_driver.py
"""

import importlib
from ..base_driver import BaseDriver, DriverError

# Package path for driver subpackages
_PACKAGE = "testcore.drivers"


def resolve_driver(name: str) -> type[BaseDriver]:
    """Resolve a driver short name to its BaseDriver subclass.

    Imports testcore.drivers.<name> and finds the first BaseDriver subclass.
    Raises DriverError if not found.
    """
    module_name = f"{_PACKAGE}.{name}"
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        raise DriverError(f"bundled driver '{name}' not found")

    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (isinstance(attr, type) and issubclass(attr, BaseDriver)
                and attr is not BaseDriver):
            return attr

    raise DriverError(f"no BaseDriver subclass in driver '{name}'")


def list_bundled() -> list[str]:
    """List available bundled driver names (folder names)."""
    import pkgutil
    return sorted(
        name for _, name, is_pkg in pkgutil.iter_modules(__path__)
        if is_pkg
    )
