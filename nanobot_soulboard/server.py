"""FastAPI server for nanobot-soulboard."""

import re
from contextlib import asynccontextmanager
from html import escape
from importlib.resources import files as pkg_files
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from nanobot.config.loader import load_config
from nanobot_soulboard.providers import make_provider
from nanobot.cron.types import CronJob, CronSchedule
from nanobot_soulboard.cron import SoulCronPayload
from nanobot.session.manager import SessionManager
from nanobot_soulboard.chat_streams import ChatStreamManager
from nanobot_soulboard.config import (
    SoulboardSettings,
    get_base_config_path,
    get_soulboard_config_path,
    load_soulboard_config,
)
from nanobot_soulboard.agent import (
    SOUL_PROMPT_FILES,
    SoulAgentLoop,
    SoulCloneCronJob,
    SoulSpec,
    SoulSupervisor,
)
from nanobot_soulboard.schemas import (
    AddSoulCronJobsFromRegistryRequest,
    CreateSoulCronJobRequest,
    AddSoulSkillRequest,
    ChatRequest,
    CloneSoulRequest,
    CreateMCPServerRequest,
    CreateSessionRequest,
    CreateSoulRequest,
    CronJobRegistryResponse,
    CronJobResponse,
    CronJobScheduleResponse,
    CronJobStateResponse,
    DisabledToolsResponse,
    ErrorResponse,
    MCPServerResponse,
    PathsResponse,
    SessionDetailResponse,
    SessionListResponse,
    SessionSummaryResponse,
    SkillPoolEntryResponse,
    SkillPoolResponse,
    SkillRegistryResponse,
    SoulPromptFileResponse,
    SoulPromptFilesResponse,
    SoulResponse,
    SoulSkillResponse,
    StreamInputMessage,
    ToolCatalogItemResponse,
    UpdateCronJobRegistryRequest,
    UpdateDisabledToolsRequest,
    UpdateMCPServerRequest,
    UpdateSkillRegistryRequest,
    UpdateSoulCronJobRequest,
    UpdateSoulPromptFilesRequest,
    UpdateSoulRequest,
)
from nanobot_soulboard.skills import count_text_stats, skill_summary


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

def _build_session_detail_response(
    session: Any,
    *,
    before: int | None = None,
    limit: int | None = None,
) -> SessionDetailResponse:
    """Build one session detail response for the requested history window."""
    total_messages = len(session.messages)
    window_end = total_messages if before is None else max(0, min(before, total_messages))
    if limit is None:
        window_start = min(session.last_consolidated, window_end)
    else:
        window_start = max(0, window_end - max(limit, 0))
    return SessionDetailResponse(
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
        metadata=session.metadata,
        last_consolidated=session.last_consolidated,
        history_start=window_start,
        history_end=window_end,
        total_messages=total_messages,
        messages=session.messages[window_start:window_end],
    )

class AppState:
    """Typed app state."""

    def __init__(
        self,
        supervisor: SoulSupervisor,
        settings: SoulboardSettings,
        nano_root: Path,
        base_config_path: Path,
        soulboard_config_path: Path,
    ):
        self.supervisor = supervisor
        self.settings = settings
        self.nano_root = nano_root
        self.base_config_path = base_config_path
        self.soulboard_config_path = soulboard_config_path


def _ensure_nano_root_exists(nano_root: Path) -> None:
    """Refuse to start before nanobot has been onboarded."""
    if nano_root.exists():
        return
    raise RuntimeError(
        f"Nanobot data root does not exist: {nano_root}. "
        'Run "nanobot onboard" first.'
    )


def _sync_soul_workspace(spec: SoulSpec) -> None:
    """Sync the soulboard workspace scaffold without default USER.md/TOOLS.md."""
    try:
        templates = pkg_files("nanobot") / "templates"
    except Exception:
        return
    if not templates.is_dir():
        return

    excluded_root_files = {"USER.md", "TOOLS.md"}

    def _write_if_missing(src, dest: Path) -> None:
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8") if src else "", encoding="utf-8")

    for item in templates.iterdir():
        if not item.name.endswith(".md") or item.name.startswith(".") or item.name in excluded_root_files:
            continue
        _write_if_missing(item, spec.workspace / item.name)

    _write_if_missing(templates / "memory" / "MEMORY.md", spec.workspace / "memory" / "MEMORY.md")
    _write_if_missing(None, spec.workspace / "memory" / "history.jsonl")
    (spec.workspace / "skills").mkdir(parents=True, exist_ok=True)
def _to_soul_response(supervisor: SoulSupervisor, spec: SoulSpec) -> SoulResponse:
    return SoulResponse(
        soul_id=spec.soul_id,
        workspace=str(spec.workspace),
        skills=_list_soul_skills(spec),
        running=supervisor.is_running(spec.soul_id),
        overrides=spec.overrides,
    )


def _list_soul_skills(spec: SoulSpec) -> list[SoulSkillResponse]:
    skills_root = spec.workspace / "skills"
    if not skills_root.exists():
        return []

    skills: list[SoulSkillResponse] = []
    for skill_dir in sorted(skills_root.iterdir(), key=lambda path: path.name):
        if not (skill_dir.is_dir() or skill_dir.is_symlink()):
            continue
        skill_path = skill_dir / "SKILL.md"
        if not skill_path.exists():
            continue
        link_target: str | None = None
        if skill_dir.is_symlink():
            try:
                link_target = str(Path(skill_dir).resolve())
            except OSError:
                link_target = str(skill_dir.readlink())
        _, description = skill_summary(skill_dir)
        content = skill_path.read_text(encoding="utf-8")
        stats = count_text_stats(content)
        skills.append(SoulSkillResponse(
            name=skill_dir.name,
            path=str(skill_path),
            content=content,
            description=description,
            char_count=stats.char_count,
            word_count=stats.word_count,
            line_count=stats.line_count,
            link_target=link_target,
        ))
    return skills


def _build_skill_registry_response(supervisor: SoulSupervisor) -> SkillRegistryResponse:
    pools_cache = supervisor.get_skill_pools()
    pools: list[SkillPoolResponse] = []
    for raw_path in supervisor.list_skill_pools():
        pool_root = Path(raw_path).expanduser()
        exists = pool_root.is_dir()
        entries = pools_cache.get(raw_path, [])
        skills = [
            SkillPoolEntryResponse(
                skill_path=str(entry.skill_dir),
                relative_path=entry.relative_path,
                name=entry.name,
                description=entry.description,
                char_count=entry.char_count,
                word_count=entry.word_count,
                line_count=entry.line_count,
            )
            for entry in entries
        ]
        pools.append(SkillPoolResponse(path=raw_path, exists=exists, skills=skills))
    return SkillRegistryResponse(pools=pools)


def _get_state(request: Request) -> AppState:
    return request.app.state.soulboard


def _get_supervisor(request: Request) -> SoulSupervisor:
    return _get_state(request).supervisor


def _raise_not_found(detail: str) -> None:
    raise HTTPException(status_code=404, detail=detail)


def _error_detail(exc: Exception) -> str:
    return str(exc.args[0]) if exc.args else str(exc)


def _get_session_manager(supervisor: SoulSupervisor, spec: SoulSpec) -> SessionManager:
    try:
        agent_loop: SoulAgentLoop = supervisor.get_agent_loop(spec.soul_id)
        return agent_loop.sessions
    except KeyError:
        return SessionManager(spec.workspace)


def _build_prompt_files_response(files: dict[str, str | None]) -> SoulPromptFilesResponse:
    return SoulPromptFilesResponse(
        files=[
            SoulPromptFileResponse(name=name, exists=files[name] is not None, content=files[name] or "")
            for name in SOUL_PROMPT_FILES
        ]
    )


def _to_cron_job_response(job: CronJob) -> CronJobResponse:
    assert isinstance(job.payload, SoulCronPayload)
    return CronJobResponse(
        id=job.id,
        name=job.name,
        enabled=job.enabled,
        delete_after_run=job.delete_after_run,
        message=job.payload.message,
        origin_channel=job.payload.origin_channel,
        origin_chat_id=job.payload.origin_chat_id,
        origin_metadata=job.payload.origin_metadata,
        session_key=job.payload.session_key,
        recurring_session_key_format=job.payload.recurring_session_key_format,
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


def create_app() -> FastAPI:
    """Create the FastAPI app."""
    settings = SoulboardSettings()
    normalized_url_prefix = settings.url_prefix
    resolved_nano_root = settings.nano_root
    resolved_base_config_path = get_base_config_path(resolved_nano_root)
    resolved_soulboard_config_path = get_soulboard_config_path(resolved_nano_root)
    _ensure_nano_root_exists(resolved_nano_root)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        base_config = load_config(resolved_base_config_path)
        soulboard_config = load_soulboard_config(resolved_soulboard_config_path)
        supervisor = SoulSupervisor(
            base_config=base_config,
            nano_root=resolved_nano_root,
            soulboard_config=soulboard_config,
            provider_factory=make_provider,
        )
        app.state.soulboard = AppState(
            supervisor=supervisor,
            settings=settings,
            nano_root=resolved_nano_root,
            base_config_path=resolved_base_config_path,
            soulboard_config_path=resolved_soulboard_config_path,
        )
        app.state.chat_streams = ChatStreamManager()
        supervisor.refresh_skill_pools()
        for spec in supervisor.list_specs():
            _sync_soul_workspace(spec)
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
    api = APIRouter(prefix=f"{normalized_url_prefix}/api")

    @app.get(
        "/health",
        summary="Health Check",
        description="Lightweight liveness endpoint for checking whether the FastAPI server process is up.",
    )
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get(
        "/paths",
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

    @api.get(
        "/nanobot-tools",
        response_model=list[ToolCatalogItemResponse],
        summary="List Nanobot Tools",
        description=(
            "Return the dynamically registered nanobot tool names that can be controlled by soulboard. "
            "The list is derived from the current base nanobot tool loader before soulboard policy is applied."
        ),
    )
    def list_nanobot_tools(request: Request) -> list[ToolCatalogItemResponse]:
        supervisor = _get_supervisor(request)
        return [
            ToolCatalogItemResponse(name=item.name, description=item.description)
            for item in supervisor.list_nanobot_tools()
        ]

    @api.get(
        "/nanobot-disabled-tools",
        response_model=DisabledToolsResponse,
        summary="Get Globally Disabled Nanobot Tools",
        description=(
            "Return the tools disabled for every soul by default. A soul can restore a registered "
            "tool through its enabled_tools list."
        ),
    )
    def get_nanobot_disabled_tools(request: Request) -> DisabledToolsResponse:
        supervisor = _get_supervisor(request)
        return DisabledToolsResponse(disabled_tools=supervisor.get_disabled_tools())

    @api.patch(
        "/nanobot-disabled-tools",
        response_model=DisabledToolsResponse,
        responses={400: {"model": ErrorResponse}},
        summary="Update Globally Disabled Nanobot Tools",
        description=(
            "Replace the global disabled tool list. Running souls must be restarted before the new "
            "policy takes effect."
        ),
    )
    def update_nanobot_disabled_tools(
        request: Request,
        body: UpdateDisabledToolsRequest,
    ) -> DisabledToolsResponse:
        supervisor = _get_supervisor(request)
        return DisabledToolsResponse(
            disabled_tools=supervisor.update_disabled_tools(body.disabled_tools)
        )

    @api.get(
        "/skill-registry",
        response_model=SkillRegistryResponse,
        summary="List Global Skill Pools",
        description=(
            "Return the configured global skill pools and the skills loaded from each. Each pool path is "
            "walked recursively for subdirectories with a valid SKILL.md frontmatter; entries lacking a "
            "valid header are skipped (and logged) rather than returned here."
        ),
    )
    def get_skill_registry(request: Request) -> SkillRegistryResponse:
        supervisor = _get_supervisor(request)
        return _build_skill_registry_response(supervisor)

    @api.patch(
        "/skill-registry",
        response_model=SkillRegistryResponse,
        responses={400: {"model": ErrorResponse}},
        summary="Update Global Skill Pools",
        description=(
            "Replace the configured global skill pool list. Each entry is a pool directory whose "
            "subdirectories with valid SKILL.md frontmatter are loaded as skills. Persisting also reloads "
            "the in-memory pool cache."
        ),
    )
    def update_skill_registry(request: Request, body: UpdateSkillRegistryRequest) -> SkillRegistryResponse:
        supervisor = _get_supervisor(request)
        try:
            supervisor.update_skill_pools(body.items)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _build_skill_registry_response(supervisor)

    @api.post(
        "/skill-registry/refresh",
        response_model=SkillRegistryResponse,
        summary="Refresh Skill Pools",
        description=(
            "Re-walk every configured skill pool from disk and rebuild the in-memory skill cache. Use "
            "this after adding or modifying SKILL.md files inside a configured pool."
        ),
    )
    def refresh_skill_registry(request: Request) -> SkillRegistryResponse:
        supervisor = _get_supervisor(request)
        supervisor.refresh_skill_pools()
        return _build_skill_registry_response(supervisor)

    @api.get(
        "/cron-job-registry",
        response_model=CronJobRegistryResponse,
        summary="List Global Cron Job Registry",
        description=(
            "Return the configured global cron job registry: predefined cron job templates that souls can "
            "schedule, including optional recurring_session_key_format for dynamic session keys."
        ),
    )
    def get_cron_job_registry(request: Request) -> CronJobRegistryResponse:
        supervisor = _get_supervisor(request)
        return CronJobRegistryResponse(items=supervisor.list_cron_job_registry())

    @api.patch(
        "/cron-job-registry",
        response_model=CronJobRegistryResponse,
        responses={400: {"model": ErrorResponse}},
        summary="Update Global Cron Job Registry",
        description=(
            "Replace the configured global cron job registry. Each entry must have a unique name and at "
            "least one of cron_expr or every_seconds. Registry changes do not affect already-scheduled "
            "soul cron jobs."
        ),
    )
    def update_cron_job_registry(
        request: Request, body: UpdateCronJobRegistryRequest
    ) -> CronJobRegistryResponse:
        supervisor = _get_supervisor(request)
        try:
            supervisor.update_cron_job_registry(body.items)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return CronJobRegistryResponse(items=supervisor.list_cron_job_registry())

    @api.get(
        "/souls",
        response_model=list[SoulResponse],
        summary="List Souls",
        description=(
            "List all valid souls discovered under soulboard/souls, including their fixed workspace paths, "
            "workspace-local overrides, and whether each soul is currently running."
        ),
    )
    def list_souls(request: Request) -> list[SoulResponse]:
        supervisor = _get_supervisor(request)
        return [_to_soul_response(supervisor, spec) for spec in supervisor.list_specs()]

    @api.post(
        "/souls/refresh",
        response_model=list[SoulResponse],
        summary="Reload Souls Config",
        description=(
            "Reload the global soulboard config, base nanobot config, and workspace-local soul configs "
            "from disk. Running souls keep their current runtime config until manually restarted."
        ),
    )
    def refresh_souls(request: Request) -> list[SoulResponse]:
        supervisor = _get_supervisor(request)
        supervisor.reload_config()
        return [_to_soul_response(supervisor, spec) for spec in supervisor.list_specs()]

    @api.get(
        "/channels",
        response_model=list[str],
        summary="List Enabled Channels",
        description=(
            "List channel names enabled in the base nanobot config.json. Souls select from these names "
            "when enabling channel runtimes in their overrides."
        ),
    )
    def list_enabled_channels(request: Request) -> list[str]:
        supervisor = _get_supervisor(request)
        return supervisor.list_enabled_channels()

    @api.get(
        "/mcp-servers",
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

    @api.post(
        "/mcp-servers",
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

    @api.patch(
        "/mcp-servers/{name}",
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

    @api.delete(
        "/mcp-servers/{name}",
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

    @api.post(
        "/souls",
        response_model=SoulResponse,
        responses={400: {"model": ErrorResponse}},
        summary="Create Soul",
        description=(
            "Create a new soul workspace with its own config.json and return the resolved definition. "
            "If the local overrides set autostart=true, the new soul is started immediately."
        ),
    )
    async def create_soul(request: Request, body: CreateSoulRequest) -> SoulResponse:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.create_soul(
                body.soul_id,
                body.overrides,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _sync_soul_workspace(spec)
        if body.cron_job_registry_names:
            try:
                supervisor.add_cron_jobs_to_soul_from_registry(
                    body.soul_id, body.cron_job_registry_names
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if spec.overrides.autostart:
            await supervisor.start_soul(spec.soul_id)
        return _to_soul_response(supervisor, spec)

    @api.post(
        "/souls/{source_soul_id}/clone",
        response_model=SoulResponse,
        responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
        summary="Clone Soul",
        description=(
            "Clone selected configuration and workspace content into a new soul directory. Memory and "
            "sessions are always reset. The source may be running or stopped."
        ),
    )
    async def clone_soul(
        request: Request,
        source_soul_id: str,
        body: CloneSoulRequest,
    ) -> SoulResponse:
        supervisor = _get_supervisor(request)
        try:
            cron_jobs: list[SoulCloneCronJob] = []
            for item in body.cron_jobs:
                if item.schedule.kind == "cron":
                    if not item.schedule.expr:
                        raise ValueError(f"Cron job {item.name!r} requires a cron expression")
                    schedule = CronSchedule(
                        kind="cron",
                        expr=item.schedule.expr,
                        tz=item.schedule.tz,
                    )
                else:
                    if item.schedule.every_ms is None or item.schedule.every_ms <= 0:
                        raise ValueError(f"Cron job {item.name!r} requires a positive interval")
                    schedule = CronSchedule(kind="every", every_ms=item.schedule.every_ms)
                cron_jobs.append(SoulCloneCronJob(
                    name=item.name,
                    enabled=item.enabled,
                    schedule=schedule,
                    message=item.message,
                    origin_channel=item.origin_channel,
                    origin_chat_id=item.origin_chat_id,
                    origin_metadata=item.origin_metadata,
                    session_key=item.session_key,
                    recurring_session_key_format=item.recurring_session_key_format,
                    delete_after_run=item.delete_after_run,
                ))
            spec = supervisor.clone_soul(
                source_soul_id,
                body.soul_id,
                body.overrides,
                prompt_files={item.name: item.content for item in body.prompt_files},
                skill_names=body.skill_names,
                cron_jobs=cron_jobs,
            )
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _sync_soul_workspace(spec)
        if body.start:
            await supervisor.start_soul(spec.soul_id)
        return _to_soul_response(supervisor, spec)

    @api.get(
        "/souls/{soul_id}",
        response_model=SoulResponse,
        responses={404: {"model": ErrorResponse}},
        summary="Get Soul",
        description=(
            "Return one discovered soul, including its fixed workspace path, workspace-local overrides, "
            "and current running state."
        ),
    )
    def get_soul(request: Request, soul_id: str) -> SoulResponse:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        return _to_soul_response(supervisor, spec)

    @api.get(
        "/souls/{soul_id}/prompt-files",
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

    @api.get(
        "/souls/{soul_id}/cron-jobs",
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
        return [_to_cron_job_response(job) for job in jobs]

    @api.post(
        "/souls/{soul_id}/cron-jobs-from-registry",
        response_model=list[CronJobResponse],
        responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
        summary="Add Cron Jobs From Registry",
        description=(
            "Schedule one or more global cron job registry entries as cron jobs in the selected soul. "
            "Works whether the soul is running or stopped. Each name must exist in the global registry."
        ),
    )
    async def add_soul_cron_jobs_from_registry(
        request: Request, soul_id: str, body: AddSoulCronJobsFromRegistryRequest
    ) -> list[CronJobResponse]:
        supervisor = _get_supervisor(request)
        try:
            added = supervisor.add_cron_jobs_to_soul_from_registry(soul_id, body.names)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return [_to_cron_job_response(job) for job in added]

    @api.post(
        "/souls/{soul_id}/cron-jobs",
        response_model=CronJobResponse,
        responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
        summary="Add Soul Cron Job",
        description=(
            "Manually schedule a new cron job in the selected soul, without going through the global "
            "cron job registry. Works whether the soul is running or stopped."
        ),
    )
    async def add_soul_cron_job(
        request: Request, soul_id: str, body: CreateSoulCronJobRequest
    ) -> CronJobResponse:
        supervisor = _get_supervisor(request)
        if body.schedule.kind not in ("cron", "every"):
            raise HTTPException(
                status_code=400, detail=f"Unsupported schedule kind: {body.schedule.kind!r}"
            )
        if body.schedule.kind == "cron" and not body.schedule.expr:
            raise HTTPException(
                status_code=400, detail="schedule.expr is required for kind='cron'"
            )
        if body.schedule.kind == "every" and not body.schedule.every_ms:
            raise HTTPException(
                status_code=400, detail="schedule.every_ms is required for kind='every'"
            )
        schedule = CronSchedule(
            kind=body.schedule.kind,
            every_ms=body.schedule.every_ms,
            expr=body.schedule.expr,
            tz=body.schedule.tz,
        )
        try:
            job = supervisor.add_cron_job_to_soul(
                soul_id,
                name=body.name,
                schedule=schedule,
                message=body.message,
                session_key=body.session_key,
                origin_channel=body.origin_channel,
                origin_chat_id=body.origin_chat_id,
                origin_metadata=body.origin_metadata,
                recurring_session_key_format=body.recurring_session_key_format,
                delete_after_run=body.delete_after_run,
            )
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _to_cron_job_response(job)

    @api.delete(
        "/souls/{soul_id}/cron-jobs/{job_id}",
        status_code=204,
        responses={404: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
        summary="Delete Soul Cron Job",
        description="Remove a cron job from a soul. Works whether the soul is running or stopped.",
    )
    async def delete_soul_cron_job(request: Request, soul_id: str, job_id: str) -> None:
        supervisor = _get_supervisor(request)
        try:
            status = supervisor.remove_cron_job(soul_id, job_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        if status == "not_found":
            raise HTTPException(status_code=404, detail=f"Cron job {job_id!r} not found")
        if status == "protected":
            raise HTTPException(status_code=403, detail=f"Cron job {job_id!r} is a protected system job")

    @api.patch(
        "/souls/{soul_id}/cron-jobs/{job_id}",
        response_model=CronJobResponse,
        responses={404: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
        summary="Update Soul Cron Job",
        description="Update mutable fields of a soul cron job. Works whether the soul is running or stopped.",
    )
    async def update_soul_cron_job(
        request: Request, soul_id: str, job_id: str, body: UpdateSoulCronJobRequest
    ) -> CronJobResponse:
        supervisor = _get_supervisor(request)
        schedule = None
        if body.schedule is not None:
            schedule = CronSchedule(
                kind=body.schedule.kind,
                every_ms=body.schedule.every_ms,
                expr=body.schedule.expr,
                tz=body.schedule.tz,
            )
        try:
            result = supervisor.update_cron_job(
                soul_id,
                job_id,
                name=body.name,
                enabled=body.enabled,
                message=body.message,
                session_key=body.session_key if "session_key" in body.model_fields_set else ...,
                origin_channel=(
                    body.origin_channel if "origin_channel" in body.model_fields_set else ...
                ),
                origin_chat_id=(
                    body.origin_chat_id if "origin_chat_id" in body.model_fields_set else ...
                ),
                origin_metadata=(
                    body.origin_metadata if "origin_metadata" in body.model_fields_set else ...
                ),
                recurring_session_key_format=(
                    body.recurring_session_key_format
                    if "recurring_session_key_format" in body.model_fields_set
                    else ...
                ),
                delete_after_run=body.delete_after_run,
                schedule=schedule,
            )
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        if result == "not_found":
            raise HTTPException(status_code=404, detail=f"Cron job {job_id!r} not found")
        if result == "protected":
            raise HTTPException(status_code=403, detail=f"Cron job {job_id!r} is a protected system job")
        assert isinstance(result, CronJob)
        return _to_cron_job_response(result)

    @api.patch(
        "/souls/{soul_id}/prompt-files",
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

    @api.get(
        "/souls/{soul_id}/skills",
        response_model=list[SoulSkillResponse],
        responses={404: {"model": ErrorResponse}},
        summary="List Soul Skills",
        description=(
            "List skill directories present in this soul's workspace skills/ folder. Each entry includes "
            "parsed SKILL.md basics (name, description) and a link_target field that is set when the entry "
            "is a soft link into the global skill registry; otherwise it is a soul-specific writable copy."
        ),
    )
    def get_soul_skills(request: Request, soul_id: str) -> list[SoulSkillResponse]:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        return _list_soul_skills(spec)

    @api.post(
        "/souls/{soul_id}/skills",
        response_model=list[SoulSkillResponse],
        responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
        summary="Add Soul Skill From Pools",
        description=(
            "Materialize a skill from one of the configured global skill pools into this soul's "
            "workspace skills/ folder. Mode 'symlink' soft-links the pool skill directory; mode 'copy' "
            "creates an independent copy."
        ),
    )
    def add_soul_skill(request: Request, soul_id: str, body: AddSoulSkillRequest) -> list[SoulSkillResponse]:
        supervisor = _get_supervisor(request)
        try:
            supervisor.add_soul_skill_from_pools(
                soul_id,
                skill_path=body.skill_path,
                target_name=body.name,
                mode=body.mode,
            )
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _list_soul_skills(spec)

    @api.delete(
        "/souls/{soul_id}/skills/{name}",
        status_code=204,
        responses={404: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
        summary="Delete Soul Skill",
        description=(
            "Remove a skill from this soul's workspace skills/ folder. For symlinks, only the link is "
            "removed (the registry source is untouched). For directories, the entire skill directory is "
            "deleted from the soul workspace."
        ),
    )
    def delete_soul_skill(request: Request, soul_id: str, name: str) -> None:
        supervisor = _get_supervisor(request)
        try:
            supervisor.delete_soul_skill(soul_id, name)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.patch(
        "/souls/{soul_id}",
        response_model=SoulResponse,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        summary="Update Soul",
        description=(
            "Replace the workspace-local config.json for a soul. This endpoint refuses to modify a running "
            "soul so runtime state and persisted config cannot diverge."
        ),
    )
    def update_soul(request: Request, soul_id: str, body: UpdateSoulRequest) -> SoulResponse:
        supervisor = _get_supervisor(request)
        try:
            supervisor.modify_soul(soul_id, body.overrides)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        spec = supervisor.get_spec(soul_id)
        _sync_soul_workspace(spec)
        return _to_soul_response(supervisor, spec)

    @api.delete(
        "/souls/{soul_id}",
        status_code=204,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        summary="Delete Soul",
        description=(
            "Remove a soul and its complete workspace directory. The soul must not be running when this "
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

    @api.post(
        "/souls/{soul_id}/start",
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
            _sync_soul_workspace(spec)
            await supervisor.start_soul(soul_id)
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _to_soul_response(supervisor, spec)

    @api.post(
        "/souls/{soul_id}/stop",
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

    @api.get(
        "/souls/{soul_id}/sessions",
        response_model=SessionListResponse,
        responses={404: {"model": ErrorResponse}},
        summary="List Soul Sessions",
        description=(
            "Paged list of persisted session files for one soul workspace, sourced from the upstream "
            "nanobot SessionManager. Sessions are sorted by updated_at; pass order=asc to flip the "
            "direction. limit/offset slice the sorted list."
        ),
    )
    def list_soul_sessions(
        request: Request,
        soul_id: str,
        limit: int = Query(default=10, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        order: Literal["asc", "desc"] = Query(default="desc"),
    ) -> SessionListResponse:
        supervisor = _get_supervisor(request)
        try:
            spec = supervisor.get_spec(soul_id)
        except KeyError as exc:
            _raise_not_found(_error_detail(exc))
        manager = _get_session_manager(supervisor, spec)
        raw = manager.list_sessions()
        if order == "asc":
            raw = list(reversed(raw))
        total = len(raw)
        page = raw[offset : offset + limit]
        return SessionListResponse(
            items=[SessionSummaryResponse(**item) for item in page],
            total=total,
            limit=limit,
            offset=offset,
            order=order,
        )

    @api.post(
        "/souls/{soul_id}/sessions",
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
        return _build_session_detail_response(session)

    @api.get(
        "/souls/{soul_id}/sessions/{session_key}",
        response_model=SessionDetailResponse,
        responses={404: {"model": ErrorResponse}},
        summary="Get Session Detail",
        description=(
            "Load one persisted history window from a single session in the selected soul's workspace. "
            "Without paging arguments, this returns all unconsolidated messages from the last consolidation "
            "point onward. The session key should match the key used by nanobot SessionManager."
        ),
    )
    def get_session(
        request: Request,
        soul_id: str,
        session_key: str,
        before: int | None = Query(default=None, ge=0),
        limit: int | None = Query(default=None, ge=1),
    ) -> SessionDetailResponse:
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
        return _build_session_detail_response(session, before=before, limit=limit)

    @api.post(
        "/souls/{soul_id}/chat",
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
            try:
                supervisor.get_spec(soul_id)
            except KeyError:
                _raise_not_found(f"Unknown soul: {soul_id}")
            raise HTTPException(status_code=409, detail=_error_detail(exc)) from exc
        response = await agent_loop.process_direct(
            content=body.content,
            session_key=body.session_key,
            channel=body.channel,
            chat_id=body.chat_id,
        )
        return {"content": response.content if response is not None else ""}

    @api.websocket("/ws/souls/{soul_id}/chat")
    async def stream_chat(websocket: WebSocket, soul_id: str) -> None:
        await websocket.accept()
        supervisor = websocket.app.state.soulboard.supervisor
        chat_streams: ChatStreamManager = websocket.app.state.chat_streams
        session_key = websocket.query_params.get("session_key", "cli:direct")
        channel = websocket.query_params.get("channel", "cli")
        chat_id = websocket.query_params.get("chat_id", "direct")
        logger.info("WebSocket connected: soul={} session_key={} channel={} chat_id={}", soul_id, session_key, channel, chat_id)
        stream_key = (soul_id, session_key, channel, chat_id)
        try:
            agent_loop = supervisor.get_agent_loop(soul_id)
        except KeyError:
            await websocket.close(code=4404, reason=f"Soul is not running or does not exist: {soul_id}")
            return
        await chat_streams.connect(stream_key, websocket)

        while True:
            try:
                payload = await websocket.receive_json()
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected: soul={} session_key={} channel={} chat_id={}", soul_id, session_key, channel, chat_id)
                await chat_streams.disconnect(stream_key, websocket)
                break

            body = StreamInputMessage.model_validate(payload)
            await chat_streams.enqueue(
                stream_key,
                agent_loop,
                ChatRequest(
                    content=body.content,
                    session_key=session_key,
                    channel=channel,
                    chat_id=chat_id,
                ),
            )

    app.include_router(api)

    # Built frontend (vite build outputs to <repo root>/static). check_dir=False so
    # the server can start before the first frontend build.
    static_dir = Path(__file__).resolve().parent.parent / "static"
    frontend_prefix = f"{normalized_url_prefix}/" if normalized_url_prefix else "/"
    app.mount(
        f"{normalized_url_prefix}/assets",
        StaticFiles(directory=static_dir / "assets", check_dir=False),
        name="assets",
    )

    @app.get(frontend_prefix, include_in_schema=False)
    @app.get(f"{normalized_url_prefix}/{{frontend_path:path}}", include_in_schema=False)
    def index(frontend_path: str = "") -> HTMLResponse:
        if frontend_path == "api" or frontend_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        index_html = static_dir / "index.html"
        if not index_html.is_file():
            raise HTTPException(
                status_code=404,
                detail="Frontend is not built. Run `pnpm build` under frontend/ to generate static/.",
            )
        base_href = frontend_prefix
        content = index_html.read_text(encoding="utf-8")
        base_tag = f'<base href="{escape(base_href, quote=True)}" />'
        if re.search(r"<base\s+href=[\"'][^\"']*[\"']\s*/?>", content, re.IGNORECASE):
            content = re.sub(
                r"<base\s+href=[\"'][^\"']*[\"']\s*/?>",
                base_tag,
                content,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            content = content.replace("<head>", f"<head>\n    {base_tag}", 1)
        return HTMLResponse(content)

    return app
