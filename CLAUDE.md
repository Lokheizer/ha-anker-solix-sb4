# CLAUDE.md

Leitfaden für Claude-Sitzungen in diesem Repository. Ausführliche Fork-Doku: [FORK.md](FORK.md).

## Was ist dieses Repo?

Inoffizieller Fork der offiziellen Home-Assistant-Integration
[anker-charging/ha-anker-solix-official](https://github.com/anker-charging/ha-anker-solix-official)
(lokales Modbus TCP) mit zusätzlichen Features, vor allem für die
**Anker SOLIX Solarbank 4 E5000 Pro (SB4)**. Verteilung über den HA-Marketplace
(2026.11+) bzw. HACS als benutzerdefiniertes Repository.

- Es werden **niemals PRs, Issues oder Kommentare im offiziellen Repo** erstellt.
  PRs immer mit Base `Lokheizer/ha-anker-solix-sb4:main`.
- Neue Upstream-Versionen kommen über den Workflow `upstream-sync.yml` als PR herein.

## Feste Regeln (Merge-Fähigkeit mit Upstream)

- Domain `anker_solix_official`, Ordner `custom_components/anker_solix_official/`
  und Entity-Unique-IDs **nicht ändern** (Drop-in-Ersatz für die offizielle Version).
- Neue Funktionen bevorzugt in **neuen Modulen/Dateien**; Upstream-Dateien nur
  minimal anfassen (Import, Hook, wenige Zeilen). Kein Umformatieren, Umbenennen
  oder Aufräumen von Upstream-Code.
- In `manifest.json` gehören `version`, `documentation`, `issue_tracker`,
  `codeowners` dem Fork (werden beim Upstream-Sync automatisch wiederhergestellt,
  siehe `.github/scripts/resolve_manifest.py`). Formatierung dieser Zeilen beibehalten.
- README: nur der Fork-Block oben ist Fork-spezifisch; restlichen Upstream-Text
  nicht umschreiben. Fork-Doku gehört in `FORK.md`.
- Jedes neue Feature in der Feature-Tabelle in `FORK.md` eintragen.
- Neue Übersetzungsschlüssel in **allen** Dateien unter `translations/` und in
  `strings.json` ergänzen.

## Architektur (Kurzüberblick)

- `config_flow.py` – Einrichtung per IP/mDNS; ermittelt die Geräte-PN.
- `modbus_client.py` – Modbus-TCP-Zugriff; bildet `pn_hash = sha256(salt + PN)`,
  daraus folgt die Gerätekonfiguration `config/<pn_hash>.yaml`.
- `config/*.yaml` – **deklarative Gerätedefinition** (Register, Sensoren, Number,
  Select, Switch, Produktnamen). SB4 E5000 Pro:
  `config/58f0132b5f7979b2cfa43a0eb1fca770053288032386ff6a4da5ed2d72d4ea35.yaml`.
  Neue Datenpunkte nach Möglichkeit zuerst hier ergänzen statt Python-Code zu ändern.
- `device_config.py`, `config_utils.py` – Laden/Auswerten der YAML-Configs.
- `coordinator.py`, `batch_reader.py`, `modbus_manager.py` – Polling (Standard 5 s),
  gebündeltes Lesen, Schreibzugriffe.
- `base_entity.py`, `sensor.py`, `number.py`, `select.py`, `switch.py` – Entitäten.
- `diagnostics.py` – HA-Diagnosedaten.

## Entwicklung & Tests

Das Setup-Skript der Cloud-Umgebung legt `.venv/` im Repo an (Python 3.13,
Home Assistant, pytest-homeassistant-custom-component, pymodbus, ruff).
Falls `.venv/` fehlt:

```bash
python3.13 -m venv .venv && .venv/bin/pip install -q pytest pytest-asyncio pytest-cov homeassistant pytest-homeassistant-custom-component "pymodbus>=3.10.0,<4.0.0" "PyYAML>=5.4.0" ruff
```

Vor jedem Commit:

```bash
.venv/bin/python -m pytest -q
```

```bash
git diff --name-only --diff-filter=ACMR origin/main -- '*.py' | xargs -r .venv/bin/ruff check
```

- Ruff nur auf **geänderte Dateien** anwenden (wie die CI). Der Upstream-Code hat
  bestehende Befunde; diese nicht im Fork beheben (unnötige Merge-Konflikte).
- Nicht die System-Python-Umgebung verwenden (Abhängigkeiten lassen sich dort nicht bauen).
- Tests liegen in `tests/` (Fakes in `tests/_fakes.py`); neue Features bekommen eigene Tests.
- Die echten Geräte sind aus der Cloud-Sitzung **nicht erreichbar** (Modbus nur im LAN).
  Verifikation am Gerät macht der User in seiner HA-Testinstanz; dafür ggf. um
  Diagnosedaten/Logs bitten.

## Versionen & Releases

- Eigenes SemVer-Schema, unabhängig von Upstream (Start `0.1.0`):
  Minor = neue Features / Upstream-Übernahme, Patch = Fixes.
- Bei einem Release: `version` in `manifest.json` anheben und Versionstabelle
  (Fork-Version ↔ Upstream-Basis) in `FORK.md` ergänzen.
- Das GitHub-Release (Tag `vX.Y.Z`, kein Pre-Release) erstellt der User;
  `release.yml` hängt anschließend `anker_solix_official.zip` an.
