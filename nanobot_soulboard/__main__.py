"""Run the soulboard FastAPI server."""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Run the default soulboard server."""
    uvicorn.run("nanobot_soulboard.server:create_app", factory=True, host="127.0.0.1", port=18791)


if __name__ == "__main__":
    main()
