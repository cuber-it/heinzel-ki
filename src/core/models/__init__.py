"""heinzel_core.models — öffentliche API.

Alle Models sind direkt aus core.models importierbar:
    from core.models import PipelineContext, HookPoint, Message, ...
"""

from .types import HookPoint
from .base import (
    Message,
    TokenUsage,
    ToolCall,
    ToolResult,
    MemoryResult,
    ThinkingStep,
    AddOnResult,
)
from .placeholders import (
    Fact,
    Skill,
    Goal,
    ResourceBudget,
    StepPlan,
    Reflection,
    EvaluationResult,
    CompactionResult,
    HandoverContext,
)
from .context import (
    PipelineContext,
    ContextDiff,
    ContextHistory,
)

__all__ = [
    "HookPoint",
    "Message",
    "TokenUsage",
    "ToolCall",
    "ToolResult",
    "MemoryResult",
    "ThinkingStep",
    "AddOnResult",
    "Fact",
    "Skill",
    "Goal",
    "ResourceBudget",
    "StepPlan",
    "Reflection",
    "EvaluationResult",
    "CompactionResult",
    "HandoverContext",
    "PipelineContext",
    "ContextDiff",
    "ContextHistory",
]
