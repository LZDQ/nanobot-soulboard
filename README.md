# nanobot-soulboard

Run multiple nanobot souls. Manage all souls, sessions, channels and MCPs in a webui.

## Running

Clone this repo recursively.

Create a local `uv` virtualenv and install the root package plus the vendored upstream `nanobot` dependency:

```bash
uv sync
source .venv/bin/activate
```

To run the FastAPI server:

```sh
# Inside venv
python -m nanobot_soulboard
```

## Web UI Frontend

The web UI lives under `frontend/` and talks to the FastAPI backend over HTTP and WebSocket.

```sh
cd frontend
pnpm install
echo 'VITE_API_BASE=http://127.0.0.1:18791' > .env
pnpm dev
```
