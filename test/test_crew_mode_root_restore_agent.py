"""A restored agent line that disagrees with the durable selection, and the save after it."""

from __future__ import annotations

import logging

import pytest

from kiro_crew.dashboard.slot_persistence.restore_inputs import _restored_agent_name
from kiro_crew.execution_context import ExecutionContext, MemoryStoreRef, bind_session_execution

KEY = "dashboard:crew-root"
LEAD = "kirocrew-team-lead"


def _default_record(key: str, *, replace: bool = False) -> None:
    bind_session_execution(
        key,
        ExecutionContext(
            None, MemoryStoreRef("default"), "template", "kirocrew", selection_name="kirocrew"
        ),
        replace_existing=replace,
    )


def test_a_disagreeing_line_restores_the_record_and_warns(tmp_path, monkeypatch, caplog):
    # The record is canonical; the line is editable by the session itself.
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    _default_record(KEY)
    meta = {"agent": LEAD, "agent_kind": "template", "crew_mode_agent": LEAD}
    with caplog.at_level(logging.WARNING):
        assert _restored_agent_name(KEY, meta) == "kirocrew"
    warned = [r.getMessage() for r in caplog.records if "disagrees" in r.getMessage()]
    assert len(warned) == 1
    assert KEY in warned[0] and repr(LEAD) in warned[0] and "'kirocrew'" in warned[0]


def test_an_agreeing_line_restores_quietly(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))
    _default_record(KEY)
    with caplog.at_level(logging.WARNING):
        assert _restored_agent_name(KEY, {"agent": "kirocrew"}) == "kirocrew"
    assert "disagrees" not in caplog.text


def test_a_save_that_keeps_foreign_rows_keeps_the_agent_binding(tmp_path, monkeypatch, caplog):
    """A slot built on the key without the line saves a short window: the rows on
    disk are kept as another writer's, and the line must keep the agent fields."""
    from chat_test_helpers import _make_state

    from kiro_crew.dashboard.chat_persistence import _save_slot_to_history
    from kiro_crew.history import ConversationLog

    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state.conversation_log = ConversationLog()
    root = state.get_or_create_slot("root")
    root.agent, root.agent_kind, root.crew_mode_agent = LEAD, "template", LEAD
    for i in range(3):
        root.append("user", f"go {i}", "msg msg-u")
    assert _save_slot_to_history(state, root)
    _default_record("dashboard:root", replace=True)
    state._slots.pop("root")

    fresh = state.get_or_create_slot("root")
    assert fresh.agent == ""
    fresh.append("user", "hi", "msg msg-u")
    with caplog.at_level(logging.WARNING):
        assert _save_slot_to_history(state, fresh)
    assert "another writer appended" in caplog.text
    meta = state.conversation_log.get_metadata("dashboard:root")
    assert (meta.get("agent"), meta.get("agent_kind"), meta.get("crew_mode_agent")) == (
        LEAD,
        "template",
        LEAD,
    )
    assert meta.get("execution_context", {}).get("selection_name") == "kirocrew"


def _switch_env(tmp_path, monkeypatch):
    """A dashboard state whose agent switch resolves *LEAD* as a template."""
    from unittest.mock import AsyncMock

    from chat_test_helpers import _make_state

    from kiro_crew.config.loader import KiroCrewConfig, ResolvedBindings
    from kiro_crew.history import ConversationLog

    mod = "kiro_crew.dashboard.chat_handlers"
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(f"{mod}.KiroCrewConfig.load", lambda: KiroCrewConfig())
    bindings = ResolvedBindings(
        workspace_dir=tmp_path / "ws",
        effective_memory_config={},
        memory_store_name="default",
        requested_resolved=True,
        resolved_alias=LEAD,
        kiro_agent=LEAD,
        selection_kind="template",
    )
    monkeypatch.setattr(f"{mod}.resolve_agent_bindings", lambda *_a, **_k: bindings)
    monkeypatch.setattr(f"{mod}._workspace_name_for_dir", lambda cfg, ws_dir: "default")
    monkeypatch.setattr(f"{mod}.cached_project_agent_names", lambda project_dir: frozenset())
    monkeypatch.setattr(f"{mod}.default_project_dir", lambda ws: str(tmp_path / "ws"))
    monkeypatch.setattr(f"{mod}.warm_project_agent_names", AsyncMock())
    monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state.conversation_log = ConversationLog()
    state.sessions.reset = AsyncMock()
    slot = state.get_or_create_slot("root")
    slot.agent, slot.agent_kind, slot.crew_mode_agent = "kirocrew-conductor", "template", ""
    _default_record("dashboard:root")
    return state, slot


def _recorded() -> str:
    from kiro_crew.execution_context import read_session_execution

    record = read_session_execution("dashboard:root")
    return (record.selection_name or record.template_id) if record else ""


@pytest.mark.asyncio
async def test_a_same_choice_restored_during_the_switch_still_writes_the_record(
    tmp_path, monkeypatch
):
    """An unlocked writer storing the same name again (a new string object) during
    the switch leaves the choice in place: the durable selection is written, so the
    line and the record name the same agent."""
    from kiro_crew.dashboard import chat_handlers

    state, slot = _switch_env(tmp_path, monkeypatch)

    async def same_value_write(*_a, **_k):
        slot.agent = str(slot.agent)
        return True

    state.sessions.reset.side_effect = same_value_write
    caller = chat_handlers.SlotAgentSwitchCaller.for_crew_mode_migration()
    resp = await chat_handlers.switch_slot_agent(state, slot, "root", LEAD, "template", caller)
    assert resp.status == 200, resp.text
    assert slot.agent == LEAD
    assert state.conversation_log.get_metadata("dashboard:root").get("agent") == LEAD
    assert _recorded() == LEAD


@pytest.mark.asyncio
async def test_a_failed_switch_keeps_a_same_choice_another_writer_stored(tmp_path, monkeypatch):
    """The same name stored by another writer during the switch is that writer's
    agent too: when the durable write then fails, the rollback leaves it in place
    instead of moving the slot back to the prior agent under that writer's turn."""
    from kiro_crew.dashboard import chat_handlers
    from kiro_crew.memory_stores import UnknownMemoryStore

    state, slot = _switch_env(tmp_path, monkeypatch)

    async def same_value_write(*_a, **_k):
        slot.agent = str(slot.agent)
        return True

    async def failing_record(*_a, **_k):
        raise UnknownMemoryStore("store unavailable")

    state.sessions.reset.side_effect = same_value_write
    monkeypatch.setattr(chat_handlers, "_record_explicit_agent_selection", failing_record)
    caller = chat_handlers.SlotAgentSwitchCaller.for_crew_mode_migration()
    resp = await chat_handlers.switch_slot_agent(state, slot, "root", LEAD, "template", caller)
    assert resp.status != 200, resp.text
    assert slot.agent == LEAD
    assert state.conversation_log.get_metadata("dashboard:root").get("agent") == LEAD
    assert _recorded() == "kirocrew"


@pytest.mark.asyncio
async def test_another_choice_landing_during_the_switch_is_a_rebound(tmp_path, monkeypatch):
    """A different agent written during the switch: no ok answer with a line the
    record does not back; the line follows the slot, the record is untouched."""
    from kiro_crew.dashboard import chat_handlers

    state, slot = _switch_env(tmp_path, monkeypatch)

    async def other_write(*_a, **_k):
        slot.agent = "other-agent"
        return True

    state.sessions.reset.side_effect = other_write
    caller = chat_handlers.SlotAgentSwitchCaller.for_crew_mode_migration()
    resp = await chat_handlers.switch_slot_agent(state, slot, "root", LEAD, "template", caller)
    assert resp.status == 409, resp.text
    assert state.conversation_log.get_metadata("dashboard:root").get("agent") == "other-agent"
    assert _recorded() == "kirocrew"


@pytest.mark.asyncio
async def test_regression_guard_the_migration_move_rewrites_the_record(tmp_path, monkeypatch):
    # Not red on main: guards that the migration keeps the record with the slot.
    from unittest.mock import AsyncMock

    from chat_test_helpers import _make_state

    from kiro_crew.config.loader import KiroCrewConfig, ResolvedBindings
    from kiro_crew.dashboard import chat_handlers
    from kiro_crew.execution_context import read_session_execution

    mod = "kiro_crew.dashboard.chat_handlers"
    monkeypatch.setenv("KIROCREW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(f"{mod}.KiroCrewConfig.load", lambda: KiroCrewConfig())
    bindings = ResolvedBindings(
        workspace_dir=tmp_path / "ws",
        effective_memory_config={},
        memory_store_name="default",
        requested_resolved=True,
        resolved_alias=LEAD,
        kiro_agent=LEAD,
        selection_kind="template",
    )
    monkeypatch.setattr(f"{mod}.resolve_agent_bindings", lambda *_a, **_k: bindings)
    monkeypatch.setattr(f"{mod}._workspace_name_for_dir", lambda cfg, ws_dir: "default")
    monkeypatch.setattr(f"{mod}.cached_project_agent_names", lambda project_dir: frozenset())
    monkeypatch.setattr(f"{mod}.default_project_dir", lambda ws: str(tmp_path / "ws"))
    monkeypatch.setattr(f"{mod}.warm_project_agent_names", AsyncMock())
    monkeypatch.setattr("kiro_crew.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    state.sessions.reset = AsyncMock()
    slot = state.get_or_create_slot("root")
    slot.agent, slot.agent_kind, slot.crew_mode_agent = "kirocrew-conductor", "template", ""
    _default_record("dashboard:root")
    caller = chat_handlers.SlotAgentSwitchCaller.for_crew_mode_migration()
    resp = await chat_handlers.switch_slot_agent(state, slot, "root", LEAD, "template", caller)
    assert resp.status == 200, resp.text
    record = read_session_execution("dashboard:root")
    assert record is not None and record.selection_name == LEAD
