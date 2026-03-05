"""Edge Agent entry point: python -m example_edge ws://localhost:8000 sk-xxx"""

import asyncio
import sys

from .agent import run


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m example_edge <ws_url> <api_key>")
        print("Example: python -m example_edge ws://localhost:8000/v1/edge/connect sk-xxx")
        sys.exit(1)

    server_url = sys.argv[1]
    # Append path if user only gave host:port
    if "/v1/edge/connect" not in server_url:
        server_url = server_url.rstrip("/") + "/v1/edge/connect"

    api_key = sys.argv[2]
    asyncio.run(run(server_url, api_key))


if __name__ == "__main__":
    main()
