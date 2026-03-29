# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Instrument registry and single-axis state machine (spec §6.3, §7.3).

States: IDLE → LOCKED → READY → FAULT/UNRESPONSIVE
Lock ownership is embedded in the state (IDLE = no owner, all others = owned).

Driver calls are offloaded to a thread executor with a watchdog timeout
(spec §7.4). If timeout fires, instrument → UNRESPONSIVE.
"""

from __future__ import annotations
import asyncio
import collections
import importlib.util
import logging
import time
from enum import Enum
from dataclasses import dataclass, field
from .base_driver import BaseDriver, DriverError

logger = logging.getLogger(__name__)


class InstrumentState(Enum):
    """Instrument lifecycle states (spec §6.3.1). Single axis."""
    IDLE = "IDLE"                  # connected, no owner
    LOCKED = "LOCKED"              # owned, not yet initialized
    READY = "READY"                # owned, initialized and operational
    UNRESPONSIVE = "UNRESPONSIVE"  # owned, driver call timed out
    FAULT = "FAULT"                # owned, unexpected driver exception


@dataclass
class Instrument:
    """A physical instrument instance with state."""
    name: str
    driver: BaseDriver
    driver_path: str
    connect_config: dict
    transport_opts: dict = field(default_factory=dict)
    state: InstrumentState = InstrumentState.IDLE
    resources: list[str] = field(default_factory=list)
    lock_owner: int | None = None  # session_id
    total_calls: int = 0
    total_errors: int = 0
    response_times: collections.deque = field(default=None)
    health_interval: float | None = None
    health_failures: int = 0
    last_call_ok: float = field(default_factory=time.monotonic)
    _busy: bool = field(default=False, repr=False)

    def __post_init__(self):
        if self.response_times is None:
            self.response_times = collections.deque(maxlen=1000)

    @property
    def mean_response_ms(self) -> float:
        if not self.response_times:
            return 0.0
        return sum(self.response_times) / len(self.response_times) * 1000


class DriverModule:
    """A loaded driver Python module (reusable across instruments)."""

    def __init__(self, path: str, driver_class: type[BaseDriver]):
        self.path = path
        self.driver_class = driver_class


class InstrumentRegistry:
    """Registry for all instruments and loaded driver modules."""

    # Slow operations (init, configure, reset, load, save) get a longer timeout
    SLOW_TIMEOUT_MULTIPLIER = 12  # 5s * 12 = 60s default

    def __init__(self, driver_timeout: float = 5.0):
        self._instruments: dict[str, Instrument] = {}
        self._driver_modules: dict[str, DriverModule] = {}  # path -> module
        self._driver_timeout = driver_timeout
        self._slow_timeout = driver_timeout * self.SLOW_TIMEOUT_MULTIPLIER

    def _is_file_path(self, name: str) -> bool:
        """Check if name looks like a file path (vs a bundled driver name)."""
        return '/' in name or '\\' in name or name.endswith('.py')

    def _load_driver_module(self, name: str) -> DriverModule:
        """Load driver module by name or file path, caching by key.

        Short name (e.g. 'dryrun') → bundled driver from testcore.drivers.<name>
        File path (e.g. './my_driver.py') → loaded via importlib from file
        """
        if name in self._driver_modules:
            return self._driver_modules[name]

        if self._is_file_path(name):
            driver_class = self._load_from_file(name)
        else:
            from .drivers import resolve_driver
            driver_class = resolve_driver(name)

        dm = DriverModule(name, driver_class)
        self._driver_modules[name] = dm
        return dm

    def _load_from_file(self, path: str) -> type[BaseDriver]:
        """Load a driver class from a Python file path."""
        try:
            spec = importlib.util.spec_from_file_location("driver_module", path)
            if spec is None or spec.loader is None:
                raise DriverError(f"cannot load module from {path}")

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except DriverError:
            raise
        except Exception as e:
            raise DriverError(f"cannot load module from {path}: {e}")

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (isinstance(attr, type) and issubclass(attr, BaseDriver)
                    and attr is not BaseDriver):
                return attr

        raise DriverError(f"no BaseDriver subclass found in {path}")

    def add(self, name: str, driver_name: str,
            address: str | None = None,
            config: dict | None = None,
            transport_opts: dict | None = None) -> Instrument:
        """Add instrument (IADD). Opens transport, connects driver. State → IDLE.

        Args:
            name: Instrument alias (e.g. 'awg', 'scope')
            driver_name: Bundled driver name or file path
            address: Instrument address (VISA, TCP host:port, COM port, or None)
            config: Extra driver-specific settings (merged into connect config)
            transport_opts: Transport overrides (baudrate, timeout, parity, etc.)
        """
        if name in self._instruments:
            raise DriverError(f"instrument '{name}' already exists")

        dm = self._load_driver_module(driver_name)
        driver = dm.driver_class()

        # Build config: driver-specific settings + transport from address
        connect_config = dict(config) if config else {}
        if address:
            from .transport import resolve_transport
            connect_config["address"] = address
            connect_config["transport"] = resolve_transport(
                address, **(transport_opts or {}))

        driver.connect(connect_config)

        inst = Instrument(
            name=name,
            driver=driver,
            driver_path=driver_name,
            connect_config=connect_config,
            transport_opts=dict(transport_opts) if transport_opts else {},
        )
        self._instruments[name] = inst
        return inst

    def remove(self, name: str) -> None:
        """Remove instrument (INSTRUMENT.REMOVE). From any state."""
        inst = self._get(name)
        try:
            inst.driver.safe_state()
        except Exception as e:
            logger.warning(f"safe_state failed during remove of {name}: {e}")
        try:
            inst.driver.disconnect()
        except Exception as e:
            logger.warning(f"disconnect failed during remove of {name}: {e}")
        del self._instruments[name]

    def lock(self, name: str, session_id: int) -> None:
        """Lock instrument (LOCK). IDLE → LOCKED."""
        inst = self._get(name)
        if inst.state != InstrumentState.IDLE:
            if inst.lock_owner is not None and inst.lock_owner != session_id:
                raise LockedError(f"{name} owned by session {inst.lock_owner}")
            if inst.lock_owner == session_id:
                return  # already locked by this session
            raise DriverError(f"instrument '{name}' is not IDLE")
        inst.state = InstrumentState.LOCKED
        inst.lock_owner = session_id

    def unlock(self, name: str, session_id: int) -> None:
        """Unlock instrument (UNLOCK). Any owned state → IDLE."""
        inst = self._get(name)
        if inst.lock_owner is None:
            raise IdleError(f"{name} not locked")
        if inst.lock_owner != session_id:
            raise LockedError(f"{name} owned by session {inst.lock_owner}")
        try:
            inst.driver.safe_state()
        except Exception as e:
            logger.error(
                f"safe_state FAILED during unlock of {name}: {e} → FAULT")
            inst.state = InstrumentState.FAULT
            # Lock is released but instrument is not safe — needs IRESET
            inst.lock_owner = None
            inst.resources = []
            return
        inst.state = InstrumentState.IDLE
        inst.lock_owner = None
        inst.resources = []

    async def _call_driver(self, inst: Instrument, func, *args,
                           timeout: float | None = None):
        """Execute a synchronous driver call in a thread with watchdog timeout.

        Spec §7.4: every driver method call is wrapped in asyncio.wait_for()
        with configurable timeout. If timeout fires, instrument → UNRESPONSIVE.

        Args:
            timeout: Override timeout in seconds. Defaults to _driver_timeout.
        """
        t = timeout if timeout is not None else self._driver_timeout
        inst._busy = True
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(func, *args),
                timeout=t,
            )
            inst.last_call_ok = time.monotonic()
            return result
        except asyncio.TimeoutError:
            inst.state = InstrumentState.UNRESPONSIVE
            inst.total_errors += 1
            logger.error(
                f"Driver timeout on {inst.name}: "
                f"{func.__name__}() exceeded {t}s → UNRESPONSIVE")
            from .events import publish_instrument_event
            asyncio.create_task(
                publish_instrument_event("UNRESPONSIVE", inst.name,
                                         reason="driver_timeout"))
            raise DriverError(
                f"driver timeout ({t}s) → UNRESPONSIVE")
        finally:
            inst._busy = False

    async def init_instrument(self, name: str,
                              config_path: str | None = None,
                              selftest: bool = False) -> None:
        """Full init (INSTRUMENT.INIT). Requires LOCKED or READY state."""
        inst = self._get(name)
        if inst.state == InstrumentState.IDLE:
            raise IdleError(f"{name} not locked")
        if inst.state in (InstrumentState.FAULT, InstrumentState.UNRESPONSIVE):
            raise FaultError(f"{name} is {inst.state.value}, requires INSTRUMENT.RESET")
        if config_path:
            await self._call_driver(inst, inst.driver.configure, config_path,
                                    timeout=self._slow_timeout)
        await self._call_driver(inst, inst.driver.init, selftest,
                                timeout=self._slow_timeout)
        resources = await self._call_driver(inst, inst.driver.discover)
        inst.resources = resources
        inst.state = InstrumentState.READY

    async def align(self, name: str) -> None:
        """Accept current state (ALIGN). Requires LOCKED state."""
        inst = self._get(name)
        if inst.state != InstrumentState.LOCKED:
            if inst.state == InstrumentState.IDLE:
                raise IdleError(f"{name} not locked")
            if inst.state == InstrumentState.READY:
                raise DriverError(f"instrument '{name}' is already READY")
            raise FaultError(f"{name} is {inst.state.value}, requires INSTRUMENT.RESET")
        resources = await self._call_driver(inst, inst.driver.discover)
        inst.resources = resources
        inst.state = InstrumentState.READY

    async def reset(self, name: str) -> None:
        """Reset instrument (INSTRUMENT.RESET). FAULT/UNRESPONSIVE → LOCKED."""
        inst = self._get(name)
        if inst.state not in (InstrumentState.UNRESPONSIVE, InstrumentState.FAULT):
            raise DriverError(f"instrument '{name}' is not in FAULT/UNRESPONSIVE state")
        try:
            inst.driver.disconnect()
        except Exception as e:
            logger.warning(f"disconnect failed during reset of {name}: {e}")

        # Re-create transport if address is present (old one was closed)
        address = inst.connect_config.get("address")
        if address:
            from .transport import resolve_transport
            inst.connect_config["transport"] = resolve_transport(
                address, **inst.transport_opts)

        inst.driver.connect(inst.connect_config)
        inst.state = InstrumentState.LOCKED
        inst.resources = []

    async def _tracked_call(self, inst: Instrument, func, *args,
                            timeout=None):
        """Call driver method with timing, error tracking, and FAULT handling."""
        start = time.perf_counter()
        try:
            result = await self._call_driver(inst, func, *args,
                                             timeout=timeout)
            inst.total_calls += 1
            inst.response_times.append(time.perf_counter() - start)
            return result
        except DriverError:
            inst.total_errors += 1
            raise
        except Exception as e:
            inst.total_errors += 1
            inst.state = InstrumentState.FAULT
            logger.error(f"FAULT on {inst.name}: {e}")
            from .events import publish_instrument_event
            asyncio.create_task(
                publish_instrument_event("FAULT", inst.name,
                                         reason=str(e)))
            raise DriverError(str(e))

    async def read(self, name: str, resource: str) -> str:
        """Read resource value. Requires READY state."""
        inst = self._get(name)
        self._check_ready(inst)
        return await self._tracked_call(inst, inst.driver.read, resource)

    async def write(self, name: str, resource: str, value: str) -> None:
        """Write resource value. Requires READY state."""
        inst = self._get(name)
        self._check_ready(inst)
        await self._tracked_call(inst, inst.driver.write, resource, value)

    async def passthrough(self, name: str, command: str) -> str:
        """Raw command passthrough. Requires READY state."""
        inst = self._get(name)
        self._check_ready(inst)
        return await self._tracked_call(inst, inst.driver.passthrough, command)

    async def save(self, name: str, target: str, file_path: str) -> str:
        """Save data from instrument to file. Requires READY state."""
        inst = self._get(name)
        self._check_ready(inst)
        return await self._tracked_call(
            inst, inst.driver.save, target, file_path,
            timeout=self._slow_timeout)

    async def load(self, name: str, target: str, file_path: str) -> str:
        """Load data from file into instrument. Requires READY state."""
        inst = self._get(name)
        self._check_ready(inst)
        return await self._tracked_call(
            inst, inst.driver.load, target, file_path,
            timeout=self._slow_timeout)

    async def wait_complete(self, name: str) -> None:
        """Wait for pending operations to complete (IWAIT). Requires READY state."""
        inst = self._get(name)
        self._check_ready(inst)
        await self._tracked_call(inst, inst.driver.wait_complete,
                                 timeout=self._slow_timeout)

    async def ping(self, name: str) -> str:
        """Send *IDN? to instrument. Works in any state except FAULT/UNRESPONSIVE."""
        inst = self._get(name)
        if inst.state in (InstrumentState.FAULT, InstrumentState.UNRESPONSIVE):
            raise FaultError(f"{name} is {inst.state.value}")
        return await self._call_driver(inst, inst.driver.passthrough, "*IDN?")

    def get(self, name: str) -> Instrument:
        """Get instrument by name."""
        return self._get(name)

    def list_instruments(self) -> list[str]:
        """List all instrument names."""
        return sorted(self._instruments.keys())

    def list_drivers(self) -> list[str]:
        """List all loaded driver names (short names for bundled, paths for external)."""
        return sorted(self._driver_modules.keys())

    @staticmethod
    def list_bundled_drivers() -> list[str]:
        """List available bundled driver names (folder names under testcore/drivers/)."""
        from .drivers import list_bundled
        return list_bundled()

    def _get(self, name: str) -> Instrument:
        inst = self._instruments.get(name)
        if inst is None:
            raise DriverError(f"instrument '{name}' not found")
        return inst

    def _check_ready(self, inst: Instrument) -> None:
        if inst.state == InstrumentState.IDLE:
            raise IdleError(f"{inst.name} not locked")
        if inst.state == InstrumentState.LOCKED:
            raise NotInitError(f"{inst.name} requires INIT or ALIGN")
        if inst.state in (InstrumentState.UNRESPONSIVE, InstrumentState.FAULT):
            raise FaultError(f"{inst.name} is {inst.state.value}, requires INSTRUMENT.RESET")


class IdleError(Exception):
    """Instrument is IDLE (no owner). Returns -IDLE to client."""
    pass


class NotInitError(Exception):
    """Instrument is LOCKED but not initialized. Returns -NOTINIT to client."""
    pass


class LockedError(Exception):
    """Instrument is locked by another session. Returns -LOCKED to client."""
    pass


class FaultError(Exception):
    """Instrument is FAULT/UNRESPONSIVE. Returns -FAULT to client."""
    pass


# Global singleton
_registry: InstrumentRegistry | None = None


def get_registry(driver_timeout: float | None = None) -> InstrumentRegistry:
    """Get global instrument registry.

    Args:
        driver_timeout: Watchdog timeout in seconds (only used on first call).
    """
    global _registry
    if _registry is None:
        _registry = InstrumentRegistry(
            driver_timeout=driver_timeout or 5.0)
    return _registry
