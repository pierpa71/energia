# Energia: Home Assistant → InfluxDB

Raccoglie le **statistiche orarie** (kWh) dei sensori configurati nella dashboard Energia
di Home Assistant e le salva in InfluxDB 2.

## Avvio

1. `cp .env.example .env`, poi in HA: *Profilo → Sicurezza → Token di accesso a lunga durata* → crea un token e
   incollalo in `.env` al posto di `INSERISCI_QUI_IL_TOKEN_HA`. Genera `INFLUX_PASSWORD` e `INFLUX_TOKEN`
   (es. `openssl rand -hex 32`) e, se hai un'auto elettrica, compila le variabili `AUTO_*`.
2. `docker compose up -d`
3. `podman logs -f energia-collector`: al primo avvio importa tutto lo storico disponibile,
   poi ogni 15 minuti aggiunge le ore nuove.

- **Dashboard**: http://<raspberry>:8090 (pulsante "Aggiorna da Home Assistant" per un sync immediato)
- InfluxDB UI: http://<raspberry>:8086 (utente `admin`, password in `.env`).

## Dashboard

`collector/app.py` (FastAPI) serve l'interfaccia in `collector/static/` e fa anche il sync periodico.
Periodi: giorno, settimana, mese, anno, tutto, intervallo personalizzato; dettaglio orario/giornaliero/
settimanale/mensile/annuale. Viste: KPI con confronto sul periodo precedente, bilancio energetico,
flussi (Sankey), consumi per dispositivo, produzione per stringa, autosufficienza/autoconsumo,
profilo orario medio, batteria, mappa oraria, confronto anno su anno, tabella con export CSV.

Sezione **Auto** (`http://<raspberry>:8090/#auto`): energia caricata a casa/fuori, quota solare delle
ricariche di casa, livello batteria, km, consumo medio, orari di ricarica e tabella delle sessioni
(ricostruite dai dati orari: ore consecutive con ricarica, separate da più di 2 ore di pausa).
Sensori configurati in `.env` (`AUTO_ENERGY_ID`, `AUTO_ODOMETER_ID`, `AUTO_BATTERY_ID`, `AUTO_POWER_ID`,
`AUTO_HOME_ID`); i sensori di misura (% batteria, potenza) finiscono nel measurement `misure_orarie`
(field `mean`/`min`/`max`). Una sessione è "a casa" se la presa di casa ha erogato almeno il 30%
dell'energia entrata nell'auto; la quota solare è ripartita ora per ora in proporzione al consumo di casa.

API: `GET /api/meta`, `GET /api/data?start=&end=&gran=`, `GET /api/auto?...`, `GET /api/monthly`,
`GET /api/export.xlsx?start=&end=&gran=` (Excel: riepilogo, casa, dispositivi, solare, auto, sessioni, dati orari), `POST /api/sync`.

Il consumo casa è calcolato: solare + prelievo + scarica batteria − immissione − carica batteria.

## Modello dati

Measurement `energia_oraria`, un punto per sensore per ora (timestamp = inizio ora, UTC):

| tag         | valori                                                                               |
|-------------|--------------------------------------------------------------------------------------|
| `entity_id` | statistic_id di HA                                                                   |
| `categoria` | `solare`, `rete_prelievo`, `rete_immissione`, `batteria_carica`, `batteria_scarica`, `dispositivo`, `extra` |
| `nome`      | nome del dispositivo (dalla dashboard Energia)                                       |

| field    | significato                                      |
|----------|--------------------------------------------------|
| `change` | kWh nell'ora (il valore che usa la dashboard)    |
| `sum`    | totale cumulativo calcolato da HA (kWh)          |
| `state`  | ultimo valore del contatore nell'ora (kWh)       |

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
  raccolto da solo, con tutto il suo storico. `FORCE_BACKFILL=true` reimporta tutto (le scritture sono
  idempotenti, non crea duplicati).
- Altri sensori fuori dalla dashboard: `EXTRA_STATISTIC_IDS=sensor.a,sensor.b` in `.env`
  (devono avere statistiche a lungo termine, cioè `state_class` impostato).

## Backup e ripristino

Pulsante 🛢 (in alto a destra) → finestra "Backup e ripristino".

- **Backup**: archivio `energia_backup_AAAAMMGG_HHMMSS.tar.gz` con `manifest.json`, `meta.json` e
  `data/<measurement>.lp` (tutti i punti in InfluxDB line protocol). Viene scaricato e conservato anche in
  `data/app/backups/` (ultimi 10, `BACKUP_KEEP` nel `.env`). Il file `.env` (token, password) NON è incluso.
- **Ripristino completo**: da file caricato o da un backup sul server. L'archivio viene validato prima di toccare
  il database; poi viene creato un backup di sicurezza `pre-restore_*.tar.gz`, cancellati i dati attuali e
  reimportato tutto. Il sync con HA è sospeso durante l'operazione e al ciclo successivo recupera le ore mancanti.

Da riga di comando:

```bash
curl -X POST http://localhost:8090/api/backup                                   # crea backup
curl -O http://localhost:8090/api/backups/<nome>.tar.gz                         # scarica
curl -X POST -H 'Content-Type: application/gzip' --data-binary @<file>.tar.gz \
     http://localhost:8090/api/restore                                          # ripristino completo
```
