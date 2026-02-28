# Chainlit PoC — Bewertung

## Was funktioniert

- Chat mit Streaming via Provider-API (kein direkter Anthropic/OpenAI-Aufruf)
- Session-ID (UUID) pro Chainlit-Chat-Session, wird an Provider übergeben
- Taucht in Dialog-Log (`/logs?session_id=...`) und Metriken (`/metrics?session_id=...`) auf
- Modell per `/model <name>` wechselbar, `/models` zeigt verfügbare Modelle
- Dialog-Logging zur Laufzeit an/aus via `/log on|off`
- Metriken der aktuellen Session via `/metrics`
- Multi-Turn: Gesprächsverlauf wird korrekt mitgegeben
- `/health`, `/info`, `/clear`, `/session`

## Was nicht funktioniert / Grenzen

**Reasoning-Steps / Tool-Call-Darstellung:**
Chainlit kann Reasoning-Steps schön anzeigen (`cl.Step`, `cl.Action`), aber das setzt voraus,
dass der Code die Tool-Calls selbst auslöst. Da wir über die Provider-API gehen und nur
den finalen Stream empfangen, sind Zwischenschritte nicht sichtbar. Der Provider gibt
`StreamChunk(type="content_delta")` zurück — kein separater Reasoning-Channel.

Abhilfe wenn gewünscht: Provider-API um einen `/chat/stream/verbose`-Endpoint erweitern,
der Reasoning-Chunks als eigenen Typ zurückgibt.

**Authentifizierung:**
Keine. Für Produktivbetrieb nötig. Chainlit bietet Password-Auth und OAuth —
muss in `chainlit.md` + `config.toml` konfiguriert werden.

**Session-Persistenz:**
Sessions leben nur im Memory des Chainlit-Prozesses. Bei Container-Neustart verloren.
Für Persistenz: Chainlit Data Layer (SQLite/PostgreSQL) aktivieren.

**Provider/Modell-Auswahl über UI:**
Aktuell nur per `/model`-Kommando im Chat. Chainlit unterstützt Settings-Panel (`cl.ChatSettings`)
für komfortablere UI — wäre eine saubere Erweiterung.

## Bewertung: Ist Chainlit sinnvoll für den Provider-Service?

**Ja, mit Einschränkungen.**

Chainlit funktioniert als schnelles Web-Frontend — ideal für Demos, interne Tests
und Entwickler-Feedback. Die Entkopplung über die Provider-API funktioniert vollständig:
Chainlit weiß nichts von Anthropic/OpenAI/Google, spricht nur gegen `http://provider:8000`.

Für den eigentlichen Heinzel-Betrieb (Agenten, Reasoning-Loops, Tool-Calls) ist Mattermost
die primäre Schnittstelle. Chainlit ist dort wo ein Mensch direkt mit dem Provider-Layer
interagieren will — für Debugging, Prompt-Tests, oder als Entwickler-Cockpit.

**Empfehlung:** Chainlit als optionalen Debug-/Test-Frontend behalten, nicht als
primäre Schnittstelle für Heinzel-Agenten.
