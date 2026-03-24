# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Integration tests for multi-client server."""

import asyncio
import pytest
from testcore.server import TestCoreServer
from testcore.protocol import RESPParser, RESPSerializer


class TestServer:
    """Integration tests for TestCore server."""

    @pytest.fixture
    async def server(self):
        """Start server for testing."""
        server = TestCoreServer(host="127.0.0.1", port=0)  # Random port

        # Start server in background
        server_task = asyncio.create_task(server.start())

        # Wait for server to be ready
        await asyncio.sleep(0.1)

        # Get the actual port
        actual_port = server.server.sockets[0].getsockname()[1]
        server.test_port = actual_port

        yield server

        # Cleanup
        await server.stop()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

    async def _connect_client(self, host: str, port: int):
        """Helper to connect a client."""
        reader, writer = await asyncio.open_connection(host, port)
        return reader, writer

    async def _send_command(self, writer, command: list[str]):
        """Helper to send RESP command."""
        data = RESPSerializer.array(command)
        writer.write(data)
        await writer.drain()

    async def _read_response(self, reader) -> str:
        """Helper to read RESP response."""
        parser = RESPParser()
        while True:
            data = await reader.read(1024)
            if not data:
                raise ConnectionError("Connection closed")

            messages = parser.feed(data)
            if messages:
                return messages[0]

    @pytest.mark.asyncio
    async def test_single_client_ping(self, server):
        """Test single client PING/PONG."""
        reader, writer = await self._connect_client("127.0.0.1", server.test_port)

        try:
            await self._send_command(writer, ["PING"])
            response = await self._read_response(reader)
            assert response == "PONG"
        finally:
            writer.close()
            await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_multiple_pings_same_client(self, server):
        """Test multiple PINGs from same client."""
        reader, writer = await self._connect_client("127.0.0.1", server.test_port)

        try:
            for _ in range(5):
                await self._send_command(writer, ["PING"])
                response = await self._read_response(reader)
                assert response == "PONG"
        finally:
            writer.close()
            await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_multiple_concurrent_clients(self, server):
        """Test multiple clients sending PING concurrently."""
        num_clients = 10
        pings_per_client = 5

        async def client_task(client_id: int):
            """Single client task."""
            reader, writer = await self._connect_client(
                "127.0.0.1",
                server.test_port
            )

            try:
                results = []
                for i in range(pings_per_client):
                    await self._send_command(writer, ["PING"])
                    response = await self._read_response(reader)
                    results.append(response)

                return results
            finally:
                writer.close()
                await writer.wait_closed()

        # Run all clients concurrently
        tasks = [client_task(i) for i in range(num_clients)]
        results = await asyncio.gather(*tasks)

        # Verify all responses
        for client_results in results:
            assert len(client_results) == pings_per_client
            assert all(r == "PONG" for r in client_results)

    @pytest.mark.asyncio
    async def test_interleaved_client_commands(self, server):
        """Test interleaved commands from multiple clients."""
        # Connect 3 clients
        clients = []
        for _ in range(3):
            reader, writer = await self._connect_client(
                "127.0.0.1",
                server.test_port
            )
            clients.append((reader, writer))

        try:
            # Send PING from each client in round-robin
            for _ in range(3):
                for reader, writer in clients:
                    await self._send_command(writer, ["PING"])
                    response = await self._read_response(reader)
                    assert response == "PONG"
        finally:
            for reader, writer in clients:
                writer.close()
                await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_client_disconnect(self, server):
        """Test client disconnect handling."""
        reader, writer = await self._connect_client("127.0.0.1", server.test_port)

        # Send PING
        await self._send_command(writer, ["PING"])
        response = await self._read_response(reader)
        assert response == "PONG"

        # Close connection
        writer.close()
        await writer.wait_closed()

        # Verify server still works with new client
        reader2, writer2 = await self._connect_client("127.0.0.1", server.test_port)
        try:
            await self._send_command(writer2, ["PING"])
            response = await self._read_response(reader2)
            assert response == "PONG"
        finally:
            writer2.close()
            await writer2.wait_closed()

    @pytest.mark.asyncio
    async def test_unknown_command(self, server):
        """Test unknown command returns error."""
        reader, writer = await self._connect_client("127.0.0.1", server.test_port)

        try:
            await self._send_command(writer, ["UNKNOWN"])
            data = await reader.read(1024)
            # Should get error response
            assert data.startswith(b"-ERR")
        finally:
            writer.close()
            await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_rapid_connect_disconnect(self, server):
        """Test rapid client connections and disconnections."""
        for _ in range(20):
            reader, writer = await self._connect_client(
                "127.0.0.1",
                server.test_port
            )
            await self._send_command(writer, ["PING"])
            response = await self._read_response(reader)
            assert response == "PONG"
            writer.close()
            await writer.wait_closed()

    @pytest.mark.asyncio
    async def test_concurrent_mixed_commands(self, server):
        """Test concurrent clients with mixed valid and invalid commands."""
        async def mixed_client():
            reader, writer = await self._connect_client(
                "127.0.0.1",
                server.test_port
            )

            try:
                # Valid PING
                await self._send_command(writer, ["PING"])
                response = await self._read_response(reader)
                assert response == "PONG"

                # Invalid command
                await self._send_command(writer, ["INVALID"])
                data = await reader.read(1024)
                assert data.startswith(b"-ERR")

                # Another valid PING
                await self._send_command(writer, ["PING"])
                response = await self._read_response(reader)
                assert response == "PONG"
            finally:
                writer.close()
                await writer.wait_closed()

        # Run 5 clients concurrently
        await asyncio.gather(*[mixed_client() for _ in range(5)])
