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

## TODO

- `cd` / changing the agent working directory is intentionally unsupported in v1
