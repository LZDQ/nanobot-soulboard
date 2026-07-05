# nanobot-soulboard

Run multiple nanobot souls. Manage all souls, sessions, channels and MCPs in a webui.

## Running

Clone this repo recursively.

Create a local `uv` virtualenv and install the root package plus the vendored upstream `nanobot` dependency:

```bash
uv sync
source .venv/bin/activate
```

To run the FastAPI server, use uvicorn directly:

```sh
# Inside venv
uvicorn nanobot_soulboard.server:create_app --factory
```

Server deployment is configured through environment variables:

- CLI args and `UVICORN_*` variables configure the uvicorn process.
- `SOULBOARD_NANO_ROOT` defaults to `~/.nanobot`.
- `SOULBOARD_BASE_CONFIG_PATH` defaults to `$SOULBOARD_NANO_ROOT/config.json`.
- `SOULBOARD_CONFIG_PATH` defaults to `$SOULBOARD_NANO_ROOT/soulboard/config.json`.
- `SOULBOARD_URL_PREFIX` defaults to empty and can be set to a path prefix such as `/soulboard`.

Example (with default host and port and custom url prefix):

```sh
SOULBOARD_URL_PREFIX=/soulboard uvicorn nanobot_soulboard.server:create_app --factory --host 127.0.0.1 --port 18791
```

## Web UI Frontend

The web UI lives under `frontend/` and talks to the FastAPI backend over HTTP and WebSocket.

For deployment, build it into `static/` at the repo root; the backend serves it on the same port as the API:

```sh
cd frontend
pnpm install
pnpm build   # outputs to ../static
```

For frontend development, run the Vite dev server against a locally running backend:

```sh
cd frontend
pnpm install
echo 'VITE_API_BASE=http://127.0.0.1:18791' > .env
pnpm dev
```

### Serving under a path prefix

`VITE_API_BASE` sets both the domain and path prefix the frontend uses for API and WebSocket calls, at build time:

- empty/unset → `/api/...` (same origin — the default for production builds)
- `/prefix` → `/prefix/api/...`
- `http://example.com/aaa` → `http://example.com/aaa/api/...`

To serve the app under a path prefix behind a reverse proxy (which strips the prefix before forwarding, e.g. Caddy `handle_path`), build the frontend with `VITE_API_BASE` set to that prefix and run the backend with the same URL prefix.
