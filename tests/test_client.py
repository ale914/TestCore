# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Simple test client to demonstrate PING/PONG over TCP."""

import socket


def send_ping():
    """Connect to server and send PING command."""
    # RESP format for PING command: *1\r\n$4\r\nPING\r\n
    ping_command = b"*1\r\n$4\r\nPING\r\n"

    # Connect to server
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("127.0.0.1", 6380))  # Using port 6380
        print("Connected to TestCore server at 127.0.0.1:6380")

        # Send PING
        print(f"\nSending PING command: {ping_command}")
        sock.sendall(ping_command)

        # Receive response
        response = sock.recv(1024)
        print(f"Received response: {response}")
        print(f"Decoded response: {response.decode('utf-8').strip()}")

        if response == b"+PONG\r\n":
            print("\n[SUCCESS] Server responded with PONG!")
        else:
            print(f"\n[FAILED] Unexpected response: {response}")

    except ConnectionRefusedError:
        print("[ERROR] Could not connect to server. Is it running?")
        print("Start server with: python -m testcore")
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        sock.close()


if __name__ == "__main__":
    print("=== TestCore PING Test Client ===\n")
    send_ping()
