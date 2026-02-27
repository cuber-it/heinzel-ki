# Git-Workflow

## Branch-Strategie

| Branch       | Zweck                                              |
|-------------|---------------------------------------------------|
| `main`       | Nur gemergte, getaggte MVPs — niemals direkt editieren |
| `development`| Integrations-Branch — Basis für alle Arbeiten      |
| `mvp-XX`     | Kurzlebiger Arbeits-Branch — wird nach Merge + Tag gelöscht |

## MVP-Workflow

```bash
# 1. Neuen MVP-Branch von development abzweigen
git checkout development
git checkout -b mvp-01

# 2. Arbeiten, committen
git add .
git commit -m "feat: ..."

# 3. In development mergen
git checkout development
git merge mvp-01 --no-ff

# 4. Wenn MVP abgeschlossen: in main mergen + taggen
git checkout main
git merge development --no-ff
git tag mvp-01

# 5. MVP-Branch löschen — der Tag ist der permanente Snapshot
git branch -d mvp-01
```

## Commit-Konventionen

Einzeilig, Präfix nach Typ:

| Präfix     | Verwendung                        |
|-----------|-----------------------------------|
| `feat:`    | Neue Funktionalität               |
| `fix:`     | Bugfix                            |
| `refactor:`| Umstrukturierung ohne Funktionsänderung |
| `docs:`    | Dokumentation                     |
| `chore:`   | Maintenance, Dependencies         |

Details gehören in `CHANGES.md` mit Commit-ID — nicht in die Commit-Message.

## Releases

- Jeder MVP bekommt einen Git-Tag (`mvp-00`, `mvp-01`, ...)
- `main` enthält immer den letzten stabilen Stand
- `CHANGES.md` wird pro MVP gepflegt
- `README.md` beschreibt immer den aktuellen Stand
