"""Print the first port from START upward that HOST can bind to. Used by run_local.sh.

    python scripts/free_port.py 127.0.0.1 8000
"""

import socket
import sys


def first_free(host: str, start: int, attempts: int = 50) -> int | None:
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn does when it binds
            try:
                probe.bind((host, port))
            except OSError:
                continue
        return port
    return None


if __name__ == "__main__":
    host, start = sys.argv[1], int(sys.argv[2])
    port = first_free(host, start)
    if port is None:
        sys.exit(f"no free port between {start} and {start + 49} on {host}")
    print(port)
