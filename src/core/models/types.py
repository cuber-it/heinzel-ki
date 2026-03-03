"""HookPoint — definiert alle Phasen der Heinzel-Pipeline."""

import enum


class HookPoint(str, enum.Enum):
    """Alle Phasen der Pipeline an denen AddOns eingreifen können."""

    # Eingabe
    ON_INPUT = "on_input"
    ON_INPUT_PARSED = "on_input_parsed"

    # Gedächtnis
    ON_MEMORY_QUERY = "on_memory_query"
    ON_MEMORY_HIT = "on_memory_hit"
    ON_MEMORY_MISS = "on_memory_miss"

    # Kontext-Aufbau
    ON_CONTEXT_BUILD = "on_context_build"
    ON_CONTEXT_READY = "on_context_ready"

    # LLM
    ON_LLM_REQUEST = "on_llm_request"
    ON_STREAM_CHUNK = "on_stream_chunk"
    ON_THINKING_STEP = "on_thinking_step"
    ON_LLM_RESPONSE = "on_llm_response"

    # Tools
    ON_TOOL_REQUEST = "on_tool_request"
    ON_TOOL_RESULT = "on_tool_result"
    ON_TOOL_ERROR = "on_tool_error"

    # Loop
    ON_LOOP_ITERATION = "on_loop_iteration"
    ON_LOOP_END = "on_loop_end"

    # Ausgabe
    ON_OUTPUT = "on_output"
    ON_OUTPUT_SENT = "on_output_sent"

    # Persistenz
    ON_STORE = "on_store"
    ON_STORED = "on_stored"

    # Session
    ON_SESSION_START = "on_session_start"
    ON_SESSION_END = "on_session_end"
    ON_SESSION_ROLL = "on_session_roll"

    # Fehler
    ON_ERROR = "on_error"
