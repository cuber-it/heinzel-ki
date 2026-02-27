# H.E.I.N.Z.E.L.

**H**ighly **E**volved **I**ntelligent **N**ode with **Z**ero-overhead **E**xecution **L**ayer

Ein modulares Multi-Agent-System auf Basis von Claude. Heinzel sind autonome KI-Agenten mit genetisch inspirierten Persönlichkeitsprofilen — sie denken, lernen, kommunizieren und entwickeln sich weiter.

---

## Was ist H.E.I.N.Z.E.L.?

H.E.I.N.Z.E.L. ist eine selbst-gehostete Plattform für KI-Agenten die auf eigener Hardware laufen, mit eigenen Daten arbeiten und über Mattermost kommunizieren. Von einem einfachen LLM-Provider bis hin zu koordinierten Multi-Agenten-Teams — Schritt für Schritt aufgebaut.

---

## Voraussetzungen

- Docker + Docker Compose
- Linux-Host (empfohlen: 16GB+ RAM)
- API-Key für mindestens einen LLM-Provider (OpenAI / Anthropic / Google)

---

## Repository-Struktur

```
src/          — LLM Provider (OpenAI, Anthropic, Google) + Hilfsmodule
test/         — Test-Suite
frontend/     — CLI + Chainlit Web-UI
docker/       — Compose-Dateien pro Service
config/       — Provider-Configs, Port-Vergabe
docs/         — Dokumentation pro MVP
scripts/      — Setup-Scripts
```

---

## MVP-Übersicht

| MVP | Inhalt | Doku |
|-----|--------|------|
| mvp-00 | Basis-Infrastruktur (Docker-Stack) | [docs/mvp-00.md](docs/mvp-00.md) |
| mvp-01 | LLM Provider Gateway + Frontends | [docs/mvp-01.md](docs/mvp-01.md) |

---

## Port-Schema

| Bereich | Range | Beispiele |
|---------|-------|-----------|
| Basis-Infrastruktur | 12001–12100 | Postgres, Mattermost, JupyterHub |
| Services | 12101–12200 | LLM Provider (12101) |
| Tools | 12201–12300 | Chainlit (12201) |
| Heinzels | 12501+ | #1/Riker (12501) |

Vollständige Port-Liste: [`config/ports.yaml`](config/ports.yaml)

---

## Lizenz

*(folgt)*
