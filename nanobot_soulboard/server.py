"""FastAPI server for nanobot-soulboard."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, Field

from nanobot.config.loader import load_config
from nanobot.config.schema import MCPServerConfig
from nanobot.cli.commands import _make_provider as make_provider
from nanobot.cron.types import CronJob
from nanobot.utils.helpers import sync_workspace_templates

from nanobot_soulboard.config import SoulOverrides, load_soulboard_config
from nanobot_soulboard.runtime import SOUL_PROMPT_FILES, SoulAgentLoop, SoulSessionManager, SoulSpec, SoulSupervisor


class CreateSoulRequest(BaseModel):
    """Request body for creating a soul."""

    soul_id: str = Field(
        description=(
            "Stable soul identifier. This becomes the key in soulboard config and, when no workspace "
            "override is provided, the default workspace directory name under ~/.nanobot/soulboard/souls/."
        )
    )
    overrides: SoulOverrides = Field(
        default_factory=SoulOverrides,
        description=(
            "Per-soul overrides layered on top of the base nanobot config. Any field omitted here keeps "
            "using the base nanobot setting."
        ),
    )


class UpdateSoulRequest(BaseModel):
    """Request body for replacing one soul definition."""

    overrides: SoulOverrides = Field(
        description=(
            "Full replacement soul override object. This endpoint currently replaces the stored override set "
            "for the soul instead of applying a partial merge."
        )
    )


class ChatRequest(BaseModel):
    """Direct chat request against a running soul."""

    content: str = Field(
        min_length=1,
        description="User message content to send to the running soul through AgentLoop.process_direct().",
    )
    session_key: str = Field(
        default="cli:direct",
        description=(
            "Session key used by nanobot SessionManager, typically in channel:chat_id form. This controls "
            "which workspace session file receives the new turn."
        ),
    )
    channel: str = Field(
        default="cli",
        description="Logical channel name passed to the agent loop for routing and context construction.",
    )
    chat_id: str = Field(
        default="direct",
        description="Channel-local chat identifier paired with channel when building runtime context.",
    )


class CreateSessionRequest(BaseModel):
    """Request body for creating one empty persisted session."""

    key: str = Field(
        min_length=1,
        description="Session key used by nanobot SessionManager, typically in channel:chat_id form.",
    )


def _build_session_metadata(key: str) -> dict[str, str]:
    """Derive basic session metadata from a session key."""
    metadata: dict[str, str] = {"title": key}
    if ":" in key:
        channel, chat_id = key.split(":", 1)
        if channel:
            metadata["channel"] = channel
        if chat_id:
            metadata["chat_id"] = chat_id
    return metadata


class StreamChunkResponse(BaseModel):
    """One streamed chunk sent from server to frontend."""

    type: str = "chunk"
    content: str | None = None
    reasoning_content: str | None = None


class StreamResetResponse(BaseModel):
    """Handshake or stream reset message sent from server to frontend."""

    type: str = "reset"
    content: str | None = None
    reasoning_content: str | None = None


class StreamFinalizedMessageResponse(BaseModel):
    """Structured finalized message emitted after persistence."""

    type: str = "finalized"
    role: str
    content: Any = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class SoulResponse(BaseModel):
    """Soul summary returned by the API."""

    soul_id: str
    workspace: str
    running: bool
    overrides: SoulOverrides


class SessionSummaryResponse(BaseModel):
    """Session summary for one soul workspace."""

    key: str
    created_at: str | None = None
    updated_at: str | None = None
    path: str


class SessionDetailResponse(BaseModel):
    """Expanded session contents."""

    created_at: str
    updated_at: str
    metadata: dict[str, Any]
    last_consolidated: int
    messages: list[dict[str, Any]]


class SoulPromptFileResponse(BaseModel):
    """One editable markdown file from a soul workspace."""

    name: str
    exists: bool
    content: str


class SoulPromptFilesResponse(BaseModel):
    """Ordered soul markdown prompt pack."""

    files: list[SoulPromptFileResponse]


class CronJobScheduleResponse(BaseModel):
    """One cron schedule definition."""

    kind: str
    at_ms: int | None = None
    every_ms: int | None = None
    expr: str | None = None
    tz: str | None = None


class CronJobStateResponse(BaseModel):
    """One cron job runtime state snapshot."""

    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: str | None = None
    last_error: str | None = None


class CronJobResponse(BaseModel):
    """One soul cron job."""

    id: str
    name: str
    enabled: bool
    delete_after_run: bool
    message: str
    deliver: bool
    channel: str | None = None
    chat_id: str | None = None
    session_key: str | None = None
    schedule: CronJobScheduleResponse
    state: CronJobStateResponse


class UpdateSoulPromptFileRequest(BaseModel):
    """One prompt file replacement."""

    name: str
    content: str


class UpdateSoulPromptFilesRequest(BaseModel):
    """Replace one or more soul prompt files."""

    files: list[UpdateSoulPromptFileRequest]


class PathsResponse(BaseModel):
    """Resolved config paths used by the server."""

    nano_root: str
    base_config_path: str
    soulboard_config_path: str


class MCPServerResponse(BaseModel):
    """Named MCP server definition from base nanobot config."""

    name: str
    config: MCPServerConfig


class CreateMCPServerRequest(BaseModel):
    """Request body for creating one MCP server definition."""

    name: str = Field(description="Unique MCP server name stored under tools.mcpServers in nanobot config.")
    config: MCPServerConfig


class UpdateMCPServerRequest(BaseModel):
    """Request body for replacing one MCP server definition."""

    config: MCPServerConfig


class ErrorResponse(BaseModel):
    """Simple structured error body."""

    detail: str


class AppState:
    """Typed app state."""

    def __init__(self, supervisor: SoulSupervisor, nano_root: Path, base_config_path: Path, soulboard_config_path: Path):
        self.supervisor = supervisor
        self.nano_root = nano_root
        self.base_config_path = base_config_path
        self.soulboard_config_path = soulboard_config_path


@dataclass
class StreamState:
    """Current streaming snapshot for one websocket chat session."""

    reasoning_content: str = ""
    content: str = ""
    websockets: set[WebSocket] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _to_soul_response(supervisor: SoulSupervisor, spec: SoulSpec) -> SoulResponse:
    return SoulResponse(
        soul_id=spec.soul_id,
        workspace=str(spec.workspace),
        running=supervisor.is_running(spec.soul_id),
        overrides=spec.overrides,
    )


def _get_state(request: Request) -> AppState:
    return request.app.state.soulboard


def _get_supervisor(request: Request) -> SoulSupervisor:
    return _get_state(request).supervisor


def _raise_not_found(detail: str) -> None:
    raise HTTPException(status_code=404, detail=detail)


def _error_detail(exc: Exception) -> str:
    return str(exc.args[0]) if exc.args else str(exc)


def _build_session_manager(spec: SoulSpec) -> SoulSessionManager:
    return SoulSessionManager(spec.workspace)


def _get_session_manager(supervisor: SoulSupervisor, spec: SoulSpec) -> SoulSessionManager:
    try:
        agent_loop: SoulAgentLoop = supervisor.get_agent_loop(spec.soul_id)
        return agent_loop.sessions
    except KeyError:
        return _build_session_manager(spec)


def _build_prompt_files_response(files: dict[str, str | None]) -> SoulPromptFilesResponse:
    return SoulPromptFilesResponse(
        files=[
            SoulPromptFileResponse(name=name, exists=files[name] is not None, content=files[name] or "")
            for name in SOUL_PROMPT_FILES
        ]
    )


def _to_cron_job_response(job: CronJob, session_key: str | None) -> CronJobResponse:
    return CronJobResponse(
        id=job.id,
        name=job.name,
        enabled=job.enabled,
        delete_after_run=job.delete_after_run,
        message=job.payload.message,
        deliver=job.payload.deliver,
        channel=job.payload.channel,
        chat_id=job.payload.to,
        session_key=session_key,
        schedule=CronJobScheduleResponse(
            kind=job.schedule.kind,
            at_ms=job.schedule.at_ms,
            every_ms=job.schedule.every_ms,
            expr=job.schedule.expr,
            tz=job.schedule.tz,
        ),
        state=CronJobStateResponse(
            next_run_at_ms=job.state.next_run_at_ms,
            last_run_at_ms=job.state.last_run_at_ms,
            last_status=job.state.last_status,
            last_error=job.state.last_error,
        ),
    )


async def _broadcast_json(stream: StreamState, payload: dict[str, Any]) -> None:
    """Send one payload to all connected listeners for a stream."""
    dead: list[WebSocket] = []
    for websocket in list(stream.websockets):
        try:
            await websocket.send_json(payload)
        except RuntimeError:
            dead.append(websocket)
    for websocket in dead:
        stream.websockets.discard(websocket)


async def _stream_chat(
    stream: StreamState,
    agent_loop,
    body: ChatRequest,
) -> None:
    """Run one streamed chat turn over an accepted websocket."""
    async with stream.lock:
        session = agent_loop.sessions.get_or_create(body.session_key)
        before_count = len(session.messages)
        stream.reasoning_content = ""
        stream.content = ""
        await _broadcast_json(stream, StreamResetResponse().model_dump())

        async def _send_chunk(content: str, *, tool_hint: bool = False) -> None:
            if tool_hint:
                stream.content += content
                payload = StreamChunkResponse(content=content, reasoning_content=None)
            else:
                stream.reasoning_content += content
                payload = StreamChunkResponse(content=None, reasoning_content=content)
            await _broadcast_json(stream, payload.model_dump())

        await agent_loop.process_direct(
            content=body.content,
            session_key=body.session_key,
            channel=body.channel,
            chat_id=body.chat_id,
            on_progress=_send_chunk,
        )

        for message in session.messages[before_count:]:
            await _broadcast_json(
                stream,
                StreamFinalizedMessageResponse(
                    role=message["role"],
                    content=message.get("content"),
                    tool_calls=message.get("tool_calls"),
                    tool_call_id=message.get("tool_call_id"),
                ).model_dump(),
            )

        stream.reasoning_content = ""
        stream.content = ""


def create_app(
    *,
    nano_root: Path | None = None,
    base_config_path: Path | None = None,
    soulboard_config_path: Path | None = None,
) -> FastAPI:
    """Create the FastAPI app."""
    resolved_nano_root = (nano_root or (Path.home() / ".nanobot")).expanduser()
    resolved_base_config_path = base_config_path or (resolved_nano_root / "config.json")
    resolved_soulboard_config_path = soulboard_config_path or (resolved_nano_root / "soulboard" / "config.json")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        base_config = load_config(resolved_base_config_path)
        soulboard_config = load_soulboard_config(resolved_soulboard_config_path)
        supervisor = SoulSupervisor(
            base_config=base_config,
            nano_root=resolved_nano_root,
            soulboard_config=soulboard_config,
            config_path=resolved_soulboard_config_path,
            base_config_path=resolved_base_config_path,
            provider_factory=make_provider,
        )
        app.state.soulboard = AppState(
            supervisor=supervisor,
            nano_root=resolved_nano_root,
            base_config_path=resolved_base_config_path,
            soulboard_config_path=resolved_soulboard_config_path,
        )
        app.state.streams = {}
        for spec in supervisor.list_specs():
            sync_workspace_templates(spec.workspace, silent=True)
        await supervisor.start_autostart_souls()
        try:
            yield
        finally:
            await supervisor.stop_all()

    app = FastAPI(title="nanobot-soulboard", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(
        "/health",
        summary="Health Check",
        description="Lightweight liveness endpoint for checking whether the FastAPI server process is up.",
    )
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/paths",
        response_model=PathsResponse,
        summary="Get Resolved Config Paths",
        description=(
            "Return the resolved nanobot root, base nanobot config path, and soulboard config path currently "
            "used by this server instance. This is useful for debugging deployment and config loading issues."
        ),
    )
    def get_paths(request: Request) -> PathsResponse:
        state = _get_state(request)
        return PathsResponse(
            nano_root=str(state.nano_root),
            base_config_path=str(state.base_config_path),
            soulboard_config_path=str(state.soulboard_config_path),
        )

    @app.get(
        "/api/souls",
        response_model=list[SoulResponse],
        summary="List Souls",
        description=(
            "List all souls declared in soulboard config.json, including their resolved workspace paths, "
            "stored overrides, and whether each soul is currently running."
        ),
    )
    def list_souls(request: Request) -> list[SoulResponse]:
        supervisor = _get_supervisor(request)
        return [_to_soul_response(supervisor, spec) for spec in supervisor.list_specs()]

    @app.get(
        "/api/mcp-servers",
        response_model=list[MCPServerResponse],
        summary="List MCP Servers",
        description=(
            "List MCP server definitions from the base nanobot config.json. Souls select from these names "
            "when enabling MCP servers in their overrides."
        ),
    )
    def list_mcp_servers(request: Request) -> list[MCPServerResponse]:
        supervisor = _get_supervisor(request)
        return [
            MCPServerResponse(name=name, config=config)
            for name, config in supervisor.list_mcp_servers().items()
        ]

    @app.post(
        "/api/mcp-servers",
        response_model=MCPServerResponse,
        responses={400: {"model": ErrorResponse}},
        summary="Create MCP Server",
        description=(
            "Create one MCP server definition in the base nanobot config.json. Souls can then enable this "
            "server by name from their override selection list."
        ),
    )
    def create_mcp_server(request: Request, body: CreateMCPServerRequest) -> MCPServerResponse:
        supervisor = _get_supervisor(request)
        try:
            config = supervisor.create_mcp_server(body.name, body.config)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return MCPServerResponse(name=body.name, config=config)

    @app.patch(
        "/api/mcp-servers/{name}",
        response_model=MCPServerResponse,
        responses={404: {"model": ErrorResponse}},
        summary="Update MCP Server",
        description=(
            "Replace one MCP server definition in the base nanobot config.json. The server name must "
            "already exist; souls can only select from this persisted definition list."
        ),
    )
    def update_mcp_server(request: Request, name: str, body: UpdateMCPServerRequest) -> MCPServerResponse:
        supervisor = _get_supervisor(request)
        try:
            config = supervisor.update_mcp_server(name, body.config)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        return MCPServerResponse(name=name, config=config)

    @app.delete(
        "/api/mcp-servers/{name}",
        status_code=204,
        responses={404: {"model": ErrorResponse}},
        summary="Delete MCP Server",
        description=(
            "Delete one MCP server definition from the base nanobot config.json. Souls will no longer be "
            "able to select this server name until it is recreated."
        ),
    )
    def delete_mcp_server(request: Request, name: str) -> None:
        supervisor = _get_supervisor(request)
        try:
            supervisor.delete_mcp_server(name)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))

    @app.post(
        "/api/souls",
        response_model=SoulResponse,
        responses={400: {"model": ErrorResponse}},
        summary="Create Soul",
        description=(
            "Create a new soul entry in soulboard config.json and return the resolved soul definition. "
            "If the stored overrides set autostart=true, the new soul is started immediately."
        ),
    )
    async def create_soul(request: Request, body: CreateSoulRequest) -> SoulResponse:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.create_soul(body.soul_id, body.overrides)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sync_workspace_templates(spec.workspace, silent=True)
        if spec.overrides.autostart:
            await supervisor.start_soul(spec.soul_id)
        return _to_soul_response(supervisor, spec)

    @app.get(
        "/api/souls/{soul_id}",
        response_model=SoulResponse,
        responses={404: {"model": ErrorResponse}},
        summary="Get Soul",
        description=(
            "Return one soul from the persisted soulboard config, including its resolved workspace path, "
            "effective overrides, and current running state."
        ),
    )
    def get_soul(request: Request, soul_id: str) -> SoulResponse:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        return _to_soul_response(supervisor, spec)

    @app.get(
        "/api/souls/{soul_id}/prompt-files",
        response_model=SoulPromptFilesResponse,
        responses={404: {"model": ErrorResponse}},
        summary="Get Soul Prompt Files",
        description=(
            "Return the editable markdown prompt pack from a soul workspace: AGENTS.md, SOUL.md, USER.md, "
            "TOOLS.md, and SYSTEM.md. Missing files are returned with exists=false and empty content."
        ),
    )
    def get_soul_prompt_files(request: Request, soul_id: str) -> SoulPromptFilesResponse:
        supervisor = _get_supervisor(request)
        try:
            files = supervisor.read_soul_prompt_files(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        return _build_prompt_files_response(files)

    @app.get(
        "/api/souls/{soul_id}/cron-jobs",
        response_model=list[CronJobResponse],
        responses={404: {"model": ErrorResponse}},
        summary="List Soul Cron Jobs",
        description=(
            "List one soul's persisted cron jobs from its workspace-local cron/jobs.json store, including the "
            "originating session key that scheduled each job."
        ),
    )
    def get_soul_cron_jobs(request: Request, soul_id: str) -> list[CronJobResponse]:
        supervisor = _get_supervisor(request)
        try:
            jobs = supervisor.list_cron_jobs(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        return [_to_cron_job_response(job, session_key) for job, session_key in jobs]

    @app.patch(
        "/api/souls/{soul_id}/prompt-files",
        response_model=SoulPromptFilesResponse,
        responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
        summary="Update Soul Prompt Files",
        description=(
            "Write one or more markdown prompt files into the selected soul workspace. This endpoint updates "
            "workspace files directly and returns the full ordered prompt pack after the write."
        ),
    )
    def update_soul_prompt_files(request: Request, soul_id: str, body: UpdateSoulPromptFilesRequest) -> SoulPromptFilesResponse:
        supervisor = _get_supervisor(request)
        try:
            files = supervisor.write_soul_prompt_files(
                soul_id,
                {item.name: item.content for item in body.files},
            )
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _build_prompt_files_response(files)

    @app.patch(
        "/api/souls/{soul_id}",
        response_model=SoulResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        summary="Update Soul",
        description=(
            "Replace the stored override block for a soul in soulboard config.json. This endpoint refuses "
            "to modify a soul while it is running so runtime state and persisted config cannot diverge."
        ),
    )
    def update_soul(request: Request, soul_id: str, body: UpdateSoulRequest) -> SoulResponse:
        supervisor = _get_supervisor(request)
        if soul_id not in supervisor.soulboard_config.souls:
            _raise_not_found(f"Unknown soul: {soul_id}")
        try:
            supervisor.modify_soul(soul_id, body.overrides)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        spec = supervisor.get_spec(soul_id)
        sync_workspace_templates(spec.workspace, silent=True)
        return _to_soul_response(supervisor, spec)

    @app.delete(
        "/api/souls/{soul_id}",
        status_code=204,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        summary="Delete Soul",
        description=(
            "Remove a soul definition from soulboard config.json. The soul must not be running when this "
            "endpoint is called."
        ),
    )
    def delete_soul(request: Request, soul_id: str) -> None:
        supervisor = _get_supervisor(request)
        try:
            supervisor.delete_soul(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/souls/{soul_id}/start",
        response_model=SoulResponse,
        responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
        summary="Start Soul",
        description=(
            "Build the in-memory nanobot runtime for a soul and start its AgentLoop and enabled channels. "
            "The stored soul config remains the source of truth; this endpoint only affects runtime state."
        ),
    )
    async def start_soul(request: Request, soul_id: str) -> SoulResponse:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.get_spec(soul_id)
            sync_workspace_templates(spec.workspace, silent=True)
            await supervisor.start_soul(soul_id)
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _to_soul_response(supervisor, spec)

    @app.post(
        "/api/souls/{soul_id}/stop",
        response_model=SoulResponse,
        responses={404: {"model": ErrorResponse}},
        summary="Stop Soul",
        description=(
            "Stop a running soul, cancel its channel tasks, and close MCP connections if present. If the "
            "soul is already stopped, the endpoint still returns the persisted soul definition."
        ),
    )
    async def stop_soul(request: Request, soul_id: str) -> SoulResponse:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        await supervisor.stop_soul(soul_id)
        return _to_soul_response(supervisor, spec)

    @app.get(
        "/api/souls/{soul_id}/sessions",
        response_model=list[SessionSummaryResponse],
        responses={404: {"model": ErrorResponse}},
        summary="List Soul Sessions",
        description=(
            "List persisted session files for one soul workspace using upstream nanobot SessionManager. "
            "Sessions remain stored under that soul's workspace/sessions directory."
        ),
    )
    def list_soul_sessions(request: Request, soul_id: str) -> list[SessionSummaryResponse]:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        manager = _get_session_manager(supervisor, spec)
        return [SessionSummaryResponse(**item) for item in manager.list_sessions()]

    @app.post(
        "/api/souls/{soul_id}/sessions",
        response_model=SessionDetailResponse,
        responses={404: {"model": ErrorResponse}},
        summary="Create Empty Session",
        description=(
            "Persist an empty session file for the selected soul workspace. If the session already exists, "
            "this returns its current persisted contents."
        ),
    )
    def create_soul_session(request: Request, soul_id: str, body: CreateSessionRequest) -> SessionDetailResponse:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        manager = _get_session_manager(supervisor, spec)
        session = manager.get_or_create(body.key)
        if not session.metadata:
            session.metadata = _build_session_metadata(body.key)
        manager.save(session)
        return SessionDetailResponse(
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            metadata=session.metadata,
            last_consolidated=session.last_consolidated,
            messages=session.messages,
        )

    @app.get(
        "/api/souls/{soul_id}/sessions/{session_key}",
        response_model=SessionDetailResponse,
        responses={404: {"model": ErrorResponse}},
        summary="Get Session Detail",
        description=(
            "Load and return the full persisted contents of a single session from the selected soul's "
            "workspace. The session key should match the key used by nanobot SessionManager."
        ),
    )
    def get_session(request: Request, soul_id: str, session_key: str) -> SessionDetailResponse:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        manager = _get_session_manager(supervisor, spec)
        known = {item["key"] for item in manager.list_sessions()}
        if session_key not in known:
            _raise_not_found(f"Unknown session: {session_key}")
        session = manager.get_or_create(session_key)
        return SessionDetailResponse(
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            metadata=session.metadata,
            last_consolidated=session.last_consolidated,
            messages=session.messages,
        )

    @app.post(
        "/api/souls/{soul_id}/chat",
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        summary="Chat With Running Soul",
        description=(
            "Send one direct message to a running soul through AgentLoop.process_direct() and return the "
            "final assistant response text. This endpoint requires the soul to already be running."
        ),
    )
    async def chat(request: Request, soul_id: str, body: ChatRequest) -> dict[str, str]:
        supervisor = _get_supervisor(request)
        try:
            agent_loop = supervisor.get_agent_loop(soul_id)
        except KeyError as exc:
            if soul_id not in supervisor.soulboard_config.souls:
                _raise_not_found(f"Unknown soul: {soul_id}")
            raise HTTPException(status_code=409, detail=_error_detail(exc)) from exc
        response = await agent_loop.process_direct(
            content=body.content,
            session_key=body.session_key,
            channel=body.channel,
            chat_id=body.chat_id,
        )
        return {"content": response}

    @app.websocket("/ws/souls/{soul_id}/chat")
    async def stream_chat(websocket: WebSocket, soul_id: str) -> None:
        await websocket.accept()
        supervisor = websocket.app.state.soulboard.supervisor
        session_key = websocket.query_params.get("session_key", "cli:direct")
        channel = websocket.query_params.get("channel", "cli")
        chat_id = websocket.query_params.get("chat_id", "direct")
        logger.info("WebSocket connected: soul={} session_key={} channel={} chat_id={}", soul_id, session_key, channel, chat_id)
        stream_key = (soul_id, session_key, channel, chat_id)
        streams: dict[tuple[str, str, str, str], StreamState] = websocket.app.state.streams
        stream = streams.setdefault(stream_key, StreamState())
        stream.websockets.add(websocket)
        try:
            agent_loop = supervisor.get_agent_loop(soul_id)
        except KeyError:
            await websocket.send_json(StreamResetResponse().model_dump())
            await websocket.close(code=4404, reason=f"Soul is not running or does not exist: {soul_id}")
            stream.websockets.discard(websocket)
            return

        await websocket.send_json(
            StreamResetResponse(
                content=stream.content or None,
                reasoning_content=stream.reasoning_content or None,
            ).model_dump()
        )

        while True:
            try:
                payload = await websocket.receive_json()
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected: soul={} session_key={} channel={} chat_id={}", soul_id, session_key, channel, chat_id)
                stream.websockets.discard(websocket)
                if not stream.websockets and not stream.reasoning_content and not stream.content:
                    streams.pop(stream_key, None)
                break

            body = ChatRequest(
                content=payload["content"],
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
            )
            try:
                await _stream_chat(stream, agent_loop, body)
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected during stream: soul={} session_key={} channel={} chat_id={}", soul_id, session_key, channel, chat_id)
                stream.websockets.discard(websocket)
                break

    return app
