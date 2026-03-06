# HNZ-003-0017: MCPToolsRouter Review — State of the Art

**Datum:** 2026-03-05  
**Aktuelle Spec:** 2025-11-25 (Latest Stable)  
**Unsere Implementierung:** HNZ-002, keine explizite Spec-Version, ~2024-11-05-Niveau  

---

## 1. Protokoll-Version

| Aspekt | Unsere Impl | Aktueller Spec (2025-11-25) | Delta |
|---|---|---|---|
| Spec-Version | nicht deklariert (~2024-11-05) | 2025-11-25 | ⚠️ |
| JSON-RPC | 2.0 (implizit) | 2.0 | ✅ |
| Batching | nicht implementiert | entfernt in 2025-06-18 | ✅ (zufällig richtig) |

**Bewertung:** Kein Breaking Change durch Versionsdelta — wir nutzen keinen Feature, der in der Zwischenzeit entfernt wurde.

---

## 2. Transport

| Aspekt | Unsere Impl | Aktueller Spec | Delta |
|---|---|---|---|
| stdio | nicht implementiert | supported | ➖ |
| HTTP+SSE | Basis-Konzept vorhanden | deprecated ab 2025-03-26 | ⚠️ |
| Streamable HTTP | geplant (HNZ-004) | Standard seit 2025-03-26 | ⚠️ |

**Detail:** Unsere `_execute()` ist ein Stub — Transport-Implementierung ist bewusst auf HNZ-004 verschoben. Das ist kein Bug, sondern ein offener Slot. **Wichtig für HNZ-004:** Streamable HTTP implementieren, nicht SSE.

Aus unseren eigenen MCP-Projekten (`mcp_shell_tools`) haben wir bereits Streamable HTTP produktiv — das Wissen ist vorhanden.

---

## 3. Tool-Discovery

| Aspekt | Unsere Impl | Aktueller Spec | Delta |
|---|---|---|---|
| `tools/list` | Stub, geplant HNZ-004 | Standard | ⚠️ |
| `.well-known` URL-Discovery | nicht vorhanden | neu in 2025-11-25 (experimentell) | ➖ |
| Tool-Annotations (`readOnly`, `destructive`) | nicht vorhanden | seit 2025-03-26 | ➖ |
| Icons-Metadata | nicht vorhanden | seit 2025-11-25 | ➖ (nice-to-have) |

**Bewertung:** Discovery-Lücken sind bekannt und HNZ-004 zugewiesen. `.well-known` und Annotations sind neu — sollten bei HNZ-004 berücksichtigt werden.

---

## 4. Approval-System

| Aspekt | Unsere Impl | Aktueller Spec | Delta |
|---|---|---|---|
| User Consent vor Tool-Call | ApprovalPolicy (4 Stufen) | MUST, aber Implementierung dem Host überlassen | ✅ |
| ALWAYS_ALLOW / ALWAYS_DENY / ASK_ONCE / ASK_ALWAYS | eigenes System | kein Standard-Enum, Spec schreibt nur Consent vor | ✅ passt |
| Audit-Log | nicht vorhanden | empfohlen | ➖ |

**Bewertung:** Unser 4-Policy-System ist spec-konform — die Spec schreibt nur vor, dass Consent eingeholt werden MUSS, wie ist dem Host überlassen. Kein Handlungsbedarf.

---

## 5. Error-Handling

| Aspekt | Unsere Impl | Aktueller Spec | Delta |
|---|---|---|---|
| `RemoteProtocolError` | gefangen in `_execute()` Stub | Standard JSON-RPC Error-Codes | ⚠️ bei HNZ-004 ausbauen |
| `ToolResult.error` Feld | vorhanden | structured error output seit 2025-06-18 | ⚠️ |
| Structured Tool Output | `ToolResult.result: Any` | typisierte Outputs seit 2025-06-18 | ⚠️ |

**Detail:** Seit 2025-06-18 können Tools strukturierte, typisierte Outputs deklarieren — unser `result: Any` ist zu loose. Für HNZ-004 `ToolResult` erweitern.

---

## 6. Neue Features seit unserer Implementierung

### Relevant für uns:

| Feature | Seit | Priorität | Aktion |
|---|---|---|---|
| Streamable HTTP als Standard-Transport | 2025-03-26 | 🔴 hoch | HNZ-004: `_execute()` auf Streamable HTTP |
| Structured Tool Output | 2025-06-18 | 🟡 mittel | `ToolResult` typisieren |
| Tool Annotations (`readOnly`, `destructive`) | 2025-03-26 | 🟡 mittel | `KnownTool` erweitern |
| Elicitation (Server fragt User) | 2025-06-18 | 🟢 niedrig | Interessant, aber nicht kritisch |
| Tasks (async, long-running) | 2025-11-25 (experimentell) | 🟢 niedrig | Abwarten bis stabil |
| `.well-known` Server-Discovery | 2025-11-25 | 🟢 niedrig | Nett für Registry-Integration |
| Sampling Tool Calling | 2025-11-25 | 🟡 mittel | Relevant wenn Heinzel als Server |
| OAuth Resource Server | 2025-06-18 | 🟢 niedrig | Nur relevant bei externem Zugriff |

### Nicht relevant für uns:
- **OAuth / DCR**: Heinzel ist internes System, keine externen Clients
- **Icons-Metadata**: UI haben wir nicht
- **OIDC Discovery**: Authelia übernimmt das auf Infra-Ebene

---

## 7. Resources, Prompts, Sampling

Drei MCP-Primitives die wir nicht implementiert haben:

| Primitive | Was es ist | Relevant für Heinzel? |
|---|---|---|
| **Resources** | Daten lesen (wie GET) | Ja — z.B. Dateisystem, DB-Inhalte | 
| **Prompts** | Server-seitige Prompt-Templates | Nein — wir haben SkillsAddOn |
| **Sampling** | Server lässt Client LLM aufrufen | Ja — wenn Heinzel als MCP-Server |

Resources wären interessant als Alternative zu direktem DB-Zugriff. Kein akuter Bedarf.

---

## 8. Fazit und Empfehlung

**Urteil: Kein Neubau — gezielter Patch bei HNZ-004.**

Unsere Abstraktion (`_execute()` als Stub, ApprovalPolicy, ToolAddress/KnownTool-Modelle) hält der aktuellen Spec stand. Die Lücken sind bekannt und sauber isoliert.

### Konkrete Stories für HNZ-004:

1. **`_execute()` implementieren** — Streamable HTTP, nicht SSE  
   Basis: `mcp_shell_tools` HTTP-Implementierung als Referenz

2. **`KnownTool` erweitern** um `annotations: dict` (readOnly, destructive)

3. **`ToolResult` typisieren** — structured output statt `result: Any`

4. **Tool-Discovery via `tools/list`** — MCPDiscovererAddOn (bereits geplant)

5. **`ToolResult.error` strukturieren** — JSON-RPC Error-Codes mapping

**Tasks und `.well-known`**: Abwarten. Beide noch experimentell oder niedrige Priorität.

---

*Review-Grundlage: modelcontextprotocol.io/specification/2025-11-25, blog.modelcontextprotocol.io*
