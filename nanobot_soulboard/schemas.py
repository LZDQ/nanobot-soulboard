"""API and websocket schemas for nanobot-soulboard."""

from typing import Any

from pydantic import BaseModel, Field

from nanobot.config.schema import MCPServerConfig
from nanobot_soulboard.config import SoulOverrides


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


class StreamInputMessage(BaseModel):
    """Inbound websocket chat payload."""

    content: str = Field(min_length=1)


class CreateSessionRequest(BaseModel):
    """Request body for creating one empty persisted session."""

    key: str = Field(
        min_length=1,
        description="Session key used by nanobot SessionManager, typically in channel:chat_id form.",
    )


class AppLinksResponse(BaseModel):
    """Configured hero-bar app links."""

    items: list[str]


class UpdateAppLinksRequest(BaseModel):
    """Replace the configured hero-bar app links."""

    items: list[str]


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


class SoulSkillResponse(BaseModel):
    """One workspace skill visible to the soul."""

    name: str
    path: str
    content: str


class SoulResponse(BaseModel):
    """Soul summary returned by the API."""

    soul_id: str
    workspace: str
    skills: list[SoulSkillResponse]
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
    history_start: int
    history_end: int
    total_messages: int
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
