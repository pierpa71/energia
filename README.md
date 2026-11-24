# Energia: Home Assistant → InfluxDB

Raccoglie le **statistiche orarie** (kWh) dei sensori configurati nella dashboard Energia di Home Assistant,
le salva in InfluxDB 2 e le mostra in una dashboard web (casa e auto elettrica).

Versione attuale: **v1.3** (vedi [Versioni](#versioni)).

## Architettura

Due container, definiti in `docker-compose.yml`:

| container           | porta  | cosa fa                                                                  |
|---------------------|--------|--------------------------------------------------------------------------|
| `influxdb-energia`  | 8086   | InfluxDB 2.7, dati in `data/influxdb/`                                    |
| `energia-collector` | 8090   | sync periodico da Home Assistant + dashboard web e API (FastAPI)         |

Il collector è su una rete bridge e pubblica esplicitamente la porta della dashboard
(`${WEB_PORT:-8090}:8090`). Dentro i container raggiunge InfluxDB come `http://influxdb:8086` e
Home Assistant sull'host come `http://host.containers.internal:8123`: questi valori sono impostati in
`docker-compose.yml` e hanno la precedenza su quelli del `.env`.

## Avvio (Podman)

1. `cp .env.example .env` e completa:
   - `HA_TOKEN`: in HA *Profilo → Sicurezza → Token di accesso a lunga durata* → crea un token e incollalo.
     In alternativa lascialo vuoto e inseriscilo dopo dall'interfaccia (Impostazioni). Serve un utente HA
     **amministratore** per il riconoscimento automatico dei sensori dell'auto.
   - `INFLUX_PASSWORD` e `INFLUX_TOKEN`: valori casuali (es. `openssl rand -hex 32`).
2. `podman-compose up -d --build`
3. `podman logs -f energia-collector`: al primo avvio importa tutto lo storico disponibile in HA,
   poi ogni 15 minuti aggiunge le ore nuove.

- **Dashboard**: `http://<host>:8090`
- **InfluxDB UI**: `http://<host>:8086` (utente `admin`, password in `.env`)

Dopo aver modificato il codice: `podman-compose up -d --build --force-recreate collector`
(senza `--force-recreate` podman-compose può lasciare in esecuzione il container con l'immagine vecchia).

Con Docker al posto di Podman: `host.containers.internal` non esiste di default, va aggiunto al servizio
`collector` con `extra_hosts: ["host.containers.internal:host-gateway"]`.

## Configurazione

### Dall'interfaccia (Impostazioni, icona ingranaggio)

- **Server Home Assistant**: IP o hostname, con porta facoltativa (predefinita 8123). Esempi: `192.168.1.50`,
  `homeassistant.local:8123`, `https://ha.example.com`. Vuoto = valore predefinito (`HA_URL`).
- **Token di accesso**: token a lunga durata di HA (campo password con Mostra/Nascondi). Vuoto = invariato;
  la casella "torna al token del file .env" cancella quello salvato.
- **Prova connessione**: verifica server e token senza salvare. **Salva e aggiorna** salva e lancia subito un sync.
- **Sensori auto**: elenco dei sensori dell'auto in uso e della loro origine (riconosciuto / da `.env`).

I valori sono salvati in `data/app/settings.json` (permessi `600`) e hanno la precedenza su `HA_URL`/`HA_TOKEN`.
Il token non viene mai rimandato al browser né incluso nei backup.

### File `.env`

| variabile              | default                        | significato                                                       |
|------------------------|--------------------------------|-------------------------------------------------------------------|
| `HA_TOKEN`             | –                              | token HA (facoltativo se impostato dall'interfaccia)              |
| `HA_URL`               | (dal compose)                  | server HA predefinito                                             |
| `INFLUX_ORG` / `INFLUX_BUCKET` | `casa` / `energia`     | organizzazione e bucket InfluxDB                                  |
| `INFLUX_PASSWORD` / `INFLUX_TOKEN` | –                  | credenziali InfluxDB (create al primo avvio)                       |
| `INTERVAL_SECONDS`     | `900`                          | intervallo del sync automatico                                    |
| `LOOKBACK_HOURS`       | `6`                            | ore già importate da rileggere a ogni sync                         |
| `EXTRA_STATISTIC_IDS`  | –                              | altri sensori fuori dalla dashboard Energia, separati da virgola  |
| `FORCE_BACKFILL`       | `false`                        | `true` = reimporta tutto lo storico a ogni sync                    |
| `BACKUP_KEEP`          | `10`                           | backup conservati sul server                                      |
| `WEB_PORT`             | `8090`                         | porta della dashboard pubblicata sull'host                        |
| `AUTO_*`               | –                              | sensori dell'auto, solo per forzarli (vedi [Auto](#auto))          |

## Dashboard

Periodi: giorno, settimana, mese, anno, tutto, intervallo personalizzato; dettaglio orario/giornaliero/
settimanale/mensile/annuale. Pulsante "Aggiorna da Home Assistant" per un sync immediato.

**Casa**: KPI con confronto sul periodo precedente, bilancio energetico, flussi (Sankey), consumi per
dispositivo, produzione per stringa, autosufficienza/autoconsumo, profilo orario medio, batteria, mappa oraria,
confronto anno su anno, tabella con export CSV, export Excel del periodo.
Il consumo casa è calcolato: solare + prelievo + scarica batteria − immissione − carica batteria.

### Auto

Sezione **Auto** (`http://<host>:8090/#auto`): energia caricata a casa/fuori, quota solare delle ricariche di
casa, livello batteria, km, consumo medio, orari di ricarica e tabella delle sessioni (ore consecutive con
ricarica, separate da più di 2 ore di pausa). Una sessione è "a casa" se la presa di casa ha erogato almeno il
30% dell'energia entrata nell'auto; la quota solare è ripartita ora per ora in proporzione al consumo di casa.

I sensori sono **riconosciuti in automatico** a ogni sync, senza configurazione:

| ruolo                  | come viene trovato                                                                     |
|------------------------|----------------------------------------------------------------------------------------|
| energia ricaricata     | Tesla Fleet / Teslemetry / Tessie: `charge_state_charge_energy_added`                   |
| contachilometri        | `vehicle_state_odometer`                                                                |
| livello batteria       | `charge_state_battery_level` (o `charge_state_usable_battery_level`)                    |
| potenza di ricarica    | `charge_state_charger_power`                                                            |
| presa/wallbox di casa  | Tesla Wall Connector, altrimenti un dispositivo della dashboard Energia con un nome tipo "auto", "wallbox", "ricarica", "charger" |

Le chiavi sono le `translation_key` interne delle entità: non cambiano se un'entità viene rinominata o se HA è in
un'altra lingua. Per Tesla Custom (HACS) si usa il suffisso dell'entity_id (`_energy_added`, `_odometer`, …).
Con più auto, tutti i sensori vengono presi dalla stessa auto. Per auto di altre marche, o per forzare un
sensore, compila nel `.env` `AUTO_ENERGY_ID`, `AUTO_ODOMETER_ID`, `AUTO_BATTERY_ID`, `AUTO_POWER_ID`,
`AUTO_HOME_ID`: hanno la precedenza sul riconoscimento automatico.

## API

| metodo e percorso                          | cosa fa                                                              |
|--------------------------------------------|----------------------------------------------------------------------|
| `GET /api/meta`                            | sensori, prezzo, periodo dei dati, stato del sync                    |
| `GET /api/data?start=&end=&gran=`          | dati casa del periodo                                                |
| `GET /api/auto?start=&end=&gran=`          | dati auto e sessioni di ricarica                                     |
| `GET /api/monthly`                         | totali mensili (confronto anno su anno)                              |
| `GET /api/export.xlsx?start=&end=&gran=`   | Excel: riepilogo, casa, dispositivi, solare, auto, sessioni, dati orari |
| `POST /api/sync`                           | sync immediato da Home Assistant                                     |
| `GET /api/settings`                        | server HA in uso e se è impostato un token (il token non viene restituito) |
| `PUT /api/settings`                        | `{"ha_url", "ha_token", "reset_token"}`                              |
| `POST /api/settings/test`                  | prova connessione con server/token indicati, senza salvare           |
| `POST /api/backup`, `GET /api/backups`, `GET /api/backups/<nome>` | crea, elenca, scarica backup                  |
| `POST /api/restore`, `POST /api/restore/<nome>` | ripristino completo da file caricato o da backup sul server     |
| `POST /api/wipe`                           | cancellazione completa (dopo backup di sicurezza)                    |

## Modello dati

Measurement `energia_oraria`, un punto per sensore per ora (timestamp = inizio ora, UTC):

| tag         | valori                                                                               |
|-------------|--------------------------------------------------------------------------------------|
| `entity_id` | statistic_id di HA                                                                   |
| `categoria` | `solare`, `rete_prelievo`, `rete_immissione`, `batteria_carica`, `batteria_scarica`, `dispositivo`, `extra`, `auto_ricarica`, `auto_km`, `auto_casa` |
| `nome`      | nome del dispositivo (dalla dashboard Energia)                                       |

| field    | significato                                      |
|----------|--------------------------------------------------|
| `change` | kWh nell'ora (il valore che usa la dashboard)    |
| `sum`    | totale cumulativo calcolato da HA (kWh)          |
| `state`  | ultimo valore del contatore nell'ora (kWh)       |

Measurement `misure_orarie`: sensori di misura (categorie `auto_batteria`, `auto_potenza`) con field
`mean`/`min`/`max` per ora.

Esempio Flux: produzione solare giornaliera degli ultimi 30 giorni

```flux
import "timezone"

from(bucket: "energia")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "energia_oraria" and r.categoria == "solare" and r._field == "change")
  |> group()
  |> aggregateWindow(every: 1d, fn: sum, timeSrc: "_start", location: timezone.location(name: "Europe/Rome"))
```

## Note

- I sensori vengono letti dalla config Energia a ogni ciclo: un dispositivo aggiunto in HA viene
  raccolto da solo, con tutto il suo storico. Le scritture sono idempotenti, rileggere ore già importate
  non crea duplicati.
- I sensori in `EXTRA_STATISTIC_IDS` devono avere statistiche a lungo termine (`state_class` impostato).

## Backup, ripristino e cancellazione

Pulsante 🛢 (in alto a destra) → finestra "Backup e ripristino".

- **Backup**: archivio `energia_backup_AAAAMMGG_HHMMSS.tar.gz` con `manifest.json`, `meta.json` e
  `data/<measurement>.lp` (tutti i punti in InfluxDB line protocol). Viene scaricato e conservato anche in
  `data/app/backups/` (ultimi `BACKUP_KEEP`). `.env` e `settings.json` (token, password) NON sono inclusi.
- **Ripristino completo**: da file caricato o da un backup sul server. L'archivio viene validato prima di toccare
  il database; poi viene creato un backup di sicurezza `pre-restore_*.tar.gz`, cancellati i dati attuali e
  reimportato tutto. Il sync con HA è sospeso durante l'operazione e al ciclo successivo recupera le ore mancanti.
- **Cancella tutti i dati**: chiede conferma (bisogna scrivere `CANCELLA`), crea un backup di sicurezza
  `pre-wipe_*.tar.gz`, poi elimina tutti i dati in InfluxDB e i nomi dei sensori; backup e impostazioni restano.
  Al sync successivo viene reimportato lo storico ancora disponibile in Home Assistant.

Da riga di comando:

```bash
curl -X POST http://localhost:8090/api/backup                                   # crea backup
curl -O http://localhost:8090/api/backups/<nome>.tar.gz                         # scarica
curl -X POST -H 'Content-Type: application/gzip' --data-binary @<file>.tar.gz \
     http://localhost:8090/api/restore                                          # ripristino completo
curl -X POST http://localhost:8090/api/wipe                                     # cancella tutto
```

## Versioni

- **v1.3**: token di Home Assistant configurabile dall'interfaccia; riconoscimento automatico dei sensori
  dell'auto e della presa/wallbox di casa; cancellazione completa dei dati con conferma e backup di sicurezza.
- **v1.1**: dashboard pubblicata esplicitamente sulla porta 8090 (rete bridge), supporto Podman; server
  Home Assistant configurabile dall'interfaccia con prova di connessione; JS/CSS sempre aggiornati nel browser.
- **v1.0**: prima versione (commit iniziale, senza tag).
