🏪 **Was hier passiert**

Ein Bot meldet Neueröffnungen im Landkreis Fulda. Quellen: bald-in.de, Spüre Fulda und neueroeffnung.info. Viele Meldungen kommen Monate vor der Eröffnung. Erst wenn das Geschäft offen ist, lässt es sich sauber in OpenStreetMap eintragen.

**Was tun?**
1. Meldung lesen: Wann und wo eröffnet was?
2. Liegt die Eröffnung in der Zukunft: auf die Meldung **antworten** und eine Erinnerung setzen (siehe unten).
3. Ist das Geschäft offen: vor Ort nachsehen, dann in OSM eintragen oder den alten Eintrag anpassen. Die Meldungen sind Hinweise fürs Mapping, keine Quelle zum Abzeichnen.
4. ✅ unter die Meldung setzen, damit niemand doppelt einträgt.

**Erinnerung setzen**
Einmalig, damit der Bot deutsche Datumsangaben versteht:

```
!remind locale de_de
```

Dann auf die Meldung antworten mit:

```
!remind 01.05.2027 um 10:00 Eröffnung prüfen, in OSM eintragen
```

Auch möglich: `!remind in 6 monaten …` oder `!remind freitag um 9:00 …`. Am Termin erwähnt dich der Bot mit einer Antwort auf die Meldung.

**Tipps**
- Wer auch erinnert werden will, setzt 👍 unter die Bestätigung des Bots.
- `!remind list` zeigt deine Erinnerungen. Löschen: die eigene `!remind`-Nachricht entfernen.
- In einem Thread klappt es nur, wenn `!remind` die erste Nachricht im Thread ist. Sonst lieber direkt auf die Meldung antworten.
