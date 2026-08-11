"""
Template for sending prompts to nanobot soulboard.

Two places to modify are marked as TODOs.
"""

import asyncio
from collections.abc import Iterator
from datetime import date, timedelta
from urllib.parse import quote

import httpx
from tqdm import tqdm

from nanobot_soulboard.schemas import ChatRequest, ChatResponse

# TODO: modify the settings
START_DATE = date.fromisoformat("2026-01-01")
END_DATE = date.fromisoformat("2026-01-31")
SOUL_ID = "your-soul-id"
SESSION_PREFIX = "daily-task"
BACKEND = "http://127.0.0.1:18791"
WORKERS = 1


def validate_config() -> None:
    if START_DATE > END_DATE:
        raise ValueError("START_DATE must not be after END_DATE")
    if not SOUL_ID.strip():
        raise ValueError("SOUL_ID must not be empty")
    if not SESSION_PREFIX.strip():
        raise ValueError("SESSION_PREFIX must not be empty")
    if not BACKEND.strip():
        raise ValueError("BACKEND must not be empty")
    if WORKERS < 1:
        raise ValueError("WORKERS must be at least 1")

def dates_between(start: date, end: date) -> Iterator[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)

async def require_running_soul(client: httpx.AsyncClient, soul_id: str) -> None:
    response = await client.get(f"/soulboard/api/souls/{quote(soul_id, safe='')}")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("running") is not True:
        raise RuntimeError(f"Soul {soul_id!r} is not running")

async def send_prompt(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    soul_id: str,
    session_prefix: str,
    day: date,
) -> tuple[date, str]:
    session_key = f"{session_prefix}:{day.isoformat()}"
    request = ChatRequest(
        # TODO: modify the prompt
        content=(
            f"Assume that today is {day.isoformat()}. "
            f"Follow the instructions to complete your task today."
        ),
        session_key=session_key,
        channel="cli",
        chat_id=session_key,
    )
    async with semaphore:
        response = await client.post(
            f"/soulboard/api/souls/{quote(soul_id, safe='')}/chat",
            json=request.model_dump(),
        )
        response.raise_for_status()
        payload = ChatResponse.model_validate(response.json())
        return day, payload.content

async def run() -> None:
    validate_config()
    semaphore = asyncio.Semaphore(WORKERS)
    async with httpx.AsyncClient(base_url=BACKEND.rstrip("/"), timeout=None) as client:
        await require_running_soul(client, SOUL_ID)
        tasks = [
            asyncio.create_task(
                send_prompt(client, semaphore, SOUL_ID, SESSION_PREFIX, day)
            )
            for day in list(dates_between(START_DATE, END_DATE))
        ]
        with tqdm(total=len(tasks), desc="Sending prompts") as progress:
            for task in asyncio.as_completed(tasks):
                day, result = await task
                tqdm.write(f"[{day.isoformat()}] {result}")
                progress.update()


if __name__ == "__main__":
    asyncio.run(run())
