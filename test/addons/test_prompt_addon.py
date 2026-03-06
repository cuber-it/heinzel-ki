"""Tests für PromptAddOn — Repository, Registry, Render, Hot-Reload, Events."""

from __future__ import annotations

import pytest
from pathlib import Path

from addons.prompt import PromptAddOn, PromptEventType, PromptEntry, YamlPromptRepository
from addons.prompt.repository import PromptRepository


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def prompt_dir(tmp_path: Path) -> Path:
    """Temporäres Verzeichnis mit YAML-Prompt-Dateien."""
    (tmp_path / "greeting.yaml").write_text(
        "name: greeting\ncontext: system\nvariables:\n  name: Heinzel\ntemplate: |\n  Hallo {{ name }}!\n",
        encoding="utf-8",
    )
    (tmp_path / "briefing.yaml").write_text(
        "name: briefing\ncontext: user\nvariables:\n  date: heute\n  focus: allgemein\ntemplate: |\n  Briefing fuer {{ date }}. Fokus: {{ focus }}.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def repo(prompt_dir: Path) -> YamlPromptRepository:
    return YamlPromptRepository(prompt_dir)


@pytest.fixture
def addon(repo: YamlPromptRepository) -> PromptAddOn:
    return PromptAddOn(repository=repo)


# Minimaler Heinzel-Stub für on_attach/on_detach
class _FakeHeinzel:
    pass


# =============================================================================
# YamlPromptRepository
# =============================================================================


def test_repo_load_all(repo: YamlPromptRepository):
    """load_all() liefert alle YAML-Dateien."""
    data = repo.load_all()
    names = {d["name"] for d in data}
    assert "greeting" in names
    assert "briefing" in names


def test_repo_load_one(repo: YamlPromptRepository):
    """load_one() liefert korrekten Prompt."""
    data = repo.load_one("greeting")
    assert data is not None
    assert data["name"] == "greeting"
    assert "{{ name }}" in data["template"]


def test_repo_load_one_missing(repo: YamlPromptRepository):
    """load_one() gibt None zurück wenn nicht vorhanden."""
    assert repo.load_one("nonexistent") is None


def test_repo_exists(repo: YamlPromptRepository):
    assert repo.exists("greeting") is True
    assert repo.exists("nonexistent") is False


def test_repo_list_names(repo: YamlPromptRepository):
    names = repo.list_names()
    assert "greeting" in names
    assert "briefing" in names


def test_repo_save_and_reload(repo: YamlPromptRepository, tmp_path: Path):
    """save() schreibt YAML, load_one() liest es zurück."""
    data = {"name": "new-prompt", "template": "Hallo {{ x }}", "context": "system"}
    repo.save("new-prompt", data)
    loaded = repo.load_one("new-prompt")
    assert loaded is not None
    assert loaded["template"] == "Hallo {{ x }}"


def test_repo_invalid_yaml_skipped(tmp_path: Path):
    """Fehlerhafte YAML-Datei wird übersprungen."""
    (tmp_path / "bad.yaml").write_text("{{{{invalid yaml:::::", encoding="utf-8")
    (tmp_path / "good.yaml").write_text("name: good\ntemplate: OK\ncontext: system\n", encoding="utf-8")
    repo = YamlPromptRepository(tmp_path)
    data = repo.load_all()
    names = [d["name"] for d in data]
    assert "good" in names
    assert "bad" not in names


# =============================================================================
# PromptAddOn — Lifecycle
# =============================================================================


@pytest.mark.asyncio
async def test_addon_on_attach_loads_prompts(addon: PromptAddOn):
    """on_attach() lädt alle Prompts in die Registry."""
    await addon.on_attach(_FakeHeinzel())
    assert "greeting" in addon.list_names()
    assert "briefing" in addon.list_names()


@pytest.mark.asyncio
async def test_addon_on_detach_clears_registry(addon: PromptAddOn):
    """on_detach() leert die Registry."""
    await addon.on_attach(_FakeHeinzel())
    await addon.on_detach(_FakeHeinzel())
    assert addon.list_names() == []


# =============================================================================
# PromptAddOn — render
# =============================================================================


@pytest.mark.asyncio
async def test_render_with_defaults(addon: PromptAddOn):
    """render() nutzt Default-Variablen."""
    await addon.on_attach(_FakeHeinzel())
    result = addon.render("greeting")
    assert "Heinzel" in result


@pytest.mark.asyncio
async def test_render_with_override(addon: PromptAddOn):
    """render() mit kwargs überschreibt Defaults."""
    await addon.on_attach(_FakeHeinzel())
    result = addon.render("greeting", name="Ulrich")
    assert "Ulrich" in result


@pytest.mark.asyncio
async def test_render_unknown_prompt(addon: PromptAddOn):
    """render() wirft KeyError bei unbekanntem Prompt."""
    await addon.on_attach(_FakeHeinzel())
    with pytest.raises(KeyError, match="nonexistent"):
        addon.render("nonexistent")


# =============================================================================
# PromptAddOn — hot_reload
# =============================================================================


@pytest.mark.asyncio
async def test_hot_reload_detects_change(addon: PromptAddOn, prompt_dir: Path):
    """hot_reload() erkennt geänderten Prompt und gibt 1 zurück."""
    await addon.on_attach(_FakeHeinzel())
    # Prompt-Datei ändern
    (prompt_dir / "greeting.yaml").write_text(
        "name: greeting\ncontext: system\nvariables:\n  name: Welt\ntemplate: |\n  Servus {{ name }}!\n",
        encoding="utf-8",
    )
    changed = await addon.hot_reload()
    assert changed == 1
    result = addon.render("greeting")
    assert "Servus" in result


@pytest.mark.asyncio
async def test_hot_reload_no_change(addon: PromptAddOn):
    """hot_reload() gibt 0 zurück wenn nichts geändert."""
    await addon.on_attach(_FakeHeinzel())
    changed = await addon.hot_reload()
    assert changed == 0


@pytest.mark.asyncio
async def test_reload_one(addon: PromptAddOn, prompt_dir: Path):
    """reload_one() lädt einzelnen Prompt neu."""
    await addon.on_attach(_FakeHeinzel())
    (prompt_dir / "greeting.yaml").write_text(
        "name: greeting\ncontext: system\nvariables:\n  name: Welt\ntemplate: |\n  Moin {{ name }}!\n",
        encoding="utf-8",
    )
    result = await addon.reload_one("greeting")
    assert result is True
    assert "Moin" in addon.render("greeting")


@pytest.mark.asyncio
async def test_reload_one_missing(addon: PromptAddOn):
    """reload_one() gibt False zurück wenn Prompt nicht im Repository."""
    await addon.on_attach(_FakeHeinzel())
    result = await addon.reload_one("nonexistent")
    assert result is False


# =============================================================================
# PromptAddOn — mutate
# =============================================================================


@pytest.mark.asyncio
async def test_mutate_template(addon: PromptAddOn, prompt_dir: Path):
    """mutate() ändert template und persistiert."""
    await addon.on_attach(_FakeHeinzel())
    await addon.mutate("greeting", "template", "Hey {{ name }}!\n")
    result = addon.render("greeting")
    assert "Hey" in result
    # Persistenz prüfen
    repo = YamlPromptRepository(prompt_dir)
    data = repo.load_one("greeting")
    assert "Hey" in data["template"]


@pytest.mark.asyncio
async def test_mutate_unknown_prompt(addon: PromptAddOn):
    """mutate() wirft KeyError bei unbekanntem Prompt."""
    await addon.on_attach(_FakeHeinzel())
    with pytest.raises(KeyError):
        await addon.mutate("nonexistent", "template", "x")


# =============================================================================
# PromptAddOn — Events / Listener
# =============================================================================


@pytest.mark.asyncio
async def test_listener_called_on_attach(addon: PromptAddOn):
    """Listener wird für jeden geladenen Prompt aufgerufen."""
    events: list[tuple] = []
    addon.on_prompt_changed(lambda et, name, entry: events.append((et, name)))
    await addon.on_attach(_FakeHeinzel())
    event_types = {e[0] for e in events}
    assert PromptEventType.PROMPT_LOADED in event_types
    names = {e[1] for e in events}
    assert "greeting" in names


@pytest.mark.asyncio
async def test_listener_called_on_hot_reload(addon: PromptAddOn, prompt_dir: Path):
    """Listener wird bei hot_reload mit PROMPT_CHANGED aufgerufen."""
    await addon.on_attach(_FakeHeinzel())
    events: list[tuple] = []
    addon.on_prompt_changed(lambda et, name, entry: events.append((et, name)))
    (prompt_dir / "greeting.yaml").write_text(
        "name: greeting\ncontext: system\nvariables:\n  name: X\ntemplate: |\n  X {{ name }}\n",
        encoding="utf-8",
    )
    await addon.hot_reload()
    assert any(e[0] == PromptEventType.PROMPT_CHANGED for e in events)


# =============================================================================
# PromptAddOn — get / list_names
# =============================================================================


@pytest.mark.asyncio
async def test_get_returns_prompt_base(addon: PromptAddOn):
    """get() gibt PromptBase-Instanz zurück."""
    await addon.on_attach(_FakeHeinzel())
    prompt = addon.get("greeting")
    assert prompt is not None
    assert prompt.name == "greeting"


@pytest.mark.asyncio
async def test_get_unknown_returns_none(addon: PromptAddOn):
    await addon.on_attach(_FakeHeinzel())
    assert addon.get("nonexistent") is None
