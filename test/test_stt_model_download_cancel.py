"""A cancelled voice model download does not leave its status stuck at downloading.

The download itself is pinned in ``test_stt_engine_download.py``.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from kiro_crew.stt import models


@pytest.mark.asyncio
async def test_a_cancelled_download_ends_failed_and_stays_failed(monkeypatch) -> None:
    monkeypatch.delenv(models.SKIP_DOWNLOAD_ENV, raising=False)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()  # set from the worker thread, awaited on the loop
    release = threading.Event()
    report: list = []

    def _blocking_download(_model, on_progress, on_complete=None):
        report.append(on_progress)
        on_progress(10, 100)
        loop.call_soon_threadsafe(started.set)
        release.wait(timeout=30)
        on_progress(20, 100)  # the thread outlives the cancelled caller
        raise RuntimeError("stopped by the test")

    store = models.ModelStore()

    async def _absent(_model):
        return None, False

    monkeypatch.setattr(store, "_accept_existing_or_refuse", _absent)
    monkeypatch.setattr(models, "_download_blocking", _blocking_download)
    model = SimpleNamespace(name="base", size_bytes=100)

    task = asyncio.create_task(store.ensure(model))
    await asyncio.wait_for(started.wait(), 30)
    assert store.status["step"] == "downloading"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        assert store.status["step"] == "failed"
        assert store.status["error"] == "download cancelled"
    finally:
        release.set()

    # Late progress from the still-running thread must not bring "downloading" back.
    report[0](30, 100)
    assert store.status["step"] == "failed"


@pytest.mark.asyncio
async def test_an_orphaned_download_that_finishes_reports_ready(monkeypatch) -> None:
    # A cancel sets "failed", but the worker thread cannot be cancelled and keeps
    # going. When it lands the verified file, the status must flip to "ready" rather
    # than staying "failed" until the next ensure() accepts the file on disk.
    monkeypatch.delenv(models.SKIP_DOWNLOAD_ENV, raising=False)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()  # set from the worker thread, awaited on the loop
    release = threading.Event()  # let the thread finish only after the cancel landed
    completed = asyncio.Event()  # set (on the loop) once the thread ran on_complete

    def _blocking_download(_model, on_progress, on_complete=None):
        on_progress(10, 100)
        loop.call_soon_threadsafe(started.set)
        release.wait(timeout=30)  # the thread outlives the cancelled caller
        # The real _download_blocking lands the verified file, then calls this.
        if on_complete is not None:
            on_complete()
        loop.call_soon_threadsafe(completed.set)
        return "/models/ggml-base.bin"

    store = models.ModelStore()

    async def _absent(_model):
        return None, False

    monkeypatch.setattr(store, "_accept_existing_or_refuse", _absent)
    monkeypatch.setattr(models, "_download_blocking", _blocking_download)
    model = SimpleNamespace(name="base", size_bytes=100)

    task = asyncio.create_task(store.ensure(model))
    await asyncio.wait_for(started.wait(), 30)
    assert store.status["step"] == "downloading"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.status["step"] == "failed"
    assert store.status["error"] == "download cancelled"

    # The orphaned thread now finishes the transfer and lands the file.
    release.set()
    await asyncio.wait_for(completed.wait(), 30)

    # The model is present, so its status reports "ready", not "failed".
    assert store.status["step"] == "ready"
    assert store.status["model"] == "base"
    assert store.status["error"] == ""


@pytest.mark.asyncio
async def test_orphaned_completion_does_not_clobber_a_newer_attempts_failure(monkeypatch) -> None:
    # A cancel releases the store lock; a second ensure() for a DIFFERENT model
    # then fails. When the first (orphaned) worker finishes, its "ready" must not
    # land over the second attempt's "failed" -- a later attempt owns the display.
    monkeypatch.delenv(models.SKIP_DOWNLOAD_ENV, raising=False)
    loop = asyncio.get_running_loop()
    first_started = asyncio.Event()
    release_first = threading.Event()
    first_completed = asyncio.Event()

    def _first(_model, on_progress, on_complete=None):
        loop.call_soon_threadsafe(first_started.set)
        release_first.wait(timeout=30)
        if on_complete is not None:
            on_complete()  # the orphaned worker lands the file
        loop.call_soon_threadsafe(first_completed.set)
        return "/models/ggml-base.bin"

    def _second(_model, on_progress, on_complete=None):
        raise RuntimeError("second model failed")

    store = models.ModelStore()

    async def _absent(_model):
        return None, False

    monkeypatch.setattr(store, "_accept_existing_or_refuse", _absent)

    monkeypatch.setattr(models, "_download_blocking", _first)
    task = asyncio.create_task(store.ensure(SimpleNamespace(name="base", size_bytes=100)))
    await asyncio.wait_for(first_started.wait(), 30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.status["step"] == "failed"

    # A second, different download runs under a fresh token and fails.
    monkeypatch.setattr(models, "_download_blocking", _second)
    assert await store.ensure(SimpleNamespace(name="small", size_bytes=200)) is None
    assert store.status["step"] == "failed"
    assert store.status["model"] == "small"

    # Now the orphaned first worker finishes. Its "ready" must NOT overwrite the
    # second attempt's failure.
    release_first.set()
    await asyncio.wait_for(first_completed.wait(), 30)
    assert store.status["step"] == "failed"
    assert store.status["model"] == "small"


@pytest.mark.asyncio
async def test_completion_just_before_cancel_still_reports_ready(monkeypatch) -> None:
    # The worker lands the file and runs on_complete BEFORE the awaiting caller is
    # cancelled. The cancel must then report "ready" (the model is installed),
    # not "failed".
    monkeypatch.delenv(models.SKIP_DOWNLOAD_ENV, raising=False)
    loop = asyncio.get_running_loop()
    completed_in_thread = asyncio.Event()
    let_thread_return = threading.Event()

    def _blocking_download(_model, on_progress, on_complete=None):
        # Complete first, then signal the loop, then block so the cancel lands
        # while this attempt has already completed but ensure() has not resumed.
        if on_complete is not None:
            on_complete()
        loop.call_soon_threadsafe(completed_in_thread.set)
        let_thread_return.wait(timeout=30)
        return "/models/ggml-base.bin"

    store = models.ModelStore()

    async def _absent(_model):
        return None, False

    monkeypatch.setattr(store, "_accept_existing_or_refuse", _absent)
    monkeypatch.setattr(models, "_download_blocking", _blocking_download)

    task = asyncio.create_task(store.ensure(SimpleNamespace(name="base", size_bytes=100)))
    await asyncio.wait_for(completed_in_thread.wait(), 30)
    # The file has landed; the caller is cancelled before ensure() resumes.
    task.cancel()
    let_thread_return.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The model is installed, so the status reports "ready", not "failed".
    assert store.status["step"] == "ready"
    assert store.status["model"] == "base"
