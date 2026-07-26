"""Tests for acquisition monitor application lifecycle wiring."""

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus import main
from papyrus.services import acquisition_monitor


async def test_lifespan_starts_and_stops_configured_acquisition_monitor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(main.settings, "acquisition_enabled", True)
    monkeypatch.setattr(main.settings, "acquisition_import_root", str(tmp_path))
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def run_monitor(
        session_maker: async_sessionmaker[AsyncSession],
        *,
        import_root: Path,
    ) -> None:
        assert import_root == tmp_path
        started.set()

        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(acquisition_monitor, "run_monitor", run_monitor)

    async with main.lifespan(FastAPI()):
        await asyncio.wait_for(started.wait(), timeout=1)

    assert stopped.is_set()


@pytest.mark.parametrize(
    ("enabled", "import_root"),
    [
        (False, "/downloads"),
        (True, None),
    ],
)
async def test_lifespan_does_not_start_incomplete_acquisition_monitor(
    enabled: bool,
    import_root: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main.settings, "acquisition_enabled", enabled)
    monkeypatch.setattr(main.settings, "acquisition_import_root", import_root)
    started = False

    async def run_monitor(
        session_maker: async_sessionmaker[AsyncSession],
        *,
        import_root: Path,
    ) -> None:
        nonlocal started
        started = True

    monkeypatch.setattr(acquisition_monitor, "run_monitor", run_monitor)

    async with main.lifespan(FastAPI()):
        await asyncio.sleep(0)

    assert not started
