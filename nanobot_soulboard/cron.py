"""Soulboard-local cron extensions."""

from pathlib import Path
from typing import Any

from loguru import logger
from nanobot.agent.tools.cron import CronTool
from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob


class SoulCronService(CronService):
    """Per-soul cron service with soulboard-specific helper accessors."""

    def __init__(self, store_path: Path, soul_id: str):
        super().__init__(store_path)
        self._soul_id = soul_id

    async def start(self) -> None:
        await super().start()
        logger.info(
            "Cron service started for soul '{}' with {} jobs",
            self._soul_id,
            len(self._store.jobs if self._store else []),
        )

    @staticmethod
    def _normalize_delivery_metadata(raw: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if not isinstance(raw, dict):
            return metadata
        if "is_group" in raw:
            metadata["is_group"] = bool(raw["is_group"])
        return metadata

    def get_session_key(self, job_id: str) -> str | None:
        store = self._load_store()
        job = next((item for item in store.jobs if item.id == job_id), None)
        if job is None:
            return None
        return job.payload.session_key

    def get_delivery_metadata(self, job_id: str) -> dict[str, Any]:
        store = self._load_store()
        job = next((item for item in store.jobs if item.id == job_id), None)
        if job is None:
            return {}
        return self._normalize_delivery_metadata(job.payload.channel_meta)

    def list_jobs_with_session_keys(
        self,
        include_disabled: bool = False,
    ) -> list[tuple[CronJob, str | None]]:
        """List jobs paired with the session key that created them."""
        return [
            (job, job.payload.session_key)
            for job in self.list_jobs(include_disabled=include_disabled)
        ]


class SoulCronTool(CronTool):
    """Cron tool variant that defaults list output to the current session."""

    def __init__(self, cron_service: SoulCronService, default_timezone: str = "UTC"):
        super().__init__(cron_service, default_timezone=default_timezone)
        self._cron: SoulCronService = cron_service

    def set_context(
        self,
        channel: str,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        normalized_metadata = self._cron._normalize_delivery_metadata(metadata)
        super().set_context(
            channel,
            chat_id,
            metadata=normalized_metadata,
            session_key=session_key,
        )

    @property
    def description(self) -> str:
        return (
            "Schedule reminders and recurring tasks. Actions: add, list, remove. "
            "List defaults to only the current session's jobs unless you explicitly disable that filter. "
            "For add, 'message' is the content that the future cron job will inject back into the agent loop "
            "when it runs, not an immediate reply to the current user."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        params = dict(super().parameters)
        properties = dict(params.get("properties", {}))
        properties["message"] = {
            "type": "string",
            "description": (
                "Content that the scheduled job will send back into the agent loop when it fires "
                "(for add). This becomes the future cron-triggered input, not an immediate user reply."
            ),
        }
        properties["only_current_session"] = {
            "type": "boolean",
            "description": (
                "For list only. Defaults to true and shows only cron jobs created by the current session. "
                "Set false to list all cron jobs for this soul."
            ),
            "default": True,
        }
        params["properties"] = properties
        return params

    async def execute(
        self,
        action: str,
        name: str | None = None,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        deliver: bool = True,
        only_current_session: bool = True,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            if self._in_cron_context.get():
                return "Error: cannot schedule new jobs from within a cron job execution"
            return self._add_job(
                name or message[:10],
                message,
                every_seconds,
                cron_expr,
                tz,
                at,
                deliver,
            )
        if action == "list":
            return self._list_jobs(only_current_session=only_current_session)
        if action == "remove":
            return self._remove_job(job_id)
        return f"Unknown action: {action}"

    def _list_jobs(self, *, only_current_session: bool = True) -> str:
        jobs_with_sessions = self._cron.list_jobs_with_session_keys()
        current_session_key = self._session_key.get()
        if only_current_session:
            jobs = [
                job
                for job, session_key in jobs_with_sessions
                if session_key == current_session_key
            ]
        else:
            jobs = [job for job, _session_key in jobs_with_sessions]
        if not jobs:
            return "No scheduled jobs."
        lines = []
        for job in jobs:
            timing = self._format_timing(job.schedule)
            parts = [f"- {job.name} (id: {job.id}, {timing})"]
            parts.extend(self._format_state(job.state, job.schedule))
            lines.append("\n".join(parts))
        return "Scheduled jobs:\n" + "\n".join(lines)
