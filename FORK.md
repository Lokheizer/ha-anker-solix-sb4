# Fork-Hinweise: ha-anker-solix-sb4

Dieses Repository ist ein **inoffizieller Fork** der offiziellen Integration
[anker-charging/ha-anker-solix-official](https://github.com/anker-charging/ha-anker-solix-official).
Hier entwickle ich zusätzliche Features und stelle sie über HACS als
benutzerdefiniertes Repository bereit. Es werden **keine PRs ins offizielle Repo**
erstellt; neue Upstream-Versionen werden regelmäßig in den Fork übernommen.

## Fork-Features

| Feature | Seit Version | Beschreibung |
|---------|--------------|--------------|
| _noch keine_ | – | – |

## Installation über HACS

1. Falls installiert: die offizielle Integration **„Anker SOLIX Official“ in HACS entfernen**
   (nur den HACS-Download, **nicht** die Integration unter *Geräte & Dienste* löschen).
2. HACS → ⋮ → **Benutzerdefinierte Repositories**
   - Repository: `https://github.com/Lokheizer/ha-anker-solix-sb4`
   - Typ: **Integration**
3. „Anker SOLIX Official (SB4 Fork)“ herunterladen und Home Assistant neu starten.

Der Fork verwendet bewusst dieselbe Domain `anker_solix_official` wie das Original.
Dadurch bleiben bestehende Config-Entries, Geräte, Entitäten und deren Historie
beim Wechsel erhalten. Offizielle Version und Fork können deshalb **nicht parallel**
installiert sein. Zurück zum Original: Fork in HACS entfernen, offizielle Version
wieder herunterladen.

## Versionen

Der Fork hat ein eigenes, vom Upstream unabhängiges Versionsschema (SemVer):

- **Minor** (`0.x.0`): neue Fork-Features oder Übernahme einer neuen Upstream-Version
- **Patch** (`0.x.y`): Fehlerbehebungen

| Fork-Version | Upstream-Basis | Bemerkung |
|--------------|----------------|-----------|
| 0.1.0 | 1.5.1 | Initialer Fork |

### Release erstellen

1. `version` in `custom_components/anker_solix_official/manifest.json` anheben
   und obige Tabelle ergänzen.
2. Commit auf `main` pushen.
3. GitHub → Releases → *Draft a new release*, Tag `vX.Y.Z` (z.B. `v0.1.0`), veröffentlichen.
4. Der Workflow `release.yml` baut `anker_solix_official.zip` und hängt es an das
   Release an – HACS installiert dieses ZIP.

## Upstream-Versionen übernehmen

### Automatisch (Workflow `upstream-sync.yml`)

Der Workflow läuft wöchentlich (montags) und kann jederzeit manuell unter
*Actions → Upstream Sync → Run workflow* gestartet werden. Er

1. holt `main` des offiziellen Repos (ohne Tags, damit keine Kollision mit eigenen Tags),
2. beendet sich, wenn bereits alles übernommen ist,
3. versucht `upstream/main` in `main` zu mergen:
   - **ohne Konflikt:** Ergebnis wird als Branch `upstream-sync` gepusht,
   - **nur `manifest.json` im Konflikt** (der Normalfall bei jeder neuen Upstream-Version):
     wird automatisch aufgelöst – Upstream-Manifest übernehmen, Fork-Felder
     (`version`, `documentation`, `issue_tracker`, `codeowners`) wiederherstellen
     (`.github/scripts/resolve_manifest.py`),
   - **mit Konflikt:** der reine Upstream-Stand wird als `upstream-sync` gepusht,
     der PR zeigt die Konflikte an (lokal auflösen, siehe unten),
4. öffnet (bzw. aktualisiert) einen PR `upstream-sync → main` **in diesem Repo**,
5. startet Tests und Hassfest für den Branch.

Wenn Upstream Dateien unter `.github/workflows` ändert, darf `GITHUB_TOKEN` den
Branch nicht pushen; dann wird stattdessen ein Issue erstellt. Abhilfe: Secret
`UPSTREAM_SYNC_TOKEN` (fine-grained PAT nur für dieses Repo, Rechte *Contents*,
*Pull requests*, *Workflows*: Read & write) anlegen – oder manuell mergen.

**Einmalige Voraussetzung:** GitHub → *Settings → Actions → General → Workflow permissions*
→ **„Allow GitHub Actions to create and approve pull requests“** aktivieren.
Ohne diese Einstellung wird der Branch gepusht, aber kein PR erstellt.

### Manuell

```bash
git remote add upstream https://github.com/anker-charging/ha-anker-solix-official.git
```

```bash
git fetch upstream --no-tags
```

```bash
git checkout main && git merge upstream/main
```

### Typische Konflikte

- **`manifest.json` → `version`**: immer die **Fork-Version** behalten (und ggf.
  anheben); die neue Upstream-Version in der Versionstabelle oben eintragen.
- **`manifest.json` → `documentation`, `issue_tracker`, `codeowners`**: Fork-Werte behalten.
- **`README.md` Kopfbereich** (Badges, Fork-Hinweis): Fork-Block behalten,
  Upstream-Änderungen am restlichen Text übernehmen.
- **`hacs.json` → `name`**: Fork-Namen behalten.

Danach Tests ausführen (`pytest tests/`) und prüfen, ob die eigenen Erweiterungen
mit den Upstream-Änderungen kompatibel sind.

## Richtlinien für Erweiterungen

Damit Upstream-Merges konfliktarm bleiben:

- Neue Funktionen bevorzugt in **neuen Dateien/Modulen** bzw. eigenen YAML-Configs
  unterbringen; Upstream-Dateien nur minimal (z.B. ein Import/Hook) anpassen.
- Keine reinen Formatierungs- oder Umbenennungsänderungen an Upstream-Code.
- Domain, Ordnername und Entity-Unique-IDs nicht ändern.
- Für neue Features Tests unter `tests/` ergänzen.
- Fork-Features oben in der Feature-Tabelle dokumentieren.
- Pull Requests immer gegen **`Lokheizer/ha-anker-solix-sb4:main`** stellen – nie gegen das offizielle Repo.
