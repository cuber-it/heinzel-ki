# Workflow

## Branch-Strategie

```
main          — nur gemergte, getaggte MVPs. Wird nie direkt bearbeitet.
development   — Integrationsstation. Basis für alle MVP-Branches.
mvp-XX        — bleibt erhalten als Snapshot des jeweiligen Stands.
```

## Ablauf

```
1. Von development abzweigen
   git checkout development
   git checkout -b mvp-01

2. Arbeiten, committen
   git add .
   git commit -m "..."

3. Fertig → nach development mergen
   git checkout development
   git merge mvp-01 --no-ff

4. Ok → nach main mergen + taggen
   git checkout main
   git merge development --no-ff -m "mvp-01: <Beschreibung>"
   git tag mvp-01

5. mvp-01 Branch bleibt erhalten (nicht löschen)
```

## Branches

| Branch | Zweck |
|---|---|
| `main` | Sauber, nur gemergte MVPs |
| `development` | Durchgangsstation, immer aktuell |
| `mvp-00` | Snapshot: Infrastruktur |
| `mvp-01` | *(folgt)* |
