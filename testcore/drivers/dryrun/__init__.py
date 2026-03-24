# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""DryRun driver - simulates hardware for testing (spec §7.5.1)."""

from testcore import BaseDriver, DriverError


class DryRunDriver(BaseDriver):
    """Simulates hardware with configurable resources."""

    def connect(self, config):
        self._config = config
        self._resources = config.get("resources", ["CH1", "CH2", "VOUT", "FREQ"])
        self._state = {r: "0.0" for r in self._resources}
        self._info = {
            "vendor": config.get("vendor", "TestCore"),
            "model": config.get("model", "DryRun"),
            "serial": config.get("serial", "000000"),
            "version": "0.3.0",
        }

    def disconnect(self):
        self._state.clear()

    def init(self):
        for r in self._state:
            self._state[r] = "0.0"

    def configure(self, config_path):
        pass

    def discover(self):
        return list(self._state.keys())

    def read(self, resource):
        if resource not in self._state:
            raise DriverError(f"unknown resource: {resource}")
        return self._state[resource]

    def write(self, resource, value):
        if resource not in self._state:
            raise DriverError(f"unknown resource: {resource}")
        self._state[resource] = value

    def load(self, target, file_path):
        try:
            with open(file_path, "r") as f:
                count = sum(1 for line in f
                            if line.strip() and not line.startswith("#"))
        except FileNotFoundError:
            raise DriverError(f"file not found: {file_path}")
        self._state[f"_loaded:{target}"] = file_path
        return f"{count} points loaded"

    def save(self, target, file_path):
        if target == "SCREEN":
            # Simulate screenshot: write a small fake PNG header
            data = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
            with open(file_path, "wb") as f:
                f.write(data)
            return f"{len(data)} bytes saved"
        elif target == "DATA":
            # Simulate trace data export
            lines = [f"{r},{self._state[r]}" for r in self._state]
            with open(file_path, "w") as f:
                f.write("\n".join(lines) + "\n")
            return f"{len(lines)} rows saved"
        else:
            raise DriverError(f"unknown save target: {target}")

    def passthrough(self, command):
        return f"DRYRUN_ECHO: {command}"

    def safe_state(self):
        for r in self._state:
            self._state[r] = "0.0"

    def info(self):
        return dict(self._info)
