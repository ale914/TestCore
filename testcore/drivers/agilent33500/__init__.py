# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Agilent 33500 Series Waveform Generator driver.

Supports: 33521A (1-ch), 33522A (2-ch)
Reference: Agilent 33500 Series User's Guide (33520-90001)
"""

from .driver import Agilent33500Driver
from .resources import RESOURCES

# Backward compat: tests import _RESOURCES
_RESOURCES = RESOURCES

__all__ = ["Agilent33500Driver", "RESOURCES"]
