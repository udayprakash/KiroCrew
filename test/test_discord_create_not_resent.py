"""A Discord write that may already have landed is not sent again.

A message create is applied once per copy Discord receives, and a 5xx, a timeout
or a dropped response can follow a write Discord already made, so re-sending it
posts the reply twice. It is retried only when Discord certainly did not apply
it: after a 429, or when the connection failed before the request went out.

A thread started from a message can be created only once, so the retry of a
create whose response was lost is refused (code 160004). The thread then exists
under the message's own id, and the client adopts it there, when Discord reports
this bot opened it, instead of reporting a failure that drops the user's message.
No other failure is looked up.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest
from test_discord_rate_limits import _harness, _Resp

from kiro_crew.discord import client as dc
from kiro_crew.discord.client import DISCORD_OK, DISCORD_TRANSIENT

_CREATE = "/channels/9111/messages"


def _already() -> _Resp:
    return _Resp(400, {"code": 160004, "message": "A thread has already been created"})


def _thread(*, owner: str) -> _Resp:
    return _Resp(200, {"id": "555", "type": 11, "parent_id": "9111", "owner_id": owner})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [_Resp(500), asyncio.TimeoutError(), aiohttp.ServerDisconnectedError()],
    ids=["5xx", "timeout", "disconnect"],
)
async def test_a_create_that_may_have_landed_is_sent_once(
    monkeypatch: pytest.MonkeyPatch, failure: object
) -> None:
    harness = _harness(monkeypatch, [failure, _Resp(200, {"id": "2"})])
    result = await harness.client.api_json("POST", _CREATE, {"content": "hi"})
    assert result.outcome == DISCORD_TRANSIENT
    assert len(harness.requests) == 1


@pytest.mark.asyncio
async def test_a_create_whose_connection_never_opened_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = aiohttp.ClientConnectorError(None, OSError("refused"))  # type: ignore[arg-type]
    harness = _harness(monkeypatch, [refused, _Resp(200, {"id": "2"})])
    result = await harness.client.api_json("POST", _CREATE, {"content": "hi"})
    assert result.outcome == DISCORD_OK and result.message_id == "2"
    assert len(harness.requests) == 2


@pytest.mark.asyncio
async def test_a_rate_limited_create_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _harness(monkeypatch, [_Resp(429, {"retry_after": 0.5}), _Resp(200, {"id": "2"})])
    result = await harness.client.api_json("POST", _CREATE, {"content": "hi"})
    assert result.outcome == DISCORD_OK and result.message_id == "2"
    assert len(harness.requests) == 2


@pytest.mark.asyncio
async def test_a_thread_whose_create_response_was_lost_is_still_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness(monkeypatch, [asyncio.TimeoutError(), _already(), _thread(owner="bot")])
    harness.client.bot_user_id = "bot"
    thread_id = await harness.client.create_thread_from_message("9111", "555", "topic")
    assert thread_id == "555"
    assert harness.requests[-1] == f"GET {dc._API_BASE}/channels/555"


@pytest.mark.asyncio
async def test_a_thread_another_member_opened_is_not_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same opener test ``readmit_own_thread`` applies after a restart."""
    harness = _harness(monkeypatch, [_already(), _thread(owner="someone-else")])
    harness.client.bot_user_id = "bot"
    assert await harness.client.create_thread_from_message("9111", "555", "topic") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [_Resp(403, {"code": 50013, "message": "Missing Permissions"}), asyncio.TimeoutError()],
    ids=["permission", "timeout"],
)
async def test_any_other_thread_failure_looks_nothing_up(
    monkeypatch: pytest.MonkeyPatch, failure: object
) -> None:
    """Only the "already created" refusal says a thread exists; a refusal or a
    withdrawn destination must stay a failure, never be reversed by a lookup."""
    harness = _harness(monkeypatch, [failure, failure, failure, _thread(owner="bot")])
    harness.client.bot_user_id = "bot"
    assert await harness.client.create_thread_from_message("9111", "555", "topic") is None
    assert all(" /channels/555" not in r and not r.startswith("GET") for r in harness.requests)
