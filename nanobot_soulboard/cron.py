"""Soulboard-local cron extensions."""

import json
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from loguru import logger
from nanobot.agent.tools.cron import CronTool
from nanobot.cron.service import CronService, _compute_next_run, _validate_schedule_for_add
from nanobot.cron.types import (
    CronJob,
    CronJobState,
    CronPayload,
    CronRunRecord,
    CronSchedule,
)


@dataclass
class SoulCronPayload(CronPayload):
    """CronPayload extended with soulboard-specific fields.

    ``recurring_session_key_format`` — when set to a strftime-style format
    (e.g. ``"%Y-%m-%d"``), the runtime renders the effective session_key by
    formatting the firing-time datetime instead of using the stored
    ``session_key``. The rendered session is created on disk on demand.
    Validation is intentionally omitted; an invalid format surfaces only when
    the job fires, and is logged and falls back to ``session_key``.
    """

    recurring_session_key_format: str | None = None


def _payload_from_persisted(payload: dict) -> SoulCronPayload:
    """Build a SoulCronPayload from the camelCase form written to jobs.json."""
    return SoulCronPayload(
        kind=payload.get("kind", "agent_turn"),
        message=payload.get("message", ""),
        deliver=payload.get("deliver", False),
        channel=payload.get("channel"),
        to=payload.get("to"),
        channel_meta=(
            payload.get("channelMeta") or payload.get("channel_meta") or {}
        ),
        session_key=payload.get("sessionKey") or payload.get("session_key"),
        recurring_session_key_format=(
            payload.get("recurringSessionKeyFormat")
            or payload.get("recurring_session_key_format")
        ),
    )


def _payload_to_persisted(payload: SoulCronPayload) -> dict:
    """Serialize a SoulCronPayload to its jobs.json form."""
    return {
        "kind": payload.kind,
        "message": payload.message,
        "deliver": payload.deliver,
        "channel": payload.channel,
        "to": payload.to,
        "channelMeta": payload.channel_meta,
        "sessionKey": payload.session_key,
        "recurringSessionKeyFormat": payload.recurring_session_key_format,
    }


def _payload_from_action_params(payload: dict) -> SoulCronPayload:
    """Build a SoulCronPayload from snake_case asdict-shaped action params."""
    return SoulCronPayload(
        kind=payload.get("kind", "agent_turn"),
        message=payload.get("message", ""),
        deliver=payload.get("deliver", False),
        channel=payload.get("channel"),
        to=payload.get("to"),
        channel_meta=(
            payload.get("channel_meta") or payload.get("channelMeta") or {}
        ),
        session_key=payload.get("session_key") or payload.get("sessionKey"),
        recurring_session_key_format=(
            payload.get("recurring_session_key_format")
            or payload.get("recurringSessionKeyFormat")
        ),
    )


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

    def _load_jobs(self) -> tuple[list[CronJob], int] | None:
        """Override parent loader so SoulCronPayload fields survive a round-trip.

        Mirrors upstream's corruption-preservation contract: returns ``None``
        when the store file exists but cannot be parsed, after renaming the
        bad file with a ``.corrupt-<ts>`` suffix. The parent ``_load_store``
        and ``start`` use that ``None`` to avoid silently overwriting a
        recoverable on-disk store with an empty job list.
        """
        jobs: list[CronJob] = []
        version = 1
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                version = data.get("version", 1)
                for j in data.get("jobs", []):
                    jobs.append(CronJob(
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
                        payload=_payload_from_persisted(j.get("payload", {})),
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
                    ))
            except Exception:
                backup = self.store_path.with_suffix(
                    self.store_path.suffix + f".corrupt-{int(time.time())}"
                )
                with suppress(OSError):
                    self.store_path.rename(backup)
                logger.exception(
                    "Failed to load soul cron store at {}. "
                    "Corrupt file preserved at {}. "
                    "Refusing to overwrite to avoid data loss.",
                    self.store_path,
                    backup,
                )
                return None
        return jobs, version

    def _save_store(self) -> None:
        """Override parent saver to persist SoulCronPayload fields."""
        if not self._store:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": _payload_to_persisted(j.payload),
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                        "runHistory": [
                            {
                                "runAtMs": r.run_at_ms,
                                "status": r.status,
                                "durationMs": r.duration_ms,
                                "error": r.error,
                            }
                            for r in j.state.run_history
                        ],
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in self._store.jobs
            ],
        }

        self._atomic_write(
            self.store_path, json.dumps(data, indent=2, ensure_ascii=False)
        )

    def _merge_action(self) -> None:
        """Override parent so action.jsonl entries reconstruct SoulCronPayload."""
        if not self._action_path.exists() or self._store is None:
            return

        jobs_map = {j.id: j for j in self._store.jobs}

        with self._lock:
            with open(self._action_path, "r", encoding="utf-8") as f:
                changed = False
                for line in f:
                    try:
                        line = line.strip()
                        if not line:
                            continue
                        action = json.loads(line)
                        if "action" not in action:
                            continue
                        params = action.get("params", {})
                        if action["action"] == "del":
                            if job_id := params.get("job_id"):
                                jobs_map.pop(job_id, None)
                        else:
                            j = self._job_from_action_params(params)
                            jobs_map[j.id] = j
                        changed = True
                    except Exception as exp:
                        logger.debug(f"load action line error: {exp}")
                        continue
            self._store.jobs = list(jobs_map.values())
            if self._running and changed:
                self._action_path.write_text("", encoding="utf-8")
                self._save_store()

    @staticmethod
    def _job_from_action_params(params: dict) -> CronJob:
        """Mirror CronJob.from_dict but route payload through SoulCronPayload.

        ``_append_action`` writes ``asdict(job)`` (snake_case), so we read with
        snake_case keys here.
        """
        kwargs = dict(params)
        state_kwargs = dict(kwargs.get("state", {}))
        state_kwargs["run_history"] = [
            r if isinstance(r, CronRunRecord) else CronRunRecord(**r)
            for r in state_kwargs.get("run_history", [])
        ]
        kwargs["schedule"] = CronSchedule(**kwargs.get("schedule", {"kind": "every"}))
        kwargs["payload"] = _payload_from_action_params(kwargs.get("payload", {}))
        kwargs["state"] = CronJobState(**state_kwargs)
        return CronJob(**kwargs)

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
        channel_meta: dict | None = None,
        session_key: str | None = None,
        recurring_session_key_format: str | None = None,
    ) -> CronJob:
        """Add a new job whose payload is always a SoulCronPayload."""
        _validate_schedule_for_add(schedule)
        now = int(time.time() * 1000)
        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=SoulCronPayload(
                kind="agent_turn",
                message=message,
                deliver=deliver,
                channel=channel,
                to=to,
                channel_meta=channel_meta or {},
                session_key=session_key,
                recurring_session_key_format=recurring_session_key_format,
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )
        if self._running:
            store = self._load_store()
            store.jobs.append(job)
            self._save_store()
            self._arm_timer()
        else:
            self._append_action("add", asdict(job))
        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    def update_job(
        self,
        job_id: str,
        *,
        name: str | None = None,
        schedule: CronSchedule | None = None,
        message: str | None = None,
        deliver: bool | None = None,
        channel: str | None = ...,
        to: str | None = ...,
        delete_after_run: bool | None = None,
        session_key: str | None = ...,
        recurring_session_key_format: str | None = ...,
    ) -> CronJob | Any:
        """Update mutable fields of a soul cron job.

        Extends upstream CronService.update_job with soul-only payload fields
        (session_key, recurring_session_key_format). Sentinel ``...`` means
        ``leave unchanged``; an explicit ``None`` clears the field.
        """
        store = self._load_store()
        job = next((j for j in store.jobs if j.id == job_id), None)
        if job is None:
            return "not_found"
        if job.payload.kind == "system_event":
            return "protected"

        if schedule is not None:
            _validate_schedule_for_add(schedule)
            job.schedule = schedule
        if name is not None:
            job.name = name
        if message is not None:
            job.payload.message = message
        if deliver is not None:
            job.payload.deliver = deliver
        if channel is not ...:
            job.payload.channel = channel
        if to is not ...:
            job.payload.to = to
        if session_key is not ...:
            job.payload.session_key = session_key
        if recurring_session_key_format is not ...:
            assert isinstance(job.payload, SoulCronPayload), (
                "Soul cron job payload must be SoulCronPayload"
            )
            job.payload.recurring_session_key_format = recurring_session_key_format
        if delete_after_run is not None:
            job.delete_after_run = delete_after_run

        now = int(time.time() * 1000)
        job.updated_at_ms = now
        if job.enabled:
            job.state.next_run_at_ms = _compute_next_run(job.schedule, now)

        if self._running:
            self._save_store()
            self._arm_timer()
        else:
            self._append_action("update", asdict(job))

        logger.info("Cron: updated soul job '{}' ({})", job.name, job.id)
        return job

    def register_system_job(self, job: CronJob) -> CronJob:
        """Promote payload to SoulCronPayload before delegating to parent."""
        if not isinstance(job.payload, SoulCronPayload):
            base = job.payload
            job.payload = SoulCronPayload(
                kind=base.kind,
                message=base.message,
                deliver=base.deliver,
                channel=base.channel,
                to=base.to,
                channel_meta=base.channel_meta,
                session_key=base.session_key,
            )
        return super().register_system_job(job)

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
