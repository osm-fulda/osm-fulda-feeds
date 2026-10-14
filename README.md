# maubot + rss — OSM-Monitoring nach Matrix

Postet neue OSM-Hinweise (Notes) und Changesets aus einer frei wählbaren Region
in einen Matrix-Raum. Ersatz für `@rss:t2bot.io` (seit 2023 ungepflegt) und
`@feeds:integrations.ems.host` (reagiert nicht auf Befehle).

Selbst betrieben heißt: eigener Bot-Account, eigener Container, Feed-Fehler im
Log statt geraten. Ein Container, ein Verzeichnis, kein Application Service.

**Als Vorlage gedacht.** Durchgehendes Beispiel ist der Landkreis Fulda —
Bounding Box, Feed-Raten und das daraus abgeleitete Abfrageintervall sind echte
Werte, keine Platzhalter. Für eine andere Region tauschst du die Bounding Box
und rechnest das Intervall neu (siehe [Die Feeds](#die-feeds)).

Der eigene Betriebszustand — Räume, Bot-Account, Host, laufende Abos — gehört
nicht hierher, sondern in eine eigene Datei: siehe
[SETUP.example.md](./SETUP.example.md).

## Was du anpassen musst

| Wert | Wo | Beispiel hier |
|------|-----|---------------|
| Bounding Box | Feed-URLs, Schritt 7 | Landkreis Fulda, `9.4272478,50.3561446,10.0830766,50.8095215` |
| Bot-MXID + Homeserver | Client, Schritt 5 | `@osm-fulda-feed:matrix.org` |
| Web-Login-Benutzer | `data/config.yaml`, Schritt 2 | `osm-fulda-feed` |
| `update_interval` | Instanz-Config, Schritt 6 | `5` — hergeleitet aus der Feed-Rate |
| `admins` | Instanz-Config, Schritt 6 | die eigene persönliche MXID |

Alles andere kann so bleiben.

## Voraussetzungen

- Docker mit Compose-Plugin
- Ein normaler Matrix-Account für den Bot, z. B. `@osm-fulda-feeds:euer.server`.
  Kein Admin, kein Application Service — ein gewöhnlicher Benutzer reicht.

## Einrichtung

### 1. Erster Start

    docker compose up -d

maubot legt `data/config.yaml` an. Im Log steht "Please modify the config file
and restart the container" — der Container läuft aber trotzdem weiter und
lauscht bereits auf 29316. Nicht wundern.

### 2. Config anpassen

`data/config.yaml` gehört UID 1337 und hat Modus 600, ist als normaler Benutzer
also nicht editierbar. Entweder mit `sudo` bearbeiten oder im Container:

    docker compose exec maubot vi /data/config.yaml

Zwei Änderungen:

    admins:
        root: ""               # leer lassen, ein Passwort hat hier keine Wirkung
        osm: DEIN_PASSWORT     # neuer Benutzer für den Web-Login

    server:
        public_url: http://localhost:29316

**Wichtig: nicht `root` als Login-Benutzer verwenden.** maubot lehnt für `root`
jede Passwort-Anmeldung ab, bevor der hinterlegte Wert überhaupt angeschaut
wird (`maubot/config.py`):

    def check_password(self, user: str, passwd: str) -> bool:
        if user == "root":
            return False
        ...

Ein Passwort bei `root` einzutragen ist deshalb nicht falsch, sondern
wirkungslos — der Login scheitert trotzdem, und man sucht den Fehler beim
Passwort. `root` kommt ausschließlich über `server.unshared_secret` rein: wer
beim Login statt Benutzer/Passwort das Secret schickt, ist eingeloggt, und ohne
`user`-Feld ist das per Default `root`. Für die Web-Maske brauchst du einen
zweiten Eintrag unter `admins:` mit beliebigem Namen.

Passwörter werden beim Start durch ihren bcrypt-Hash ersetzt, im Klartext
stehen sie nur bis zum ersten Neustart in der Datei. Eine Falle dabei: schreibt
man wörtlich `password`, ersetzt maubot das nicht durch den Hash, sondern durch
ein zufällig erzeugtes Token — der Eintrag ist dann unbrauchbar.

Dann:

    docker compose restart

### 3. Web-UI öffnen

Port 29316 ist absichtlich nur an localhost gebunden — die UI hat vollen Zugriff
auf den Bot-Account und gehört nicht ins offene Netz. Von außen ist da nichts
erreichbar, auch ohne Firewall-Regel. Vom Laptop:

    ssh -L 29316:127.0.0.1:29316 BENUTZER@HOST

Dann im Browser: <http://localhost:29316/_matrix/maubot/>
Login: der Benutzer aus Schritt 2, **nicht** `root`.

### 4. Plugin hochladen

`xyz.maubot.rss-v0.4.1.mbp` von <https://github.com/maubot/rss/releases> holen,
in der UI unter *Plugins* hochladen.

### 5. Client anlegen

*Clients* → neu:

| Feld | Wert |
|------|------|
| User ID | die MXID des Bots, z. B. `@osm-fulda-feed:matrix.org` |
| Homeserver | z. B. `https://matrix.org` |
| Access Token | siehe unten |
| Device ID | leer lassen |
| Display name | z. B. `OSM Fulda Feeds` |
| Sync | an |
| Autojoin | **an** — sonst nimmt der Bot Einladungen nicht an |
| Enabled | an |

Bietet die Maske ein Passwortfeld statt Access Token, das nehmen — maubot holt
sich das Token dann selbst. Sonst Token besorgen:

    curl -s -X POST https://matrix.org/_matrix/client/v3/login \
      -H 'Content-Type: application/json' \
      -d '{"type":"m.login.password","identifier":{"type":"m.id.user","user":"BOT_BENUTZER"},"password":"BOT_PASSWORT","initial_device_display_name":"maubot"}'

`access_token` aus der Antwort ins Feld.

### 6. Instanz anlegen

*Instances* → neu:

| Feld | Wert |
|------|------|
| ID | `rss` |
| Type | `xyz.maubot.rss` |
| Primary user | der Client aus Schritt 5 |
| Enabled / Running | beides an |

Instanz-Config:

    update_interval: 5
    command_prefix: "rss"
    admins:
    - "@deine-persoenliche-mxid:matrix.org"

`admins` ist hier **nicht** der Web-Login aus Schritt 2, sondern wer im
Matrix-Raum abonnieren darf, ohne dort Moderator zu sein. Deine persönliche
MXID, nicht die des Bots. Bist du im Raum ohnehin Admin, kann die Liste leer
bleiben.

`update_interval: 5` statt der Voreinstellung `60`, weil der WHODIDIT-Feed nur
20 Einträge hält: Bei einem Schub — Mapping-Abend, Importlauf — können in einer
Stunde mehr Changesets anfallen, als der Feed überhaupt vorhält, und der Rest
ist dann weg. Ausführlich unter [Die Feeds](#die-feeds).

### 7. Im Matrix-Raum

Bot einladen, dann — hier mit der Bounding Box des Landkreises Fulda:

    !rss subscribe https://www.openstreetmap.org/api/0.6/notes/feed?bbox=9.4272478,50.3561446,10.0830766,50.8095215
    !rss subscribe https://simon04.dev.openstreetmap.org/whodidit/scripts/rss.php?bbox=9.4272478,50.3561446,10.0830766,50.8095215

Weitere Befehle: `!rss subscriptions`, `!rss unsubscribe <id>`,
`!rss template <id> <vorlage>`, `!rss notice <id> <true|false>`.

Zum Prüfen, ob es läuft, den Changeset-Feed nehmen — bei Fulda ~21 Einträge/Tag,
also binnen 1–2 Stunden sichtbar. Notes mit ~2,9/Tag taugen dafür nicht.

Der Bot braucht keine Moderator-Rechte, einladen genügt. Ein unverschlüsselter
Raum ist nötig — maubot kann E2EE nur mit zusätzlicher Konfiguration.

## Mehrere Räume

Ein Bot bedient beliebig viele Räume. Kein zweiter Client, keine zweite Instanz
— einladen und im neuen Raum `!rss subscribe <url>` tippen.

Abos hängen an der Kombination Feed + Raum. Derselbe Feed kann in mehreren
Räumen liegen, oder Notes hier und Changesets dort.

Abgerufen wird jede URL trotzdem nur einmal pro Intervall; das Plugin verteilt
das Ergebnis an alle Räume, die sie abonniert haben. Kein zusätzlicher Traffic
pro Raum.

`!rss subscriptions` zeigt immer nur die Abos des Raums, in dem der Befehl
getippt wird.

Bei vielen Räumen greift `spam_sleep: 2` — zwei Sekunden Pause zwischen den
Sendungen, damit matrix.org nicht drosselt.

## Link-Vorschauen abschalten

Jede Bot-Nachricht enthält einen Link, Element hängt je nach Einstellung eine
Vorschaukarte dran. Das erzeugt der Client, nicht der Bot — maubot kann es nicht
unterdrücken.

Abschalten geht auf drei Ebenen. Die Raum-Ebene ist die einzige, die man als
Betreiber selbst setzen kann:

| Ebene | Wirkung | Wie |
|-------|---------|-----|
| Raum (State-Event) | Standard für alle im Raum | siehe unten, braucht Moderator |
| Benutzer | nur für sich selbst | Element-Einstellungen |
| Server | alle Räume der Instanz | `url_preview_enabled: false`, auf matrix.org nicht möglich |

Raum-Default setzen — in Element: Raumeinstellungen → *URL-Vorschau* →
"URL-Vorschauen standardmäßig für Teilnehmer dieses Raums aktivieren" abwählen.
Oder per API:

    TOKEN=...   # persönlicher Access Token, Element: Einstellungen -> Hilfe & Über -> Erweitert

    # Alias -> Room-ID  ( # = %23, : = %3A )
    ROOM=$(curl -s "https://matrix.org/_matrix/client/v3/directory/room/%23DEIN-ALIAS%3Amatrix.org" \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["room_id"])')

    # ! für die URL escapen
    ROOM_ENC=$(python3 -c 'import sys,urllib.parse as u; print(u.quote(sys.argv[1], safe=""))' "$ROOM")

    curl -X PUT "https://matrix.org/_matrix/client/v3/rooms/$ROOM_ENC/state/org.matrix.room.preview_urls/" \
      -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
      -d '{"disable": true}'

Nicht `ROOM_ENC=${ROOM/#!/%21}` schreiben — in der interaktiven Bash frisst die
History-Expansion das `!` (`bash: !/: event not found`). Entweder `set +H` davor
oder den Umweg über Python wie oben.

**Der Raum-Default wirkt nur bei Leuten ohne eigene Einstellung.** Elements
Rangfolge ist `device > room-device > room-account > account > room > default` —
das State-Event steht weit hinten. Wer die Vorschau in seinem Client einmal
bewusst aktiviert hat, sieht sie weiterhin und muss sie selbst abwählen:

- nur dieser Raum: Raumeinstellungen → *URL-Vorschau* → "für diesen Raum
  aktivieren (betrifft nur dich)"
- überall: Einstellungen → *Präferenzen* → "Vorschau von Links standardmäßig
  aktivieren"

Erzwingen lässt es sich also nicht. Der einzige Hebel, der wirklich bei allen
greift, ist bot-seitig: Vorschauen brauchen ein `<a href>` im gerenderten HTML,
ein Template ohne Markdown-Link erzeugt keine.

    !rss template <id> New in $feed_title: **$title** — `$link`

Kostet die Klickbarkeit und lohnt sich normalerweise nicht. Sinnvoller: den
Hinweis in Raumbeschreibung, angepinnte Nachricht oder OSM-Wiki legen und die
Entscheidung den Leuten überlassen.

Aktuellen Stand prüfen:

    curl -s "https://matrix.org/_matrix/client/v3/rooms/$ROOM_ENC/state/org.matrix.room.preview_urls/" \
      -H "Authorization: Bearer $TOKEN"

Erwartet: `{"disable":true}`

## Die Feeds

### Warum die Größe des Feeds zählt

Ein RSS-Feed ist ein **Fenster, kein Archiv**. Der Server liefert immer nur die
letzten N Einträge; taucht ein neuer auf, fällt der älteste hinten raus. Der Bot
merkt sich, was er zuletzt gesehen hat, und postet beim nächsten Abruf alles,
was seitdem dazugekommen ist.

Daraus folgt die einzige Zahl, die im Betrieb wirklich zählt:

> **Wie lange darf der Bot stehen, ohne dass etwas verloren geht?**
> So lange, wie der Feed braucht, um sich einmal komplett zu erneuern.

Steht der Bot länger, sind die Einträge, die in der Zwischenzeit hinten
rausgefallen sind, endgültig weg — sie werden nie gepostet, und man merkt es
nicht, weil ja weiterhin neue Meldungen kommen.

Wie lang dieses Fenster ist, hängt an zwei Dingen: **wie viele Einträge der Feed
hält** (fest vorgegeben) und **wie schnell deine Region sie nachliefert**
(hängt an der Größe und Aktivität des Gebiets). Deshalb die Angaben pro Tag —
sie sind kein Selbstzweck, sondern nur der Zwischenschritt zur Fensterlänge:

    Fenster = Einträge im Feed ÷ neue Einträge pro Tag

### Die Zahlen für den Landkreis Fulda

Beobachtete Werte, kein Richtwert für anderswo:

| Feed | hält | neue Einträge | Fenster |
|------|------|---------------|---------|
| Notes (osm.org) | 100 Einträge | ~2,9 pro Tag | 100 ÷ 2,9 ≈ **35 Tage** |
| Changesets (WHODIDIT) | 20 Einträge | ~21 pro Tag | 20 ÷ 21 ≈ **knapp 1 Tag** |
| Neueröffnungen (bald-in.de) | 43 Einträge | ~0,6 pro Tag | 43 ÷ 0,6 ≈ **10 Wochen** |
| Neueröffnungen (Spüre Fulda) | 3 Beiträge | wenige pro Jahr | alle seit 12/2025, **> 10 Monate** |

Die beiden Neueröffnungs-Feeds sind bereits auf Fulda gefiltert (`?stadt=fulda`
bzw. Kategorie-Feed). Stand Oktober 2026; es sind Sammel- bzw. Ankündigungs-
seiten, keine Quelle zum Übernehmen nach OSM — nur Hinweise fürs Mapping.

`neueroeffnung.info` ist bewusst nicht abonniert: Der Feed ist DACH-weit
(~130 Einträge pro Tag, Fenster ≈ 1 Tag) und lässt sich serverseitig nicht
filtern. Ohne vorgeschalteten Filter würde er den Raum fluten.

Die Changesets sind also der enge Fall: Der Bot darf keinen ganzen Tag stehen.
Bei den Notes wären fünf Wochen Ausfall verkraftbar.

Umgekehrt heißt das auch: Der Changeset-Feed eignet sich zum Prüfen, ob alles
läuft — bei ~21 Einträgen am Tag ist ein kompletter stiller Tag praktisch
ausgeschlossen. Der Notes-Feed taugt dafür mit ~2,9 am Tag nicht.

### Für die eigene Region

Die Rate musst du nicht schätzen, du kannst das Fenster direkt ablesen: Feed-URL
im Browser öffnen und beim **ältesten** Eintrag auf den Zeitstempel schauen. Wie
weit der zurückliegt, ist genau dein Fenster.

In einer aktiveren Region als Fulda schrumpft es entsprechend — bei der
dreifachen Änderungsrate hält der WHODIDIT-Feed statt einem Tag nur noch acht
Stunden.

Bounding Box: Relation der eigenen Region auf osm.org suchen und deren Bounding
Box übernehmen. Für Fulda ist das
[r62700](https://www.openstreetmap.org/relation/62700) →
`9.4272478,50.3561446,10.0830766,50.8095215`.

### Was `update_interval` damit zu tun hat — und was nicht

Häufiger abfragen **verlängert das Fenster nicht.** Gegen einen langen Ausfall
hilft nur die Größe des Feeds, und die ist vorgegeben.

Wogegen ein kurzes Intervall hilft, ist ein **Schub**: Wenn zwischen zwei
Abrufen mehr Einträge auflaufen, als der Feed überhaupt hält, ist der Rest weg,
auch bei laufendem Bot. Bei einem Mapping-Abend mit 30 Changesets in einer
Stunde würde ein Intervall von 60 Minuten in genau diese Falle laufen — der
WHODIDIT-Feed hält nur 20. Mit 5 Minuten sammeln sich pro Abruf nur wenige
Einträge an, und das kann nicht passieren.

Der zweite Effekt ist schlicht, wie frisch die Meldungen ankommen: mit `5`
innerhalb von fünf Minuten, mit `60` bis zu einer Stunde später.

Der offizielle Changeset-Feed `openstreetmap.org/history/feed?bbox=…` ist
bewusst nicht in Benutzung: jeder Eintrag enthält drei `<link rel="alternate">`,
und viele Clients wählen die API-Variante statt der HTML-Seite — man landet dann
auf rohem osmChange-XML statt auf der Changeset-Seite.

WHODIDIT kann statt `bbox=` auch `wkt=POLYGON((...))`, dann exakt auf die
Kreisgrenze zugeschnitten (Raster ca. 1 km, Koordinaten sind lat×100 lon×100).
Ergibt bei Fulda eine ~1,7 kB lange URL — funktioniert, ist aber unhandlich.

## Betrieb

Logs:

    docker compose logs -f maubot

Anders als bei den gehosteten Bots siehst du Feed-Fehler hier direkt, statt sie
am ausbleibenden Post zu erraten.

Das rss-Plugin macht bei fehlschlagenden Feeds ein exponentielles Backoff bis zu
`max_backoff` (Standard 7200 Minuten = 5 Tage). Ein Feed, der länger weg war,
kommt also nicht sofort nach der Reparatur zurück — Instanz neu starten, wenn es
schneller gehen soll.

Faustregel zur Kontrolle: Ein ganzer stiller Tag im Changeset-Raum heißt, dass
etwas kaputt ist — und zugleich, dass das Fenster des WHODIDIT-Feeds gerade
abläuft. Siehe [Die Feeds](#die-feeds).

Direkt in die Plugin-Datenbank schauen (Abos, Fehlerzähler, zuletzt gesehene
Einträge):

    docker compose exec maubot sqlite3 /data/dbs/rss.db \
      "select id, error_count, url from feed; select room_id, feed_id from subscription;"

## Was nicht im Repo steht

Client, Instanz und Abos werden über die Web-UI beziehungsweise `!rss subscribe`
angelegt und liegen danach in `data/maubot.db` und `data/dbs/rss.db`. Es gibt
keine Konfigurationsdatei, aus der sich das reproduzieren ließe — das rss-Plugin
hat für Abos keine API, und der Stand "was wurde schon gesehen" darf auch gar
nicht deklarativ sein, sonst käme bei jedem Neuaufsetzen eine Flut alter
Einträge.

Dieses Repo ist deshalb die Anleitung und der Container, nicht der Zustand. Der
Zustand ist `data/` — ignoriert, weil dort der Access Token des Bot-Accounts
liegt, und zu sichern über den Umzugsweg unten.

## Umzug auf einen anderen Host

`data/` enthält Access Token, Client, Instanz, Abos und den Stand "was wurde
schon gesehen". Mitkopieren, dann ist nichts neu einzurichten — und es gibt
keine Flut alter Einträge beim ersten Start.

**Nie beide Instanzen gleichzeitig laufen lassen.** Zwei maubots mit demselben
Access Token syncen parallel: doppelte Posts und Device-Konflikte.

    # Quelle stoppen
    cd ~/osm-fulda-feeds && docker compose down

    # packen -- via Container, dann braucht es kein sudo auf dem Host
    docker run --rm -v ~/osm-fulda-feeds:/src:ro -v /tmp:/out alpine \
      tar -C /src --numeric-owner -czf /out/osm-fulda-feeds.tgz .

    scp /tmp/osm-fulda-feeds.tgz ziel:/tmp/

    # auf dem Ziel
    mkdir -p ~/osm-fulda-feeds
    docker run --rm -v ~/osm-fulda-feeds:/dst -v /tmp:/in:ro alpine \
      tar -C /dst --numeric-owner -xzf /in/osm-fulda-feeds.tgz
    cd ~/osm-fulda-feeds && docker compose up -d && docker compose logs -f

`--numeric-owner` ist der Punkt, der sonst schiefgeht: ohne das mappt tar UID
1337 auf einen Namen und beim Auspacken auf eine andere UID — dann kommt maubot
nicht mehr an seine eigenen Dateien.

Im Log erwarten:

    Client started, starting plugin instances...
    Started instance of xyz.maubot.rss v0.4.1
    Polling 2 feeds

Danach `!rss ls` in einem der Räume — stehen die Abos drin, ist der Umzug durch.

## Lizenz

[CC BY-SA 4.0](./LICENSE) — Namensnennung, Weitergabe unter gleichen
Bedingungen. Also: nimm das hier als Vorlage für deine Region, pass die Bounding
Box und die Zahlen an, nenne die Quelle und stell das Ergebnis unter dieselbe
Lizenz. Keine Copyleft-Pflicht für den Bot selbst — maubot und das rss-Plugin
haben ihre eigenen Lizenzen.
