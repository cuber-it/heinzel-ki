"""Tests für addon_extension — BaseAddOnExtension, SkillBase, PromptBase."""

import pytest

from core.addon_extension import BaseAddOnExtension, PromptBase, SkillBase


# =============================================================================
# Fixtures — konkrete Minimal-Implementierungen
# =============================================================================


class ConcreteSkill(SkillBase):
    """Minimale konkrete Skill-Implementierung für Tests."""


class ConcretePrompt(PromptBase):
    """Minimale konkrete Prompt-Implementierung für Tests."""


# =============================================================================
# BaseAddOnExtension
# =============================================================================


def test_base_addon_extension_is_abstract():
    """BaseAddOnExtension kann nicht direkt instanziiert werden."""
    with pytest.raises(TypeError):
        BaseAddOnExtension()  # type: ignore


def test_base_addon_extension_requires_load_unload():
    """Konkrete Subklasse ohne load/unload wirft TypeError."""

    class Incomplete(BaseAddOnExtension):
        name = "incomplete"
        version = "0.1.0"
        # load und unload fehlen

    with pytest.raises(TypeError):
        Incomplete()


# =============================================================================
# SkillBase
# =============================================================================


def test_skill_base_defaults():
    """SkillBase setzt alle Defaults korrekt."""
    skill = ConcreteSkill(name="test_skill")
    assert skill.name == "test_skill"
    assert skill.version == "0.1.0"
    assert skill.description == ""
    assert skill.trigger_patterns == []
    assert skill.system_prompt_fragment == ""
    assert skill.tools == []


def test_skill_base_custom_values():
    """SkillBase übernimmt übergebene Werte."""
    skill = ConcreteSkill(
        name="web_search",
        version="1.0.0",
        description="Sucht im Web",
        trigger_patterns=["suche nach", "finde"],
        system_prompt_fragment="Du kannst im Web suchen.",
        tools=["searxng_search"],
    )
    assert skill.name == "web_search"
    assert skill.version == "1.0.0"
    assert skill.description == "Sucht im Web"
    assert skill.trigger_patterns == ["suche nach", "finde"]
    assert skill.system_prompt_fragment == "Du kannst im Web suchen."
    assert skill.tools == ["searxng_search"]


def test_skill_base_mutable_lists_are_independent():
    """Zwei Skills teilen keine Mutable-Default-Listen."""
    skill_a = ConcreteSkill(name="a")
    skill_b = ConcreteSkill(name="b")
    skill_a.trigger_patterns.append("trigger")
    assert skill_b.trigger_patterns == []


@pytest.mark.asyncio
async def test_skill_base_load_unload_noop():
    """Standard load/unload sind No-Ops — kein Fehler."""
    skill = ConcreteSkill(name="noop_skill")
    await skill.load()
    await skill.unload()


def test_skill_base_repr():
    skill = ConcreteSkill(name="my_skill", version="2.0.0")
    assert "my_skill" in repr(skill)
    assert "2.0.0" in repr(skill)


# =============================================================================
# PromptBase
# =============================================================================


def test_prompt_base_defaults():
    """PromptBase setzt Defaults korrekt."""
    prompt = ConcretePrompt(name="test_prompt", template="Hallo {{ name }}")
    assert prompt.name == "test_prompt"
    assert prompt.version == "0.1.0"
    assert prompt.variables == {}
    assert prompt.context == "system"


def test_prompt_base_custom_values():
    """PromptBase übernimmt übergebene Werte."""
    prompt = ConcretePrompt(
        name="greeting",
        template="Hallo {{ name }}!",
        version="1.2.0",
        variables={"name": "Welt"},
        context="user",
    )
    assert prompt.variables == {"name": "Welt"}
    assert prompt.context == "user"


@pytest.mark.asyncio
async def test_prompt_base_render_basic():
    """render() gibt korrekten String zurück."""
    prompt = ConcretePrompt(
        name="greeting",
        template="Hallo {{ name }}!",
        variables={"name": "Heinzel"},
    )
    await prompt.load()
    result = prompt.render()
    assert result == "Hallo Heinzel!"


@pytest.mark.asyncio
async def test_prompt_base_render_kwargs_override_defaults():
    """render() mit kwargs überschreibt Default-Variablen."""
    prompt = ConcretePrompt(
        name="greeting",
        template="Hallo {{ name }}!",
        variables={"name": "Heinzel"},
    )
    await prompt.load()
    result = prompt.render(name="Ulrich")
    assert result == "Hallo Ulrich!"


@pytest.mark.asyncio
async def test_prompt_base_render_multiple_variables():
    """render() verarbeitet mehrere Variablen korrekt."""
    prompt = ConcretePrompt(
        name="intro",
        template="{{ greeting }}, ich bin {{ agent }} v{{ version }}.",
        variables={"greeting": "Hallo", "agent": "Heinzel", "version": "1.0"},
    )
    await prompt.load()
    result = prompt.render()
    assert result == "Hallo, ich bin Heinzel v1.0."


@pytest.mark.asyncio
async def test_prompt_base_render_strict_undefined():
    """render() wirft Fehler bei fehlenden Variablen (StrictUndefined)."""
    from jinja2 import UndefinedError

    prompt = ConcretePrompt(
        name="strict",
        template="Hallo {{ missing_var }}!",
    )
    await prompt.load()
    with pytest.raises(UndefinedError):
        prompt.render()


def test_prompt_base_render_without_load_raises():
    """render() ohne vorheriges load() wirft RuntimeError."""
    prompt = ConcretePrompt(name="unloaded", template="{{ x }}")
    with pytest.raises(RuntimeError, match="nicht geladen"):
        prompt.render()


@pytest.mark.asyncio
async def test_prompt_base_unload_clears_compiled():
    """Nach unload() ist render() nicht mehr möglich."""
    prompt = ConcretePrompt(name="lifecycle", template="{{ x }}")
    await prompt.load()
    await prompt.unload()
    with pytest.raises(RuntimeError):
        prompt.render()


def test_prompt_base_repr():
    prompt = ConcretePrompt(name="my_prompt", template="x", version="3.0.0")
    assert "my_prompt" in repr(prompt)
    assert "3.0.0" in repr(prompt)


# =============================================================================
# Import-Test
# =============================================================================


def test_exports_from_core():
    """BaseAddOnExtension, SkillBase, PromptBase sind aus core importierbar."""
    from core import BaseAddOnExtension, PromptBase, SkillBase  # noqa: F401
