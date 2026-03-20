# nanobot-soulboard

Backend-first runtime management for multiple nanobot souls in one Python process.

## Current Shape

- soulboard keeps its own minimal config under `~/.nanobot/soulboard/config.json`
- each soul defaults to `~/.nanobot/soulboard/souls/{soul_id}`
- each soul runtime owns its own upstream `MessageBus`, `AgentLoop`, `ChannelManager`, and `SessionManager`
- upstream `workspace/sessions` layout stays unchanged

## Running

Create a local `uv` virtualenv and install the root package plus the vendored upstream `nanobot` dependency:

```bash
cd /home/ldq/nanobot-soulboard
uv venv
source .venv/bin/activate
uv sync
```

`pytest` is in the standard `dev` dependency group, so plain `uv sync` installs it too.

Then run Python or tests normally:

```bash
cd /home/ldq/nanobot-soulboard
source .venv/bin/activate
python
pytest tests/test_soulboard_runtime.py
```

To run the FastAPI server:

```bash
cd /home/ldq/nanobot-soulboard
source .venv/bin/activate
python -m nanobot_soulboard
```

Or with uvicorn directly:

```bash
uvicorn nanobot_soulboard.server:create_app --factory --host 127.0.0.1 --port 18791
```

## Frontend

The web UI lives under `frontend/` and talks to the FastAPI backend over HTTP and WebSocket.

```bash
cd /home/ldq/nanobot-soulboard/frontend
pnpm install
pnpm dev
```

By default the frontend talks to the same origin it is served from. If you run the Vite dev server on a
different port than the backend, point it at the backend explicitly:

```bash
cd /home/ldq/nanobot-soulboard/frontend
VITE_API_BASE=http://127.0.0.1:18791 VITE_WS_BASE=ws://127.0.0.1:18791 pnpm dev
```

## TODO

- `cd` / changing the agent working directory is intentionally unsupported in v1
