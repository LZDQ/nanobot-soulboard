"""API and websocket schemas for nanobot-soulboard."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from nanobot.config.schema import MCPServerConfig
from nanobot_soulboard.config import CronJobRegistryEntry, SoulOverrides


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
    cron_job_registry_names: list[str] = Field(
        default_factory=list,
        description=(
            "Names of global cron job registry entries to add to the soul at creation time. Each name "
            "must exist in the soulboard cron_job_registry."
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
    timestamp: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class SoulSkillResponse(BaseModel):
    """One workspace skill visible to the soul."""

    name: str
    path: str
    content: str
    description: str | None = None
    char_count: int | None = Field(
        default=None,
        description="Character count for SKILL.md only; excludes any sibling files.",
    )
    word_count: int | None = Field(
        default=None,
        description="Whitespace-delimited word count for SKILL.md only; excludes any sibling files.",
    )
    line_count: int | None = Field(
        default=None,
        description="Line count for SKILL.md only; excludes any sibling files.",
    )
    link_target: str | None = Field(
        default=None,
        description=(
            "Resolved symlink target if this skill directory is a soft link, otherwise None for a "
            "soul-specific writable copy."
        ),
    )


class SkillPoolEntryResponse(BaseModel):
    """One skill discovered inside a configured pool."""

    skill_path: str = Field(description="Absolute path to the skill directory.")
    relative_path: str = Field(description="Path relative to the pool root, posix-style.")
    name: str = Field(description="Skill name from SKILL.md frontmatter.")
    description: str | None = None
    char_count: int | None = Field(
        default=None,
        description="Character count for SKILL.md only; excludes any sibling files.",
    )
    word_count: int | None = Field(
        default=None,
        description="Whitespace-delimited word count for SKILL.md only; excludes any sibling files.",
    )
    line_count: int | None = Field(
        default=None,
        description="Line count for SKILL.md only; excludes any sibling files.",
    )


class SkillPoolResponse(BaseModel):
    """One configured skill pool with its discovered skills."""

    path: str = Field(description="The configured pool path (raw, unexpanded).")
    exists: bool = Field(description="Whether the pool path resolves to a directory on disk.")
    skills: list[SkillPoolEntryResponse]


class SkillRegistryResponse(BaseModel):
    """Configured global skill pools and their loaded skills."""

    pools: list[SkillPoolResponse]


class UpdateSkillRegistryRequest(BaseModel):
    """Replace the configured global skill pool list."""

    items: list[str]


class AddSoulSkillRequest(BaseModel):
    """Request body for adding a skill loaded from the pools to a soul."""

    skill_path: str = Field(
        description=(
            "Absolute path to a skill directory. Must be a skill discovered inside one of the "
            "configured global skill pools."
        )
    )
    name: str | None = Field(
        default=None,
        description=(
            "Optional override for the skill directory name inside the soul workspace. Defaults to the "
            "skill's directory basename."
        ),
    )
    mode: Literal["symlink", "copy"] = Field(
        default="symlink",
        description=(
            "How to materialize the skill into the soul workspace. 'symlink' soft-links the directory so "
            "the soul tracks the pool source live. 'copy' creates an independent writable copy."
        ),
    )


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


class SessionListResponse(BaseModel):
    """Paged session list for one soul workspace."""

    items: list[SessionSummaryResponse]
    total: int = Field(description="Total session count for this soul (across all pages).")
    limit: int
    offset: int
    order: Literal["asc", "desc"]


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
    recurring_session_key_format: str | None = None
    schedule: CronJobScheduleResponse
    state: CronJobStateResponse


class CronJobRegistryResponse(BaseModel):
    """Global cron job registry."""

    items: list[CronJobRegistryEntry]


class UpdateCronJobRegistryRequest(BaseModel):
    """Replace the global cron job registry."""

    items: list[CronJobRegistryEntry]


class AddSoulCronJobsFromRegistryRequest(BaseModel):
    """Add selected registry cron jobs to a soul."""

    names: list[str] = Field(description="Registry entry names to schedule in the soul.")


class UpdateSoulCronJobScheduleRequest(BaseModel):
    """Updated schedule for a soul cron job."""

    kind: str
    every_ms: int | None = None
    expr: str | None = None
    tz: str | None = None


class UpdateSoulCronJobRequest(BaseModel):
    """Editable fields of a soul cron job."""

    name: str | None = None
    enabled: bool | None = None
    message: str | None = None
    deliver: bool | None = None
    channel: str | None = None
    chat_id: str | None = None
    session_key: str | None = None
    recurring_session_key_format: str | None = None
    delete_after_run: bool | None = None
    schedule: UpdateSoulCronJobScheduleRequest | None = None


class CreateSoulCronJobRequest(BaseModel):
    """Manual per-soul cron job creation payload."""

    name: str = Field(min_length=1, description="Display name for the new job.")
    message: str = Field(default="", description="Message the job will inject when it fires.")
    deliver: bool = Field(default=False)
    channel: str | None = None
    chat_id: str | None = None
    session_key: str | None = None
    recurring_session_key_format: str | None = None
    delete_after_run: bool = False
    schedule: UpdateSoulCronJobScheduleRequest = Field(
        description="Schedule definition. Only kind='cron' (with expr) and kind='every' (with every_ms) are accepted."
    )


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


class ToolCatalogItemResponse(BaseModel):
    """One nanobot tool available for soulboard policy configuration."""

    name: str
    description: str


class DisabledToolsResponse(BaseModel):
    """Nanobot tools disabled for souls by default."""

    disabled_tools: list[str]


class UpdateDisabledToolsRequest(BaseModel):
    """Replace the global disabled tool list."""

    disabled_tools: list[str]


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
