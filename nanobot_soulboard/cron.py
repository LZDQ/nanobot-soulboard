"""Soulboard-local cron extensions."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from nanobot.agent.tools.cron import CronTool
from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronJobState, CronPayload, CronRunRecord, CronSchedule


class SoulCronService(CronService):
    """Per-soul cron service that persists session-local delivery context for each job."""

    def __init__(self, store_path: Path, soul_id: str):
        super().__init__(store_path)
        self._soul_id = soul_id
        self._session_keys: dict[str, str] = {}
        self._delivery_metadata: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        await super().start()
        logger.info("Cron service started for soul '{}' with {} jobs", self._soul_id, len(self._store.jobs if self._store else []))

    def _load_jobs(self) -> tuple[list[CronJob], int]:
        """Load jobs from disk, including soulboard-only session keys stored per job."""
        self._session_keys = {}
        self._delivery_metadata = {}
        jobs: list[CronJob] = []
        version = 1
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                version = data.get("version", 1)
                for j in data.get("jobs", []):
                    job = CronJob(
                        id=j["id"],
                        name=j["name"],
                        enabled=j.get("enabled", True),
                        schedule=CronSchedule(
                            kind=j["schedule"]["kind"],
                            at_ms=j["schedule"].get("atMs"),
                            every_ms=j["schedule"].get("everyMs"),
                            expr=j["schedule"].get("expr"),
                            tz=j["schedule"].get("tz"),
                        ),
                        payload=CronPayload(
                            kind=j["payload"].get("kind", "agent_turn"),
                            message=j["payload"].get("message", ""),
                            deliver=j["payload"].get("deliver", False),
                            channel=j["payload"].get("channel"),
                            to=j["payload"].get("to"),
                        ),
                        state=CronJobState(
                            next_run_at_ms=j.get("state", {}).get("nextRunAtMs"),
                            last_run_at_ms=j.get("state", {}).get("lastRunAtMs"),
                            last_status=j.get("state", {}).get("lastStatus"),
                            last_error=j.get("state", {}).get("lastError"),
                            run_history=[
                                CronRunRecord(
                                    run_at_ms=r["runAtMs"],
                                    status=r["status"],
                                    duration_ms=r.get("durationMs", 0),
                                    error=r.get("error"),
                                )
                                for r in j.get("state", {}).get("runHistory", [])
                            ],
                        ),
                        created_at_ms=j.get("createdAtMs", 0),
                        updated_at_ms=j.get("updatedAtMs", 0),
                        delete_after_run=j.get("deleteAfterRun", False),
                    )
                    jobs.append(job)
                    session_key = j.get("sessionKey")
                    if isinstance(session_key, str) and session_key:
                        self._session_keys[job.id] = session_key
                    delivery_metadata = self._normalize_delivery_metadata(j.get("deliveryMetadata"))
                    if delivery_metadata:
                        self._delivery_metadata[job.id] = delivery_metadata
            except Exception as exc:
                logger.warning("Failed to load cron store: {}", exc)
                jobs = []
                self._session_keys = {}
                self._delivery_metadata = {}
        return jobs, version

    def _save_store(self) -> None:
        """Save jobs to disk, including soulboard-only session keys per job."""
        if not self._store:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "enabled": job.enabled,
                    "schedule": {
                        "kind": job.schedule.kind,
                        "atMs": job.schedule.at_ms,
                        "everyMs": job.schedule.every_ms,
                        "expr": job.schedule.expr,
                        "tz": job.schedule.tz,
                    },
                    "payload": {
                        "kind": job.payload.kind,
                        "message": job.payload.message,
                        "deliver": job.payload.deliver,
                        "channel": job.payload.channel,
                        "to": job.payload.to,
                    },
                    "state": {
                        "nextRunAtMs": job.state.next_run_at_ms,
                        "lastRunAtMs": job.state.last_run_at_ms,
                        "lastStatus": job.state.last_status,
                        "lastError": job.state.last_error,
                        "runHistory": [
                            {
                                "runAtMs": run.run_at_ms,
                                "status": run.status,
                                "durationMs": run.duration_ms,
                                "error": run.error,
                            }
                            for run in job.state.run_history
                        ],
                    },
                    "createdAtMs": job.created_at_ms,
                    "updatedAtMs": job.updated_at_ms,
                    "deleteAfterRun": job.delete_after_run,
                    "sessionKey": self._session_keys.get(job.id),
                    "deliveryMetadata": self._delivery_metadata.get(job.id),
                }
                for job in self._store.jobs
            ],
        }
        self.store_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _normalize_delivery_metadata(raw: Any) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        if not isinstance(raw, dict):
            return metadata
        if "is_group" in raw:
            metadata["is_group"] = bool(raw["is_group"])
        return metadata

    def get_session_key(self, job_id: str) -> str | None:
        self._load_store()
        return self._session_keys.get(job_id)

    def get_delivery_metadata(self, job_id: str) -> dict[str, Any]:
        self._load_store()
        metadata = self._delivery_metadata.get(job_id, {})
        return dict(metadata)

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
        *,
        session_key: str | None = None,
        delivery_metadata: dict[str, Any] | None = None,
    ) -> CronJob:
        job = super().add_job(
            name=name,
            schedule=schedule,
            message=message,
            deliver=deliver,
            channel=channel,
            to=to,
            delete_after_run=delete_after_run,
        )
        normalized_metadata = self._normalize_delivery_metadata(delivery_metadata)
        self._load_store()
        stored_job = next((item for item in self._store.jobs if item.id == job.id), None)
        if stored_job is None:
            stored_job = job
            self._store.jobs.append(stored_job)
        if session_key:
            self._session_keys[stored_job.id] = session_key
        if normalized_metadata:
            self._delivery_metadata[stored_job.id] = normalized_metadata
        self._save_store()
        return stored_job

    def remove_job(self, job_id: str) -> bool:
        result = super().remove_job(job_id)
        if result == "removed":
            self._load_store()
            self._session_keys.pop(job_id, None)
            self._delivery_metadata.pop(job_id, None)
            self._save_store()
            return True
        return False

    def list_jobs_with_session_keys(self, include_disabled: bool = False) -> list[tuple[CronJob, str | None]]:
        """List jobs paired with the session key that created them."""
        self._load_store()
        return [(job, self._session_keys.get(job.id)) for job in self.list_jobs(include_disabled=include_disabled)]


class SoulCronTool(CronTool):
    """Cron tool variant that remembers which session scheduled the job."""

    def __init__(self, cron_service: SoulCronService, default_timezone: str = "UTC"):
        super().__init__(cron_service, default_timezone=default_timezone)
        self._cron: SoulCronService = cron_service
        self._session_key = ""
        self._delivery_metadata: dict[str, Any] = {}

    def set_context(
        self,
        channel: str,
        chat_id: str,
        session_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().set_context(channel, chat_id)
        self._session_key = session_key or ""
        self._delivery_metadata = self._cron._normalize_delivery_metadata(metadata)

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

    def _add_job(
        self,
        message: str,
        every_seconds: int | None,
        cron_expr: str | None,
        tz: str | None,
        at: str | None,
    ) -> str:
        if not message:
            return "Error: message is required for add"
        if not self._channel or not self._chat_id:
            return "Error: no session context (channel/chat_id)"
        if tz and not cron_expr:
            return "Error: tz can only be used with cron_expr"
        if tz:
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(tz)
            except (KeyError, Exception):
                return f"Error: unknown timezone '{tz}'"

        delete_after = False
        if every_seconds:
            schedule = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            effective_tz = tz or self._default_timezone
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(effective_tz)
            except (KeyError, Exception):
                return f"Error: unknown timezone '{effective_tz}'"
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=effective_tz)
        elif at:
            from zoneinfo import ZoneInfo

            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return f"Error: invalid ISO datetime format '{at}'. Expected format: YYYY-MM-DDTHH:MM:SS"
            if dt.tzinfo is None:
                try:
                    dt = dt.replace(tzinfo=ZoneInfo(self._default_timezone))
                except (KeyError, Exception):
                    return f"Error: unknown timezone '{self._default_timezone}'"
            schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
            delete_after = True
        else:
            return "Error: either every_seconds, cron_expr, or at is required"

        job = self._cron.add_job(
            name=message[:30],
            schedule=schedule,
            message=message,
            deliver=True,
            channel=self._channel,
            to=self._chat_id,
            delete_after_run=delete_after,
            session_key=self._session_key or None,
            delivery_metadata=self._delivery_metadata or None,
        )
        return f"Created job '{job.name}' (id: {job.id})"

    async def execute(
        self,
        action: str,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        tz: str | None = None,
        at: str | None = None,
        job_id: str | None = None,
        only_current_session: bool = True,
        **kwargs: Any,
    ) -> str:
        if action == "add":
            if self._in_cron_context.get():
                return "Error: cannot schedule new jobs from within a cron job execution"
            return self._add_job(message, every_seconds, cron_expr, tz, at)
        if action == "list":
            return self._list_jobs(only_current_session=only_current_session)
        if action == "remove":
            return self._remove_job(job_id)
        return f"Unknown action: {action}"

    def _list_jobs(self, *, only_current_session: bool = True) -> str:
        jobs_with_sessions = self._cron.list_jobs_with_session_keys()
        if only_current_session:
            jobs = [job for job, session_key in jobs_with_sessions if session_key == self._session_key]
        else:
            jobs = [job for job, _session_key in jobs_with_sessions]
        if not jobs:
            return "No scheduled jobs."
        lines = []
        for j in jobs:
            timing = self._format_timing(j.schedule)
            parts = [f"- {j.name} (id: {j.id}, {timing})"]
            parts.extend(self._format_state(j.state, j.schedule))
            lines.append("\n".join(parts))
        return "Scheduled jobs:\n" + "\n".join(lines)
