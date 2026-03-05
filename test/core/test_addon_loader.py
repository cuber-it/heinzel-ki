"""Tests für AddOnLoader — Load/Unload, Dependency-Check, Concurrency, Fehler-Isolation."""

from __future__ import annotations

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.addon import AddOn
from core.addon_loader import AddOnLoader, AddOnLoadError
from core.models import HookPoint
from core.provider import NoopProvider
from core.runner import Runner


# =============================================================================
# Fixtures
# =============================================================================


class _SimpleAddOn(AddOn):
    name = "simple"
    version = "1.0"
    dependencies: list[str] = []

    def __init__(self):
        self.attached = False
        self.detached = False

    async def on_attach(self, heinzel) -> None:
        self.attached = True

    async def on_detach(self, heinzel) -> None:
        self.detached = True


class _DepAddOn(AddOn):
    name = "dep_addon"
    dependencies: list[str] = ["simple"]


class _BrokenAttachAddOn(AddOn):
    name = "broken"
    dependencies: list[str] = []

    async def on_attach(self, heinzel) -> None:
        raise RuntimeError("on_attach kaputt")


@pytest.fixture
async def runner():
    r = Runner(provider=NoopProvider(), name="test")
    await r.connect()
    yield r
    await r.disconnect()


@pytest.fixture
def loader(runner):
    return AddOnLoader(runner)


# =============================================================================
# load_from_file
# =============================================================================


def test_load_from_file_nonexistent(loader):
    with pytest.raises(AddOnLoadError, match="nicht gefunden"):
        loader.load_from_file("/tmp/nonexistent_xyz.py", "MyAddOn")


def test_load_from_file_missing_class(loader, tmp_path):
    f = tmp_path / "mymod.py"
    f.write_text("x = 1\n")
    with pytest.raises(AddOnLoadError, match="nicht in"):
        loader.load_from_file(f, "MyAddOn")


def test_load_from_file_not_addon(loader, tmp_path):
    f = tmp_path / "mymod.py"
    f.write_text("class MyAddOn: pass\n")
    with pytest.raises(AddOnLoadError, match="kein AddOn"):
        loader.load_from_file(f, "MyAddOn")


def test_load_from_file_success(loader, tmp_path):
    f = tmp_path / "mymod.py"
    f.write_text("""
from core.addon import AddOn
class MyAddOn(AddOn):
    name = "my"
    dependencies = []
    async def on_attach(self, h): pass
    async def on_detach(self, h): pass
""")
    addon = loader.load_from_file(f, "MyAddOn")
    assert addon.name == "my"


# =============================================================================
# load_from_package
# =============================================================================


def test_load_from_package_missing(loader):
    with pytest.raises(AddOnLoadError, match="nicht gefunden"):
        loader.load_from_package("nonexistent.package.xyz", "MyClass")


def test_load_from_package_missing_class(loader):
    with pytest.raises(AddOnLoadError, match="nicht in"):
        loader.load_from_package("core.addon", "NonExistentClass")


def test_load_from_package_success(loader):
    addon = loader.load_from_package("addons.database.sqlite", "SQLiteAddOn")
    assert addon.name == "database"


# =============================================================================
# load_and_register
# =============================================================================


@pytest.mark.asyncio
async def test_load_and_register_success(loader, runner):
    addon = _SimpleAddOn()
    ok = await loader.load_and_register(addon, hooks={HookPoint.ON_INPUT})
    assert ok is True
    assert addon.attached is True
    assert runner.addons.get("simple") is addon


@pytest.mark.asyncio
async def test_load_and_register_dependency_missing(loader):
    addon = _DepAddOn()
    with pytest.raises(AddOnLoadError, match="Dependency"):
        await loader.load_and_register(addon, hooks={HookPoint.ON_INPUT})


@pytest.mark.asyncio
async def test_load_and_register_broken_attach(loader, runner):
    addon = _BrokenAttachAddOn()
    ok = await loader.load_and_register(addon, hooks={HookPoint.ON_INPUT})
    assert ok is False
    # Kein Eintrag im Runner
    assert runner.addons.get("broken") is None


@pytest.mark.asyncio
async def test_load_and_register_tracks_active_calls(loader, runner):
    addon = _SimpleAddOn()
    await loader.load_and_register(addon, hooks={HookPoint.ON_INPUT})
    assert loader._active_calls["simple"] == 0


# =============================================================================
# unload_and_detach
# =============================================================================


@pytest.mark.asyncio
async def test_unload_and_detach_success(loader, runner):
    addon = _SimpleAddOn()
    await loader.load_and_register(addon, hooks={HookPoint.ON_INPUT})
    ok = await loader.unload_and_detach("simple")
    assert ok is True
    assert addon.detached is True
    assert runner.addons.get("simple") is None


@pytest.mark.asyncio
async def test_unload_not_found(loader):
    ok = await loader.unload_and_detach("nicht_vorhanden")
    assert ok is False


@pytest.mark.asyncio
async def test_load_unload_load(loader, runner):
    """Load → Chat → Unload → Load wieder möglich."""
    addon1 = _SimpleAddOn()
    await loader.load_and_register(addon1, hooks={HookPoint.ON_INPUT})
    await loader.unload_and_detach("simple")
    assert runner.addons.get("simple") is None

    addon2 = _SimpleAddOn()
    ok = await loader.load_and_register(addon2, hooks={HookPoint.ON_INPUT})
    assert ok is True
    assert runner.addons.get("simple") is addon2


# =============================================================================
# is_unloading
# =============================================================================


@pytest.mark.asyncio
async def test_is_unloading_during_unload(loader, runner):
    """is_unloading() ist True während des Unloads."""
    addon = _SimpleAddOn()
    await loader.load_and_register(addon, hooks={HookPoint.ON_INPUT})

    # Simuliere laufenden Call
    loader._active_calls["simple"] = 1
    unload_task = asyncio.create_task(loader.unload_and_detach("simple"))
    await asyncio.sleep(0.05)  # kurz warten — Unload wartet auf Call
    assert loader.is_unloading("simple") is True

    # Call beenden
    loader._active_calls["simple"] = 0
    await unload_task


# =============================================================================
# list_loaded
# =============================================================================


@pytest.mark.asyncio
async def test_list_loaded(loader, runner):
    addon = _SimpleAddOn()
    await loader.load_and_register(addon, hooks={HookPoint.ON_INPUT})
    loaded = loader.list_loaded()
    names = [e["name"] for e in loaded]
    assert "simple" in names


@pytest.mark.asyncio
async def test_list_loaded_after_unload(loader, runner):
    addon = _SimpleAddOn()
    await loader.load_and_register(addon, hooks={HookPoint.ON_INPUT})
    await loader.unload_and_detach("simple")
    names = [e["name"] for e in loader.list_loaded()]
    assert "simple" not in names
