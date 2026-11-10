"""An auto-thread whose sender or parent channel is withdrawn mid-create is not granted.

Recovering a thread whose create response was lost reads it back through a lookup the
client's mid-send re-check skips, so ``receive`` reads both rosters again once the
create returns, as ``readmit_own_thread`` does after its own lookup.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from discord_helpers import _drop_live_config_snapshot  # noqa: F401
from discord_helpers import FakeClient

from kiro_crew.discord.client import DiscordInbound
from kiro_crew.discord.transport import DiscordTransport
from kiro_crew.messaging.transport import InboundMessage


@pytest.fixture(autouse=True)
def _inbound_permitted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _yes(_channel: str) -> bool:
        return True

    monkeypatch.setattr("kiro_crew.discord.transport.channel_inbound_permitted", _yes)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "kiro_crew.discord.transport.sel",
        lambda: SimpleNamespace(log_api_access=lambda **kw: seen.append(kw)),
    )
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("withdrawn", [None, "sender", "channel"])
async def test_a_grant_follows_the_rosters_as_the_create_returns(
    withdrawn: str | None, events: list[dict[str, Any]]
) -> None:
    dispatched: list[InboundMessage] = []

    async def _dispatch(m: InboundMessage) -> None:
        dispatched.append(m)

    client = FakeClient()
    t = DiscordTransport(
        client,  # type: ignore[arg-type]
        allowed_user_ids=["u1"],
        allowed_channel_ids=["c1"],
        dispatch=_dispatch,
    )
    create = client.create_thread_from_message

    async def _create_then_reload(channel_id: str, message_id: str, name: str) -> str:
        thread_id = await create(channel_id, message_id, name)
        # A live reload landing while the create (and any recovery lookup) waited.
        t.reconfigure(
            SimpleNamespace(
                allowed_user_ids=[] if withdrawn == "sender" else ["u1"],
                allowed_channel_ids=[] if withdrawn == "channel" else ["c1"],
                allowed_thread_ids=[],
                auto_thread=True,
            )
        )
        return thread_id

    client.create_thread_from_message = _create_then_reload  # type: ignore[method-assign]
    await t.receive(
        DiscordInbound(channel_id="c1", user_id="u1", text="hi", message_id="m1", guild_id="g1")
    )

    granted = "thread-m1" in t._allowed_threads
    assert granted is (withdrawn is None)
    assert bool(dispatched) is (withdrawn is None)
    outcomes = [e["outcome"] for e in events]
    assert ("thread_withdrawn_during_create" in outcomes) is (withdrawn is not None)
