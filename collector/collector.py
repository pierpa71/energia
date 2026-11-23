"""Collector: statistiche orarie energia da Home Assistant -> InfluxDB 2.

Legge la configurazione della dashboard Energia di HA, scarica le statistiche
orarie (long-term statistics) dei sensori coinvolti e le scrive in InfluxDB.
Al primo avvio importa tutto lo storico, poi lavora in modo incrementale.
Le scritture sono idempotenti (stesso measurement+tag+timestamp = sovrascrittura),
quindi rileggere qualche ora già importata non crea duplicati.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import websockets
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

HA_URL = os.environ.get("HA_URL", "http://127.0.0.1:8123").rstrip("/")
HA_TOKEN = os.environ["HA_TOKEN"]
INFLUX_URL = os.environ.get("INFLUX_URL", "http://127.0.0.1:8086")
INFLUX_TOKEN = os.environ["INFLUX_TOKEN"]
INFLUX_ORG = os.environ.get("INFLUX_ORG", "casa")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "energia")
MEASUREMENT = os.environ.get("MEASUREMENT", "energia_oraria")
MEAS_MEAN = os.environ.get("MEASUREMENT_MEAN", "misure_orarie")  # sensori di misura (mean/min/max)

BACKFILL_START = os.environ.get("BACKFILL_START", "2020-01-01")  # HA restituisce solo ciò che esiste
FORCE_BACKFILL = os.environ.get("FORCE_BACKFILL", "false").lower() == "true"
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "6"))  # ore già importate da rileggere
CHUNK_DAYS = int(os.environ.get("CHUNK_DAYS", "30"))
INTERVAL_SECONDS = int(os.environ.get("INTERVAL_SECONDS", "900"))
RUN_ONCE = os.environ.get("RUN_ONCE", "false").lower() == "true"
# statistic_id extra non presenti nella dashboard Energia, separati da virgola
META_PATH = os.environ.get("META_PATH", "/data/meta.json")
EXTRA_IDS = [s.strip() for s in os.environ.get("EXTRA_STATISTIC_IDS", "").split(",") if s.strip()]

# Auto elettrica: sensori opzionali (lasciare vuoto per disattivare)
AUTO_SUM = {  # contatori: energia caricata nell'auto (casa + fuori), km
    "auto_ricarica": os.environ.get("AUTO_ENERGY_ID", ""),
    "auto_km": os.environ.get("AUTO_ODOMETER_ID", ""),
}
AUTO_MEAN = {  # misure: % batteria, potenza di ricarica
    "auto_batteria": os.environ.get("AUTO_BATTERY_ID", ""),
    "auto_potenza": os.environ.get("AUTO_POWER_ID", ""),
}
AUTO_HOME_ID = os.environ.get("AUTO_HOME_ID", "")  # contatore della presa/wallbox di casa

log = logging.getLogger("energia")


class HAClient:
    """Client minimale per l'API WebSocket di Home Assistant."""

    def __init__(self, url: str, token: str):
        self.ws_url = url.replace("http", "ws", 1) + "/api/websocket"
        self.token = token
        self._id = 0
        self.ws = None

    async def __aenter__(self):
        self.ws = await websockets.connect(self.ws_url, max_size=None, open_timeout=30)
        msg = json.loads(await self.ws.recv())
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"Risposta inattesa da HA: {msg}")
        await self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        msg = json.loads(await self.ws.recv())
        if msg.get("type") != "auth_ok":
            raise RuntimeError(f"Autenticazione HA fallita: {msg.get('message', msg)}")
        log.info("Connesso a Home Assistant %s", msg.get("ha_version"))
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def call(self, msg_type: str, **payload):
        self._id += 1
        await self.ws.send(json.dumps({"id": self._id, "type": msg_type, **payload}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self._id and msg.get("type") == "result":
                if not msg.get("success"):
                    raise RuntimeError(f"{msg_type} fallito: {msg.get('error')}")
                return msg["result"]


def sensors_from_energy_prefs(prefs: dict) -> dict[str, dict]:
    """Mappa statistic_id -> {categoria, nome} a partire dalla config Energia."""
    out: dict[str, dict] = {}

    def add(stat_id, categoria, nome=None, kind="sum"):
        if stat_id:
            out.setdefault(stat_id, {"categoria": categoria, "nome": nome or stat_id, "kind": kind})

    for src in prefs.get("energy_sources", []):
        t = src.get("type")
        if t == "grid":
            # formato attuale (campi piatti) e formato storico (liste flow_from/flow_to)
            add(src.get("stat_energy_from"), "rete_prelievo")
            add(src.get("stat_energy_to"), "rete_immissione")
            for f in src.get("flow_from", []):
                add(f.get("stat_energy_from"), "rete_prelievo")
            for f in src.get("flow_to", []):
                add(f.get("stat_energy_to"), "rete_immissione")
        elif t == "solar":
            add(src.get("stat_energy_from"), "solare")
        elif t == "battery":
            add(src.get("stat_energy_from"), "batteria_scarica")
            add(src.get("stat_energy_to"), "batteria_carica")
        elif t in ("gas", "water"):
            add(src.get("stat_energy_from"), t)

    for dev in prefs.get("device_consumption", []):
        add(dev.get("stat_consumption"), "dispositivo", dev.get("name"))

    for stat_id in EXTRA_IDS:
        add(stat_id, "extra")
    for cat, stat_id in AUTO_SUM.items():
        add(stat_id, cat)
    for cat, stat_id in AUTO_MEAN.items():
        add(stat_id, cat, kind="mean")
    return out


def to_datetime(value) -> datetime:
    """HA restituisce 'start' come epoch in millisecondi (versioni recenti) o ISO string."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def last_imported(query_api) -> dict[str, datetime]:
    """Ultimo timestamp importato per ciascun sensore."""
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: 0)
  |> filter(fn: (r) => (r._measurement == "{MEASUREMENT}" and r._field == "sum")
                    or (r._measurement == "{MEAS_MEAN}" and r._field == "mean"))
  |> last()
'''
    out: dict[str, datetime] = {}
    for table in query_api.query(flux, org=INFLUX_ORG):
        for rec in table.records:
            eid = rec.values["entity_id"]
            out[eid] = max(out.get(eid, rec.get_time()), rec.get_time())
    return out


def save_meta(prefs: dict, sensors: dict, states: list) -> None:
    """Salva nomi leggibili e prezzo energia per l'interfaccia web."""
    names = {s["entity_id"]: s.get("attributes", {}).get("friendly_name") for s in states}
    price = next((s.get("number_energy_price") for s in prefs.get("energy_sources", [])
                  if s.get("type") == "grid" and s.get("number_energy_price") is not None), None)
    meta = {
        "sensors": {
            sid: {**m, "label": m["nome"] if m["nome"] != sid else (names.get(sid) or sid)}
            for sid, m in sensors.items()
        },
        "price": price,
        "auto": {"home": AUTO_HOME_ID or None},
    }
    try:
        os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
        tmp = META_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        os.replace(tmp, META_PATH)
    except OSError as e:
        log.warning("Impossibile salvare %s: %s", META_PATH, e)


async def fetch_and_write(ha, write_api, sensors: dict, ids: list[str], start: datetime, now: datetime) -> int:
    total = 0
    chunk_start = start
    while chunk_start < now:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), now)
        result = await ha.call(
            "recorder/statistics_during_period",
            start_time=chunk_start.isoformat(),
            end_time=chunk_end.isoformat(),
            statistic_ids=ids,
            period="hour",
            types=["change", "sum", "state", "mean", "min", "max"],
            units={"energy": "kWh", "distance": "km", "power": "kW"},
        )
        points = []
        for stat_id, rows in result.items():
            meta = sensors.get(stat_id, {"categoria": "extra", "nome": stat_id, "kind": "sum"})
            keys, meas = (("mean", "min", "max"), MEAS_MEAN) if meta.get("kind") == "mean" else (("change", "sum", "state"), MEASUREMENT)
            for row in rows:
                fields = {k: float(row[k]) for k in keys if row.get(k) is not None}
                if not fields:
                    continue
                p = (
                    Point(meas)
                    .tag("entity_id", stat_id)
                    .tag("categoria", meta["categoria"])
                    .tag("nome", meta["nome"])
                    .time(to_datetime(row["start"]), WritePrecision.S)
                )
                for k, v in fields.items():
                    p = p.field(k, v)
                points.append(p)
        if points:
            write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points)
        total += len(points)
        if points or chunk_end == now:
            log.info("  %s -> %s: %d punti", chunk_start.date(), chunk_end.date(), len(points))
        chunk_start = chunk_end
    return total


async def sync_once(influx: InfluxDBClient) -> int:
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    async with HAClient(HA_URL, HA_TOKEN) as ha:
        prefs = await ha.call("energy/get_prefs")
        sensors = sensors_from_energy_prefs(prefs)
        if not sensors:
            log.warning("Nessun sensore trovato nella dashboard Energia")
            return 0
        log.info("Sensori monitorati: %d", len(sensors))
        save_meta(prefs, sensors, await ha.call("get_states"))

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        lasts = {} if FORCE_BACKFILL else last_imported(influx.query_api())
        backfill_start = datetime.fromisoformat(BACKFILL_START).replace(tzinfo=timezone.utc)
        # sensori già presenti: incrementale dall'ultimo dato; sensori nuovi: tutto lo storico
        known = [sid for sid in sensors if sid in lasts]
        new = [sid for sid in sensors if sid not in lasts]
        groups = []
        if known:
            # un sensore fermo da tempo non deve far rileggere ogni volta settimane di dati
            newest = max(lasts[sid] for sid in known)
            oldest = max(min(lasts[sid] for sid in known), newest - timedelta(days=3))
            groups.append((oldest - timedelta(hours=LOOKBACK_HOURS), known))
        if new:
            groups.append((backfill_start, new))

        total = 0
        for start, ids in groups:
            log.info("Sync da %s per %d sensori%s", start.isoformat(), len(ids), " (nuovi: backfill)" if start == backfill_start else "")
            total += await fetch_and_write(ha, write_api, sensors, ids, start, now)

        log.info("Sync completato: %d punti scritti", total)
        return total


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    with InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG, timeout=60_000) as influx:
        for _ in range(60):
            if influx.ping():
                break
            log.info("In attesa che InfluxDB sia pronto...")
            await asyncio.sleep(5)
        while True:
            delay = INTERVAL_SECONDS
            try:
                await sync_once(influx)
            except Exception:
                log.exception("Errore durante il sync")
                if RUN_ONCE:
                    sys.exit(1)
                delay = min(60, INTERVAL_SECONDS)  # riprova presto dopo un errore
            if RUN_ONCE:
                return
            await asyncio.sleep(delay)


if __name__ == "__main__":
    asyncio.run(main())
