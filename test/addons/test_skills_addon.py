"""Tests für SkillsAddOn und SkillLoaderAddOn."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from addons.skills import SkillsAddOn, SkillLoaderAddOn, SkillValidationError, YamlSkillRepository
from addons.skills.addon import _matches, _build_entry
from core.addon_extension import SkillBase


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    (tmp_path / "python-expert.yaml").write_text(
        "name: python-expert\nversion: '1.0'\ndescription: Python Code\n"
        "instructions: |\n  Du schreibst Python nach PEP8.\ntrigger_patterns:\n"
        "  - python\n  - code\ntools: []\n",
        encoding="utf-8",
    )
    (tmp_path / "web-search.yaml").write_text(
        "name: web-search\nversion: '1.0'\ndescription: Web-Suche\n"
        "instructions: |\n  Du kannst im Web suchen.\ntrigger_patterns:\n"
        "  - suche\n  - recherche\ntools: [searxng]\n",
        encoding="utf-8",
    )
    (tmp_path / "always-on.yaml").write_text(
        "name: always-on\nversion: '1.0'\ndescription: Immer aktiv\n"
        "instructions: |\n  Sei immer freundlich.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def repo(skill_dir: Path) -> YamlSkillRepository:
    return YamlSkillRepository(skill_dir)


@pytest.fixture
def addon(repo: YamlSkillRepository) -> SkillsAddOn:
    return SkillsAddOn(repository=repo)


class _FakeHeinzel:
    pass


# =============================================================================
# YamlSkillRepository
# =============================================================================


def test_repo_load_all(repo):
    data = repo.load_all()
    names = {d["name"] for d in data}
    assert "python-expert" in names
    assert "web-search" in names


def test_repo_load_one(repo):
    data = repo.load_one("python-expert")
    assert data is not None
    assert data["name"] == "python-expert"
    assert "PEP8" in data["instructions"]


def test_repo_load_one_missing(repo):
    assert repo.load_one("nonexistent") is None


def test_repo_exists(repo):
    assert repo.exists("python-expert") is True
    assert repo.exists("nope") is False


def test_repo_list_names(repo):
    names = repo.list_names()
    assert "python-expert" in names
    assert "web-search" in names


def test_repo_save_and_reload(repo, tmp_path):
    data = {"name": "new-skill", "instructions": "Tu was.", "description": "Neu"}
    repo.save("new-skill", data)
    loaded = repo.load_one("new-skill")
    assert loaded is not None
    assert "Tu was." in loaded["instructions"]


def test_repo_invalid_yaml_skipped(tmp_path):
    (tmp_path / "bad.yaml").write_text("{{{{:", encoding="utf-8")
    (tmp_path / "good.yaml").write_text(
        "name: good\ninstructions: ok\ndescription: x\n", encoding="utf-8"
    )
    repo = YamlSkillRepository(tmp_path)
    names = [d["name"] for d in repo.load_all()]
    assert "good" in names
    assert "bad" not in names


# =============================================================================
# _build_entry / _matches
# =============================================================================


def test_build_entry_valid():
    data = {"name": "test", "instructions": "Tu was.", "description": "x"}
    entry = _build_entry(data)
    assert entry is not None
    assert entry.skill.name == "test"
    assert entry.skill.system_prompt_fragment == "Tu was."


def test_build_entry_alias_system_prompt_fragment():
    """system_prompt_fragment als Alias für instructions."""
    data = {"name": "test", "system_prompt_fragment": "Fragment", "description": "x"}
    entry = _build_entry(data)
    assert entry is not None
    assert entry.skill.system_prompt_fragment == "Fragment"


def test_build_entry_missing_name():
    entry = _build_entry({"instructions": "x"})
    assert entry is None


def test_matches_no_patterns():
    """Skill ohne trigger_patterns ist immer aktiv."""
    data = {"name": "x", "instructions": "y"}
    entry = _build_entry(data)
    assert _matches(entry.skill, "") is True
    assert _matches(entry.skill, "irgendwas") is True


def test_matches_with_patterns():
    data = {"name": "x", "instructions": "y", "trigger_patterns": ["python", "code"]}
    entry = _build_entry(data)
    assert _matches(entry.skill, "python ist cool") is True
    assert _matches(entry.skill, "schreib code") is True
    assert _matches(entry.skill, "kochrezept") is False


def test_matches_case_insensitive():
    data = {"name": "x", "instructions": "y", "trigger_patterns": ["Python"]}
    entry = _build_entry(data)
    assert _matches(entry.skill, "PYTHON ist toll") is True


# =============================================================================
# SkillsAddOn — Lifecycle
# =============================================================================


@pytest.mark.asyncio
async def test_on_attach_loads_skills(addon):
    await addon.on_attach(_FakeHeinzel())
    assert "python-expert" in addon.list_skills()
    assert "web-search" in addon.list_skills()
    assert "always-on" in addon.list_skills()


@pytest.mark.asyncio
async def test_on_detach_clears(addon):
    await addon.on_attach(_FakeHeinzel())
    await addon.on_detach(_FakeHeinzel())
    assert addon.list_skills() == []


# =============================================================================
# SkillsAddOn — get_active
# =============================================================================


@pytest.mark.asyncio
async def test_get_active_no_filter_always_on(addon):
    """Skill ohne trigger_patterns ist ohne Filter immer aktiv."""
    await addon.on_attach(_FakeHeinzel())
    active = addon.get_active("kochrezept")
    names = [s.name for s in active]
    assert "always-on" in names


@pytest.mark.asyncio
async def test_get_active_no_filter_pattern_match(addon):
    """Skill mit passendem trigger_pattern wird zurückgegeben."""
    await addon.on_attach(_FakeHeinzel())
    active = addon.get_active("schreib python code")
    names = [s.name for s in active]
    assert "python-expert" in names
    assert "web-search" not in names


@pytest.mark.asyncio
async def test_get_active_config_filter(skill_dir):
    """active-Filter gibt nur die konfigurierten Skills zurück."""
    repo = YamlSkillRepository(skill_dir)
    addon = SkillsAddOn(repository=repo, active=["web-search"])
    await addon.on_attach(_FakeHeinzel())
    active = addon.get_active("python code schreiben")
    names = [s.name for s in active]
    assert names == ["web-search"]  # python-expert ignoriert trotz Match


@pytest.mark.asyncio
async def test_get_active_config_filter_order(skill_dir):
    """active-Filter respektiert die Reihenfolge in der Config."""
    repo = YamlSkillRepository(skill_dir)
    addon = SkillsAddOn(repository=repo, active=["web-search", "python-expert"])
    await addon.on_attach(_FakeHeinzel())
    active = addon.get_active("")
    assert active[0].name == "web-search"
    assert active[1].name == "python-expert"


# =============================================================================
# SkillsAddOn — hot_reload / reload_one
# =============================================================================


@pytest.mark.asyncio
async def test_hot_reload_detects_change(addon, skill_dir):
    await addon.on_attach(_FakeHeinzel())
    (skill_dir / "python-expert.yaml").write_text(
        "name: python-expert\nversion: '2.0'\ndescription: Neu\n"
        "instructions: |\n  Neue Instruktion.\ntrigger_patterns:\n  - python\n",
        encoding="utf-8",
    )
    changed = await addon.hot_reload()
    assert changed == 1
    skill = addon.get_skill("python-expert")
    assert "Neue Instruktion" in skill.system_prompt_fragment


@pytest.mark.asyncio
async def test_hot_reload_no_change(addon):
    await addon.on_attach(_FakeHeinzel())
    changed = await addon.hot_reload()
    assert changed == 0


@pytest.mark.asyncio
async def test_reload_one(addon, skill_dir):
    await addon.on_attach(_FakeHeinzel())
    (skill_dir / "web-search.yaml").write_text(
        "name: web-search\nversion: '2.0'\ndescription: x\n"
        "instructions: |\n  Suche v2.\n",
        encoding="utf-8",
    )
    result = await addon.reload_one("web-search")
    assert result is True
    assert "Suche v2" in addon.get_skill("web-search").system_prompt_fragment


@pytest.mark.asyncio
async def test_reload_one_missing(addon):
    await addon.on_attach(_FakeHeinzel())
    assert await addon.reload_one("nonexistent") is False


# =============================================================================
# SkillsAddOn — load_skill / unload_skill / get_skill
# =============================================================================


@pytest.mark.asyncio
async def test_load_skill_from_path(addon, tmp_path):
    await addon.on_attach(_FakeHeinzel())
    skill_file = tmp_path / "custom.yaml"
    skill_file.write_text(
        "name: custom\ndescription: x\ninstructions: Custom-Skill.\n",
        encoding="utf-8",
    )
    skill = await addon.load_skill(str(skill_file))
    assert skill.name == "custom"
    assert addon.get_skill("custom") is not None


@pytest.mark.asyncio
async def test_unload_skill(addon):
    await addon.on_attach(_FakeHeinzel())
    result = await addon.unload_skill("python-expert")
    assert result is True
    assert addon.get_skill("python-expert") is None


@pytest.mark.asyncio
async def test_unload_skill_missing(addon):
    await addon.on_attach(_FakeHeinzel())
    assert await addon.unload_skill("nonexistent") is False


def test_get_skill_unknown_returns_none(addon):
    assert addon.get_skill("nope") is None


# =============================================================================
# SkillLoaderAddOn
# =============================================================================


def _make_heinzel_with_skills(addon: SkillsAddOn) -> MagicMock:
    heinzel = MagicMock()
    heinzel.addons.get.return_value = addon
    return heinzel


@pytest.mark.asyncio
async def test_skill_loader_sets_metadata(skill_dir):
    """SkillLoaderAddOn setzt ctx.metadata['skills']."""
    from core.models import PipelineContext

    skills_addon = SkillsAddOn(repository=YamlSkillRepository(skill_dir))
    await skills_addon.on_attach(_FakeHeinzel())

    loader = SkillLoaderAddOn()
    await loader.on_attach(_make_heinzel_with_skills(skills_addon))

    ctx = PipelineContext(session_id="s", parsed_input="python code schreiben")
    updated = await loader.on_context_build(ctx)

    assert "skills" in updated.metadata
    assert any("python-expert" in s for s in updated.metadata["skills"])


@pytest.mark.asyncio
async def test_skill_loader_always_on_no_input(skill_dir):
    """Skill ohne trigger_patterns erscheint auch bei leerem Input."""
    from core.models import PipelineContext

    skills_addon = SkillsAddOn(repository=YamlSkillRepository(skill_dir))
    await skills_addon.on_attach(_FakeHeinzel())

    loader = SkillLoaderAddOn()
    await loader.on_attach(_make_heinzel_with_skills(skills_addon))

    ctx = PipelineContext(session_id="s", parsed_input="")
    updated = await loader.on_context_build(ctx)

    assert any("always-on" in s for s in updated.metadata["skills"])


@pytest.mark.asyncio
async def test_skill_loader_immutable_context(skill_dir):
    """on_context_build verändert den originalen Context nicht."""
    from core.models import PipelineContext

    skills_addon = SkillsAddOn(repository=YamlSkillRepository(skill_dir))
    await skills_addon.on_attach(_FakeHeinzel())

    loader = SkillLoaderAddOn()
    await loader.on_attach(_make_heinzel_with_skills(skills_addon))

    ctx = PipelineContext(session_id="s", parsed_input="python")
    updated = await loader.on_context_build(ctx)

    assert updated is not ctx
    assert ctx.metadata == {} or "skills" not in (ctx.metadata or {})


@pytest.mark.asyncio
async def test_skill_loader_no_skills_addon():
    """SkillLoaderAddOn ohne SkillsAddOn gibt unveränderten Context zurück."""
    from core.models import PipelineContext

    heinzel = MagicMock()
    heinzel.addons.get.return_value = None

    loader = SkillLoaderAddOn()
    await loader.on_attach(heinzel)

    ctx = PipelineContext(session_id="s", parsed_input="x")
    updated = await loader.on_context_build(ctx)
    assert updated is ctx
