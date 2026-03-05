"""CommandAddOn — !-Kommandos für Heinzel."""

from .addon import CommandAddOn, CommandRegistry, CommandContext, CommandResult

__all__ = ["CommandAddOn", "CommandRegistry", "CommandContext", "CommandResult"]

from .addon2 import CommandAddOnII, AliasStore, MacroStore

__all__ += ['CommandAddOnII', 'AliasStore', 'MacroStore']
