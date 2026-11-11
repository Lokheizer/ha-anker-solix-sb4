# Fork-Hinweise: ha-anker-solix-sb4

Dieses Repository ist ein **inoffizieller Fork** der offiziellen Integration
[anker-charging/ha-anker-solix-official](https://github.com/anker-charging/ha-anker-solix-official).
Hier entwickle ich zusätzliche Features und stelle sie über HACS als
benutzerdefiniertes Repository bereit. Es werden **keine PRs ins offizielle Repo**
erstellt; neue Upstream-Versionen werden regelmäßig in den Fork übernommen.

## Fork-Features

| Feature | Seit Version | Beschreibung |
|---------|--------------|--------------|
| SB4: Sensoren „Verfügbare Ladeleistung“ / „Verfügbare Entladeleistung“ | 0.2.0 | Die bereits gelesenen Register 10036 (Rechargeable Power) und 10038 (Dischargeable Power) stehen bei der Solarbank 4 E5000 Pro als Sensoren (`max_charge_power`, `max_discharge_power`) zur Verfügung. |
| SB4: Sensoren „Kraftwerksstatus“ und „Batteriegesundheit“ (SOH) | 0.2.0 | Zusätzliche Datenpunkte der Anlagen-Übersicht für die Solarbank 4 E5000 Pro: Register 10000 (Plant Status, `power_station_status`) und 10015 (SOH, `battery_soh`). Dafür kann eine Geräte-Config mit `device_class: none` die aus der Einheit abgeleitete Geräteklasse entfernen. Die Register 10022 / 10024 (Total Battery Charge / Discharge Energy) liefern auf der SB4 dieselben Werte wie die vorhandenen Zähler aus 10262 / 10264 und werden deshalb nicht zusätzlich angeboten. |
| MPPT-Leistungen PV1–PV4 (SB4 E5000 Pro) | 0.2.0 | Vier Sensoren „PV1…PV4-Leistung“ in W. PV1–3 = Spannung × Strom (Register 10167–10172). PV4 hat auf der SB4 keine eigenen Register und wird berechnet: Solarleistung (Register 10002) − PV1 − PV2 − PV3. |

### MPPT-Leistungen PV1–PV4

- Die Werte werden auf ganze Watt gerundet und nach unten auf 0 begrenzt (nachts liefert
  das Gerät kleine negative Messwerte).
- **PV4 ist ein Restwert**, kein Messwert: Er enthält die Messtoleranzen der drei anderen
  Eingänge und kann bei schnell wechselnder Einstrahlung kurz springen.
- Fremd-PV (Register 10004) ist in der Berechnung nicht enthalten; der Sensor „Solarstrom“
  zeigt weiterhin die Summe aus eigener und Fremd-PV.
- Spannung und Strom von PV1–3 sind keine eigenen Entitäten, stehen aber in den
  Diagnosedaten (`register_data`: `pv1_voltage`, `pv1_current`, …).
- Technik: Abschnitt `derived_quantities` in der Geräte-YAML, ausgewertet von
  `derived_sensor.py` (Operationen `product` und `difference`).

### SB4: geprüfte, nicht übernommene Register

Diese Register aus der Anker-Modbus-Doku V1.0.0 („Summary – Information“) wurden an einer
Solarbank 4 E5000 Pro geprüft (Gerätetest November 2026) und bewusst **nicht** als Sensoren
übernommen:

| Register | Doku-Name | Befund am Gerät |
|----------|-----------|-----------------|
| 10006 | Active Power | Identisch mit dem vorhandenen Sensor `ac_grid_output_power` (Register 10208). |
| 10022 / 10024 | Total Battery Charge / Discharge Energy | Identisch mit den vorhandenen Zählern aus 10262 / 10264. |
| 10040 / 10041 | Number of PCS / Number of Available PCS | Liefern 257 (`0x0101`) und 4865 (`0x1301`), offenbar byteweise gepackte Werte unklarer Bedeutung. Passt nicht zur Doku; die SB4 ist dort nicht als unterstütztes Gerät gelistet. |

## Installation

Der Fork verwendet bewusst dieselbe Domain `anker_solix_official` wie das Original.
Offizielle Version und Fork können deshalb **nicht parallel** installiert sein.

### Home Assistant 2026.11+ (Marketplace)

Beim Entfernen der offiziellen Version im Marketplace löscht Home Assistant auch den
Konfigurationseintrag mit allen Geräten und Entitäten. **Verlauf und Langzeitstatistik
bleiben in der Datenbank** und hängen an der `entity_id`. Wer das Gerät beim Neueinrichten
exakt gleich benennt, bekommt dieselben `entity_id`s, und Verlauf und Statistik laufen
weiter (nur mit einer Lücke für die Umstellzeit). Getestet in einer HA-2026.11.0-Testinstanz.

1. **Vollbackup inkl. Datenbank** erstellen (*Einstellungen → System → Backups*).
2. **Gerätenamen und `entity_id`s notieren** (*Einstellungen → Entitäten*, nach der
   Integration filtern), vor allem selbst umbenannte; dazu Bereiche und Labels.
3. Im Marketplace die offizielle Integration **„Anker SOLIX Official“ entfernen** und die
   Abfrage zum Löschen von Geräten und Entitäten bestätigen.
4. Marketplace → **Add from link** → `https://github.com/Lokheizer/ha-anker-solix-sb4`
   (dafür muss im Marketplace ein GitHub-Konto verbunden sein), „Anker SOLIX Official
   (SB4 Fork)“ installieren und Home Assistant neu starten.
5. Integration unter *Einstellungen → Geräte & Dienste* neu einrichten und das
   **Gerät exakt wie vorher benennen**.
6. `entity_id`s mit der Liste aus Schritt 2 vergleichen; abweichende auf die alte ID
   umbenennen. Bereiche, Labels und Anzeigenamen neu setzen.

Hinweise:
- Solange die Entitäten fehlen, unter *Statistik* (Entwickler-/Tools-Ansicht) **keine**
  „Löschen“-Reparatur für sie ausführen – das würde die Langzeitstatistik wirklich löschen.
- Der kurzfristige Verlauf wird nach `purge_keep_days` (Standard 10 Tage) ohnehin bereinigt;
  den Umstieg also zügig abschließen.
- Zurück zum Original: derselbe Ablauf in umgekehrter Richtung.

### Home Assistant bis 2026.10 (HACS)

1. Falls installiert: die offizielle Integration **„Anker SOLIX Official“ in HACS entfernen**
   (nur den HACS-Download, **nicht** die Integration unter *Geräte & Dienste* löschen).
2. HACS → ⋮ → **Benutzerdefinierte Repositories**
   - Repository: `https://github.com/Lokheizer/ha-anker-solix-sb4`
   - Typ: **Integration**
3. „Anker SOLIX Official (SB4 Fork)“ herunterladen und Home Assistant neu starten.

Hier bleiben Konfigurationseintrag, Geräte und Entitäten erhalten.
Zurück zum Original: Fork in HACS entfernen, offizielle Version wieder herunterladen.

## Testen in der HA-Testinstanz (lokale ZIPs)

Neue Features werden vor einem Release als **Test-ZIP** manuell installiert –
ohne Release und ohne Marketplace.

1. Test-ZIP bauen (macht Claude in der Sitzung und schickt die Datei):
   ```bash
   scripts/build_test_zip.sh
   ```
   Ergebnis: `dist/anker_solix_official-<version>-dev-<commit>.zip`. Die Version im
   ZIP (z.B. `0.1.0-dev-1a2b3c4`) zeigt in HA, welcher Stand installiert ist;
   `-dirty` bedeutet: enthält noch nicht committete Änderungen.
2. In der Testinstanz den **Inhalt** von `/config/custom_components/anker_solix_official/`
   löschen (damit keine im Branch entfernten Dateien liegen bleiben), dann das ZIP nach
   `/config/custom_components/` entpacken (File-Editor-App, Samba oder SSH).
3. Home Assistant neu starten. Kontrolle: *Einstellungen → Geräte & Dienste →
   Anker SOLIX Official → Info*, Version `…-dev-<commit>`.

Konfigurationseintrag, Geräte und Entitäten bleiben dabei unverändert.
Zurück auf einen offiziellen Stand: die gewünschte Version im Marketplace bzw. in HACS
erneut herunterladen (überschreibt den Ordner) und neu starten.

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
