# FEATURE REQUEST: Files API mit Hash-Cache (Anthropic/Google)
**Board:** H.E.I.N.Z.E.L. | **Liste:** Icebox | **Priorität:** Medium

## Motivation
Dateien (PDF, Bilder) werden aktuell bei jedem Request erneut als Base64 inline übertragen.
Für Dokumente die mehrfach referenziert werden (multi-turn, Gedächtnismechanismus) ist das ineffizient.
Anthropic und Google haben jeweils eine Files API: einmal hochladen, per file_id referenzieren.

## Konzept

**Hash-basierter Cache (lokal, SQLite)**
- Datei rein → SHA256-Hash berechnen
- DB-Lookup: Hash bekannt? → file_id direkt verwenden, kein Upload
- Hash unbekannt → Upload → file_id + Ablaufdatum in DB speichern

**Provider-Mapping**
| Provider | Files API | Ablauf | Fallback |
|---|---|---|---|
| Anthropic | ✅ `/v1/files` (files-api-2025-04-14 beta) | manuell löschen | base64 inline |
| Google | ✅ Files API | 48h | base64 inline |
| OpenAI | ❌ nicht kompatibel (Assistants-Kontext) | — | Text-Extraktion (pypdf) |

**Steuerung per Request-Parameter**
```json
{
  "use_files_api": true,
  "messages": [...]
}
```

**Gedächtnis-Integration**
Passt direkt zu H.E.I.N.Z.E.L.'s lokalen Gedächtnismechanismen:
- Ein Heinzel kann ein Dokument "kennen" über mehrere Sessions
- file_id bleibt im lokalen Cache solange gültig
- Kein erneutes Übertragen/Tokenisieren bekannter Dokumente

## Abgrenzung
- Wir nutzen NICHT die Responses API (OpenAI-spezifisch, zerstört unified interface)
- Eigene Implementierung bleibt provider-agnostisch
- Files API ist reine Transport-Optimierung, das Datenmodell bleibt gleich

## Voraussetzungen
- HNZ-001-0011 abgeschlossen (Multimodal-Support) ✅
- Lokale SQLite für file_id-Cache (bereits vorhanden via costs.db Infrastruktur)

## Scope
- Anthropic + Google Files API
- Hash-Cache in SQLite
- `use_files_api` Flag im ChatRequest
- Automatisches Cleanup abgelaufener file_ids
- Tests

## Notiz
Wekan-POST-Bug verhindert automatisches Anlegen (Meteor Connection-Reset).
Manuell im Icebox anlegen sobald Wekan stabil läuft.
