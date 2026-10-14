# SETUP — eigener Betriebszustand

Vorlage. Kopieren nach `SETUP.local.md` und ausfüllen:

    cp SETUP.example.md SETUP.local.md

`SETUP.local.md` ist ignoriert und bleibt lokal. Hier stehen keine Passwörter
und keine Access Tokens — die liegen ausschließlich in `data/`. Diese Datei
beantwortet nur "wo läuft es und was ist abonniert", also das, was man nach
sechs Monaten Pause als Erstes wissen will.

## Läuft wo

| | |
|---|---|
| Host | `BENUTZER@HOST` |
| Pfad | `~/osm-fulda-feeds` |
| Container | `osm-fulda-feeds-maubot-1` |
| Restart-Policy | `unless-stopped` |

## Accounts

| | |
|---|---|
| Bot-MXID | `@BOT:HOMESERVER` |
| Homeserver | `https://HOMESERVER` |
| Web-Login-Benutzer | `BENUTZER` (Passwort im Passwortmanager, Hash in `data/config.yaml`) |
| Instanz-`admins` | `@DEINE-MXID:HOMESERVER` |

## Räume und Abos

| Raum | Room-ID | Feed |
|------|---------|------|
| | `!RAUM_ID:HOMESERVER` | Changesets (WHODIDIT) |
| | `!RAUM_ID:HOMESERVER` | Notes (osm.org) |
| | `!RAUM_ID` | Neueröffnungen (bald-in.de, Spüre Fulda) |

Bounding Box: `MIN_LON,MIN_LAT,MAX_LON,MAX_LAT`
Entspricht Relation [rXXXXX](https://www.openstreetmap.org/relation/XXXXX).

Aktueller Stand direkt aus der Datenbank:

    docker compose exec maubot sqlite3 /data/dbs/rss.db \
      "select id, error_count, url from feed; select room_id, feed_id from subscription;"

## Instanz-Config

    update_interval: MINUTEN
    command_prefix: "rss"
    admins:
    - "@DEINE-MXID:HOMESERVER"

## Zugang zur Web-UI

    ssh -L 29316:127.0.0.1:29316 BENUTZER@HOST
    # dann http://localhost:29316/_matrix/maubot/

## Notizen

Was hier sonst noch abweicht vom README — geänderte Templates, abgeschaltete
Link-Vorschauen, Besonderheiten des Homeservers.
