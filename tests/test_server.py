import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from nanobot_soulboard.server import create_app


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_server_soul_lifecycle(monkeypatch, tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: provider)
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("nanobot_soulboard.runtime.SoulAgentLoop.process_direct", AsyncMock(return_value="hello"))

    _write_json(tmp_path / "config.json", {"providers": {"ollama": {"apiBase": "http://localhost:11434"}}})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}

        created = client.post(
            "/api/souls",
            json={"soul_id": "alpha", "overrides": {"model": "test-model", "autostart": True}},
        )
        assert created.status_code == 200
        assert created.json()["soul_id"] == "alpha"
        assert created.json()["running"] is True

        listed = client.get("/api/souls")
        assert listed.status_code == 200
        assert [item["soul_id"] for item in listed.json()] == ["alpha"]

        updated = client.patch("/api/souls/alpha", json={"overrides": {"provider": "ollama"}})
        assert updated.status_code == 200
        assert updated.json()["overrides"]["provider"] == "ollama"

        chatted = client.post("/api/souls/alpha/chat", json={"content": "hi"})
        assert chatted.status_code == 200
        assert chatted.json() == {"content": "hello"}

        stopped = client.post("/api/souls/alpha/stop")
        assert stopped.status_code == 200
        assert stopped.json()["running"] is False

        deleted = client.delete("/api/souls/alpha")
        assert deleted.status_code == 204


def test_server_lists_and_reads_sessions(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: MagicMock())
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])

    _write_json(tmp_path / "config.json", {})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {}}})

    session_dir = tmp_path / "soulboard" / "souls" / "alpha" / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / "cli_direct.jsonl"
    session_path.write_text(
        json.dumps(
            {
                "_type": "metadata",
                "key": "cli:direct",
                "created_at": "2025-01-01T00:00:00",
                "updated_at": "2025-01-01T00:00:00",
                "metadata": {"title": "demo"},
                "last_consolidated": 0,
            }
        )
        + "\n"
        + json.dumps({"role": "user", "content": "hello", "timestamp": "2025-01-01T00:00:01"})
        + "\n",
        encoding="utf-8",
    )

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        listed = client.get("/api/souls/alpha/sessions")
        assert listed.status_code == 200
        assert listed.json()[0]["key"] == "cli:direct"

        detail = client.get("/api/souls/alpha/sessions/cli:direct")
        assert detail.status_code == 200
        assert detail.json()["messages"][0]["content"] == "hello"


def test_server_websocket_streams_reset_and_chunks(monkeypatch, tmp_path: Path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    async def _fake_process_direct(self, content, session_key="cli:direct", channel="cli", chat_id="direct", on_progress=None):
        assert content == "hi"
        assert session_key == "cli:direct"
        if on_progress is not None:
            await on_progress("thinking")
            await on_progress("read_file(\"x\")", tool_hint=True)
        session = self.sessions.get_or_create(session_key)
        session.messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
                "timestamp": "2025-01-01T00:00:01",
            }
        )
        session.messages.append(
            {
                "role": "tool",
                "name": "read_file",
                "tool_call_id": "call_1",
                "content": "file body",
                "timestamp": "2025-01-01T00:00:02",
            }
        )
        session.messages.append(
            {
                "role": "assistant",
                "content": "done",
                "timestamp": "2025-01-01T00:00:03",
            }
        )
        return "done"

    monkeypatch.setattr("nanobot_soulboard.server.make_provider", lambda _config: provider)
    monkeypatch.setattr("nanobot_soulboard.server.sync_workspace_templates", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("nanobot_soulboard.runtime.SoulAgentLoop.process_direct", _fake_process_direct)

    _write_json(tmp_path / "config.json", {"providers": {"ollama": {"apiBase": "http://localhost:11434"}}})
    _write_json(tmp_path / "soulboard" / "config.json", {"souls": {"alpha": {"autostart": True}}})

    app = create_app(
        nano_root=tmp_path,
        base_config_path=tmp_path / "config.json",
        soulboard_config_path=tmp_path / "soulboard" / "config.json",
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws/souls/alpha/chat?session_key=cli:direct") as websocket:
            assert websocket.receive_json() == {"type": "reset", "content": None, "reasoning_content": None}

            websocket.send_json({"content": "hi"})

            reset = websocket.receive_json()
            assert reset == {"type": "reset", "content": None, "reasoning_content": None}

            chunk = websocket.receive_json()
            assert chunk == {"type": "chunk", "content": None, "reasoning_content": "thinking"}

            tool_hint = websocket.receive_json()
            assert tool_hint == {"type": "chunk", "content": 'read_file("x")', "reasoning_content": None}

            finalized_tool_call = websocket.receive_json()
            assert finalized_tool_call["type"] == "finalized"
            assert finalized_tool_call["role"] == "assistant"
            assert finalized_tool_call["tool_calls"][0]["id"] == "call_1"

            finalized_tool_result = websocket.receive_json()
            assert finalized_tool_result == {
                "type": "finalized",
                "role": "tool",
                "content": "file body",
                "tool_calls": None,
                "tool_call_id": "call_1",
            }

            finalized_answer = websocket.receive_json()
            assert finalized_answer == {
                "type": "finalized",
                "role": "assistant",
                "content": "done",
                "tool_calls": None,
                "tool_call_id": None,
            }
