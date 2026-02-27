# H.E.I.N.Z.E.L.

**H**ighly **E**volved **I**ntelligent **N**ode with **Z**ero-overhead **E**xecution **L**ayer

Ein modulares Multi-Agent-System auf Basis von Claude. Heinzel sind autonome KI-Agenten mit genetisch inspirierten Persönlichkeitsprofilen — sie denken, lernen, kommunizieren und entwickeln sich weiter.

---

## Aufbau

Dieses Repository wächst MVP für MVP:

| Branch | Inhalt |
|---|---|
| `mvp-00` | Infrastruktur — Docker, Netzwerk, Postgres, Mattermost, JupyterHub, Caddy, Portainer, Gitea |
| `mvp-01` | LLM Provider Gateway — OpenAI/Anthropic/Google, Multimodal, CLI + Chainlit Frontend |
| `main` | Aktueller Stand |
| `development` | Laufende Entwicklung |

Jeder MVP-Branch bleibt erhalten — nachvollziehbar, lesbar, als Lernpfad.

## Struktur

```
src/          — LLM Provider (OpenAI, Anthropic, Google) + Hilfsmodule
test/         — Tests (120 passing)
frontend/     — CLI + Chainlit Web-UI
docker/       — Compose-Dateien pro Service
config/       — Provider-Configs, Port-Vergabe
docs/         — Feature Requests, Dokumentation
scripts/      — Setup-Scripts
```

## Port-Schema

| Bereich | Range | Beispiel |
|---|---|---|
| Basis-Infrastruktur | 12001–12100 | Postgres, Mattermost, JupyterHub |
| Services | 12101–12200 | LLM Provider (12101) |
| Tools | 12201–12300 | Chainlit (12201) |
| Heinzels | 12501+ | #1/Riker (12501) |

## Voraussetzungen

- Docker + Docker Compose
- API-Key für OpenAI und/oder Anthropic und/oder Google

## Lizenz

*(folgt)*
