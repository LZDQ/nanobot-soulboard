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

## TODO

- [ ] This cron job never expires and triggers every second:

  ```json
      {
        "id": "8c3abc6a",
        "name": "周一开盘了。请激活 work/simons/.venv，运行",
        "enabled": true,
        "schedule": {
          "kind": "cron",
          "atMs": null,
          "everyMs": null,
          "expr": "35 9 13 4 *",
          "tz": "Asia/Shanghai"
        },
        "payload": {
          "kind": "agent_turn",
          "message": "周一开盘了。请激活 work/simons/.venv，运行 work/simons/monday_open_watch.py，用 ak.stock_intraday_sina('股票代码','20260413') 获取候选股盘中数据，基于开盘强弱与量价关系从候选池里选3只最强股票，先查询 quant-arena 持仓和挂单，再尝试下单。注意控制总仓位，优先每只约1/3资金。",
          "deliver": true,
          "channel": "cli",
          "to": "direct"
        },
        "state": {
          "nextRunAtMs": 1776044100000,
          "lastRunAtMs": null,
          "lastStatus": null,
          "lastError": null,
          "runHistory": []
        },
        "createdAtMs": 1775924678700,
        "updatedAtMs": 1775924678700,
        "deleteAfterRun": false,
        "sessionKey": "start",
        "deliveryMetadata": null
      }
  ```

- [ ] Deepseek doesn't work if `apiBase` is not set.
- [ ] Manually tune delivery metadata in cron jobs.
- [ ] Napcat channel cannot send image to group, probably related to `is_group` metadata.
- [x] Remove `working_dir` in exec tool to avoid making GPT models confused.
- [x] Refreshing agents should also refresh config.
