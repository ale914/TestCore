# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""AsyncIO-based TCP server with multi-client support."""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional
from .protocol import RESPParser, RESPProtocolError
from .commands import dispatcher
from .instruments import get_registry

logger = logging.getLogger(__name__)

# Server singleton
_server: TestCoreServer | None = None


def get_server() -> TestCoreServer | None:
    """Get global server instance (None if not started)."""
    return _server


class ClientHandler:
    """Handles individual client connection."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        client_id: int
    ):
        self.reader = reader
        self.writer = writer
        self.client_id = client_id
        self.parser = RESPParser()
        self.running = True
        self.connect_time: float = time.time()
        self.name: str | None = None
        self.cmd_count: int = 0
        self.monitoring: bool = False
        self.subscribing: bool = False

        # Get client address
        peername = writer.get_extra_info('peername')
        self.address = f"{peername[0]}:{peername[1]}" if peername else "unknown"

        logger.info(f"Client {client_id} connected from {self.address}")

    async def _publish_connect_event(self):
        """Publish session connect event."""
        from .events import publish_session_event
        await publish_session_event(
            "connect", self.client_id, address=self.address)

    async def handle(self):
        """Main client handling loop."""
        try:
            await self._publish_connect_event()
            while self.running:
                # Read data from client
                data = await self.reader.read(4096)

                if not data:
                    # Client disconnected
                    logger.info(f"Client {self.client_id} disconnected")
                    break

                # Parse RESP messages
                try:
                    messages = self.parser.feed(data)
                except RESPProtocolError as e:
                    logger.error(f"Client {self.client_id} protocol error: {e}")
                    # Send error and close connection
                    from .protocol import RESPSerializer
                    await self._write(RESPSerializer.error(str(e)))
                    break

                # Process each complete message
                for message in messages:
                    await self._process_message(message)

        except asyncio.CancelledError:
            logger.info(f"Client {self.client_id} handler cancelled")
            raise
        except ConnectionResetError:
            logger.info(f"Client {self.client_id} disconnected (connection reset)")
        except Exception as e:
            logger.error(f"Client {self.client_id} error: {e}", exc_info=True)
        finally:
            await self.close()

    async def _process_message(self, message):
        """Process a single RESP message or inline command."""
        logger.debug(f"Client {self.client_id} received: {message}")

        # Message should be array of strings (command + args)
        if not isinstance(message, list):
            from .protocol import RESPSerializer
            await self._write(RESPSerializer.error("expected array"))
            return

        # Skip empty commands (e.g., from empty lines in inline mode)
        if not message:
            return

        # Convert all elements to strings
        command = [str(item) for item in message]

        self.cmd_count += 1

        # Track server-wide command count
        server = get_server()
        if server:
            server.total_commands += 1

        # Auto-exit MONITOR mode when client sends a non-MONITOR command
        cmd_upper = command[0].upper() if command else ""
        if self.monitoring and cmd_upper != "MONITOR":
            server = get_server()
            if server:
                server.monitors.discard(self)
            self.monitoring = False

        # Dispatch command with session context
        context = {"session_id": self.client_id, "client_handler": self}
        response = await dispatcher.dispatch(command, context)

        # Send response
        await self._write(response)

        # Deferred MONITOR registration: activate AFTER +OK is sent,
        # so monitor broadcasts don't arrive before the client sees +OK
        if getattr(self, "_pending_monitor", False):
            self._pending_monitor = False
            server = get_server()
            if server:
                server.monitors.add(self)
                self.monitoring = True

    async def _write(self, data: bytes):
        """Write data to client."""
        self.writer.write(data)
        await self.writer.drain()

    async def close(self):
        """Close client connection and release all held locks."""
        if not self.running:
            return

        self.running = False

        # Release all locks held by this session (spec §9.1)
        try:
            registry = get_registry()
            for name in registry.list_instruments():
                inst = registry.get(name)
                if inst.lock_owner == self.client_id:
                    try:
                        registry.unlock(name, self.client_id)
                        logger.info(
                            f"Client {self.client_id} disconnect: "
                            f"unlocked {name}")
                        # Publish lock release event
                        from .events import publish_lock_event
                        await publish_lock_event(
                            "released", name, self.client_id,
                            reason="disconnect")
                    except Exception as e:
                        logger.error(
                            f"Client {self.client_id} disconnect: "
                            f"failed to unlock {name}: {e}")
        except Exception as e:
            logger.error(f"Client {self.client_id} disconnect cleanup error: {e}")

        # Publish session disconnect event (spec §9.1)
        try:
            from .events import publish_session_event
            await publish_session_event(
                "disconnect", self.client_id, address=self.address)
        except Exception as e:
            logger.error(
                f"Client {self.client_id} disconnect: "
                f"failed to publish event: {e}")

        # Remove from event subscribers (spec §9.1)
        try:
            from .events import get_event_bus
            bus = get_event_bus()
            bus.unsubscribe_all(self)
        except Exception as e:
            logger.error(
                f"Client {self.client_id} disconnect: "
                f"failed to unsubscribe events: {e}")

        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass  # Connection already closed by client

        logger.info(f"Client {self.client_id} connection closed")


class TestCoreServer:
    """Main TestCore server with multi-client support."""

    def __init__(self, host: str = "127.0.0.1", port: int = 6399,
                 max_clients: int = 64):
        global _server
        self.host = host
        self.port = port
        self.max_clients = max_clients
        self.server: Optional[asyncio.Server] = None
        self.clients: dict[int, asyncio.Task] = {}
        self.client_handlers: dict[int, ClientHandler] = {}
        self.monitors: set[ClientHandler] = set()
        self.next_client_id = 1
        self.running = False
        self.start_time: float = time.time()
        self.total_connections: int = 0
        self.total_commands: int = 0
        self.rejected_connections: int = 0
        _server = self

    async def broadcast_monitors(self, data: bytes):
        """Send data to all MONITOR clients. Remove dead ones."""
        dead = []
        for handler in self.monitors:
            try:
                await handler._write(data)
            except Exception:
                dead.append(handler)
        for handler in dead:
            self.monitors.discard(handler)

    async def start(self):
        """Start the server."""
        self.server = await asyncio.start_server(
            self._client_connected,
            self.host,
            self.port
        )

        self.running = True
        addr = self.server.sockets[0].getsockname()
        logger.info(f"TestCore server started on {addr[0]}:{addr[1]}")

        async with self.server:
            await self.server.serve_forever()

    async def _client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter
    ):
        """Handle new client connection."""
        # Enforce max_clients limit
        if len(self.clients) >= self.max_clients:
            self.rejected_connections += 1
            peername = writer.get_extra_info('peername')
            addr = f"{peername[0]}:{peername[1]}" if peername else "unknown"
            logger.warning(
                f"Rejected connection from {addr}: "
                f"max_clients ({self.max_clients}) reached")
            from .protocol import RESPSerializer
            writer.write(RESPSerializer.error(
                f"max clients reached ({self.max_clients})"))
            writer.close()
            return

        client_id = self.next_client_id
        self.next_client_id += 1
        self.total_connections += 1

        handler = ClientHandler(reader, writer, client_id)
        task = asyncio.create_task(handler.handle())
        self.clients[client_id] = task
        self.client_handlers[client_id] = handler

        # Clean up when task completes
        def _cleanup(_):
            self.clients.pop(client_id, None)
            h = self.client_handlers.pop(client_id, None)
            if h:
                self.monitors.discard(h)

        task.add_done_callback(_cleanup)

    async def stop(self):
        """Stop the server gracefully.

        1. Close all client connections (triggers lock cleanup per client)
        2. Safe-state all remaining instruments (belt-and-suspenders)
        3. Disconnect all instruments
        4. Stop listening
        """
        if not self.running:
            return

        logger.info("Stopping TestCore server...")
        self.running = False

        # Close all client connections (each triggers lock release + safe_state)
        for client_id, task in list(self.clients.items()):
            logger.info(f"Closing client {client_id}")
            task.cancel()

        if self.clients:
            await asyncio.gather(*self.clients.values(), return_exceptions=True)

        # Belt-and-suspenders: safe_state + disconnect ALL instruments
        # regardless of lock state (covers edge cases where client cleanup
        # failed or instruments were added without locks)
        registry = get_registry()
        for name in registry.list_instruments():
            try:
                inst = registry.get(name)
                try:
                    inst.driver.safe_state()
                    logger.info(f"Shutdown: {name} safe_state OK")
                except Exception as e:
                    logger.warning(f"Shutdown: {name} safe_state failed: {e}")
                try:
                    inst.driver.disconnect()
                    logger.info(f"Shutdown: {name} disconnected")
                except Exception as e:
                    logger.warning(f"Shutdown: {name} disconnect failed: {e}")
            except Exception as e:
                logger.error(f"Shutdown: error cleaning up {name}: {e}")

        # Stop server
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        logger.info("TestCore server stopped")


async def run_server(host: str = "127.0.0.1", port: int = 6399,
                     max_clients: int = 64):
    """Run the TestCore server (spec §11: port 6399 to avoid Redis conflicts)."""
    server = TestCoreServer(host, port, max_clients=max_clients)
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await server.stop()
