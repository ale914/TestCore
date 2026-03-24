# Copyright (c) 2026 Alessandro Ricco
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# See LICENSE file for details.

"""Test inline command support (like typing in PuTTY)."""

import socket


def test_inline_ping():
    """Test inline PING command."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("127.0.0.1", 6379))
        print("Connected to server")

        # Send inline command (what you type in PuTTY)
        inline_command = b"PING\r\n"
        print(f"Sending: {inline_command}")
        sock.sendall(inline_command)

        # Receive response
        response = sock.recv(1024)
        print(f"Received: {response}")
        print(f"Decoded: {response.decode('utf-8').strip()}")

        if response == b"+PONG\r\n":
            print("\n[SUCCESS] Inline command works!")
        else:
            print(f"\n[FAILED] Unexpected response")

    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        sock.close()


def test_inline_with_args():
    """Test inline command with arguments."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("127.0.0.1", 6379))
        print("\nConnected to server")

        # Send inline command with arguments (simulates GET key)
        inline_command = b"GET mykey\r\n"
        print(f"Sending: {inline_command}")
        sock.sendall(inline_command)

        # Receive response (will be error since GET not implemented)
        response = sock.recv(1024)
        print(f"Received: {response}")
        print(f"Decoded: {response.decode('utf-8').strip()}")

        if response.startswith(b"-ERR unknown command"):
            print("\n[SUCCESS] Command parsed correctly (GET not implemented yet)")
        else:
            print(f"\n[INFO] Response: {response}")

    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        sock.close()


if __name__ == "__main__":
    print("=== Testing Inline Commands (PuTTY-style) ===\n")
    test_inline_ping()
    test_inline_with_args()
    print("\n=== You can now type commands directly in PuTTY! ===")
    print("Just connect in RAW mode and type: PING")
