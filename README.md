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

Dev:

```sh
uvicorn nanobot_soulboard.server:create_app --factory --host 127.0.0.1 --port 18791 --reload --reload-dir nanobot_soulboard
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
echo 'VITE_API_BASE=http://127.0.0.1:18791' > .env  # .env.example
pnpm dev
```

### Serving under a path prefix

`VITE_API_BASE` is the single build-time setting for both HTTP API and WebSocket calls. It controls the domain and the path prefix:

- empty/unset → same origin and the backend-injected page mount prefix
- `/prefix` → `/prefix/api/...`
- `http://example.com/aaa` → `http://example.com/aaa/api/...`

Production builds need no frontend environment variables. The backend injects `<base href>` into the built `index.html` response at runtime, so assets, client routes, API calls, and WebSocket calls all inherit `SOULBOARD_URL_PREFIX`.

The included `.env.example` points Vite at the default local development backend on `http://127.0.0.1:18792`. Change that one value when developing the frontend against a different backend origin or mount prefix.

The UI home page is the injected mount root. Soul-specific configuration, sessions, and chat live at `/<soul-id>/soulboard` beneath that root, with the selected session kept in the `session-key` query parameter. For example, `SOULBOARD_URL_PREFIX=/soulboard` serves the home page at `/soulboard/` and soul pages at `/soulboard/<soul-id>/soulboard`.
