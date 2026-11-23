"""Web app: API sui dati energia in InfluxDB + sync da Home Assistant + UI statica."""

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import tempfile

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from influxdb_client import Dialect, InfluxDBClient

import collector as c
import backup as bk
import export_xlsx

TZ_NAME = os.environ.get("TZ_NAME", "Europe/Rome")
TZ = ZoneInfo(TZ_NAME)
STATIC = os.path.join(os.path.dirname(__file__), "static")
MAX_HEATMAP_DAYS = 400
MAX_HOUR_BUCKETS_DAYS = 62

log = logging.getLogger("energia.app")
influx = InfluxDBClient(url=c.INFLUX_URL, token=c.INFLUX_TOKEN, org=c.INFLUX_ORG, timeout=120_000)
query_api = influx.query_api()
CSV_DIALECT = Dialect(header=True, annotations=[], date_time_format="RFC3339")
BACKUP = bk.Backup(influx, CSV_DIALECT, c.INFLUX_ORG, c.INFLUX_BUCKET, [c.MEASUREMENT, c.MEAS_MEAN], c.META_PATH,
                   os.environ.get("BACKUP_DIR", "/data/backups"), int(os.environ.get("BACKUP_KEEP", "10")))
MAX_UPLOAD = 1024**3
sync_lock = asyncio.Lock()
_cache: dict = {}
CACHE_MAX = 64


def cached(fn):
    """Cache delle risposte API, svuotata a ogni sync (i dati cambiano solo lì)."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        key = (fn.__name__, args, tuple(sorted(kwargs.items())))
        if key not in _cache:
            if len(_cache) >= CACHE_MAX:
                _cache.pop(next(iter(_cache)))
            _cache[key] = fn(*args, **kwargs)
        return _cache[key]
    return wrapper
sync_state = {"running": False, "last_sync": None, "last_points": None, "last_error": None}


# ---------------------------------------------------------------- sync

async def run_sync() -> None:
    async with sync_lock:
        sync_state["running"] = True
        try:
            pts = await c.sync_once(influx)
            sync_state.update(last_sync=datetime.now(timezone.utc).isoformat(), last_points=pts, last_error=None)
            _cache.clear()
        except Exception as e:
            log.exception("Errore durante il sync")
            sync_state["last_error"] = str(e)
            raise
        finally:
            sync_state["running"] = False


async def sync_loop() -> None:
    for _ in range(60):
        if influx.ping():
            break
        log.info("In attesa che InfluxDB sia pronto...")
        await asyncio.sleep(5)
    while True:
        delay = c.INTERVAL_SECONDS
        try:
            await run_sync()
        except Exception:
            delay = min(60, c.INTERVAL_SECONDS)
        await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(sync_loop())
    yield
    task.cancel()


app = FastAPI(title="Energia", lifespan=lifespan)


# ---------------------------------------------------------------- helpers

def flux_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_range(start: date, end: date) -> tuple[datetime, datetime]:
    """[start 00:00, end+1 00:00) in ora locale."""
    return (datetime.combine(start, time.min, TZ), datetime.combine(end + timedelta(days=1), time.min, TZ))


def bucket_key(dt_local: datetime, gran: str) -> str:
    if gran == "hour":
        return dt_local.strftime("%Y-%m-%dT%H:00")
    if gran == "day":
        return dt_local.strftime("%Y-%m-%d")
    if gran == "week":
        return (dt_local.date() - timedelta(days=dt_local.weekday())).isoformat()
    if gran == "month":
        return dt_local.strftime("%Y-%m")
    return dt_local.strftime("%Y")


def all_buckets(s: datetime, e: datetime, gran: str) -> list[str]:
    keys, seen = [], set()
    step = timedelta(hours=1) if gran == "hour" else timedelta(days=1)
    t = s.astimezone(timezone.utc)
    end = e.astimezone(timezone.utc)
    while t < end:
        k = bucket_key(t.astimezone(TZ), gran)
        if k not in seen:
            seen.add(k)
            keys.append(k)
        t += step
    return keys


def base_query(s: datetime, e: datetime) -> str:
    return f'''import "timezone"
from(bucket: "{c.INFLUX_BUCKET}")
  |> range(start: {flux_time(s)}, stop: {flux_time(e)})
  |> filter(fn: (r) => r._measurement == "{c.MEASUREMENT}" and r._field == "change")'''


def run_query(flux: str):
    return [rec for table in query_api.query(flux, org=c.INFLUX_ORG) for rec in table.records]


def run_csv(flux: str):
    """Esegue la query leggendo il CSV grezzo (molto più veloce dei FluxRecord su ARM).
    Restituisce dizionari colonna -> stringa."""
    header = None
    for row in query_api.query_csv(flux, org=c.INFLUX_ORG, dialect=CSV_DIALECT):
        if not row or len(row) < 3:
            continue
        if "_time" in row:
            header = row
            continue
        if header:
            yield dict(zip(header, row))


def parse_time(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def run_rows(flux: str, tag: str):
    """Tuple (tag, datetime UTC, valore)."""
    return [(r[tag], parse_time(r["_time"]), float(r["_value"])) for r in run_csv(flux) if r.get("_value")]


def windowed(s: datetime, e: datetime, group_col: str, every: str):
    flux = base_query(s, e) + f'''
  |> group(columns: ["{group_col}"])
  |> aggregateWindow(every: {every}, fn: sum, createEmpty: false, timeSrc: "_start",
                     location: timezone.location(name: "{TZ_NAME}"))
  |> keep(columns: ["_time", "_value", "{group_col}"])'''
    return run_rows(flux, group_col)


def totals(s: datetime, e: datetime) -> dict[str, float]:
    flux = base_query(s, e) + '\n  |> group(columns: ["entity_id"])\n  |> sum()'
    return {r.values["entity_id"]: r.get_value() for r in run_query(flux)}


def load_meta() -> dict:
    try:
        with open(c.META_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"sensors": {}, "price": None}


# ---------------------------------------------------------------- API

@app.get("/api/meta")
def api_meta():
    meta = load_meta()
    # range|>filter|>first/last/sum vengono eseguiti direttamente sullo storage (veloci)
    src = f'''from(bucket: "{c.INFLUX_BUCKET}")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "{c.MEASUREMENT}" and r._field == "change")'''
    firsts = [r.get_time() for r in run_query(src + "\n  |> first()")]
    lasts = [r.get_time() for r in run_query(src + "\n  |> last()")]
    alltime: dict[str, float] = {}
    for r in run_query(src + "\n  |> sum()"):
        alltime[r.values["entity_id"]] = alltime.get(r.values["entity_id"], 0.0) + (r.get_value() or 0.0)
    for sid, m in meta["sensors"].items():
        m["total_kwh"] = alltime.get(sid, 0.0)
    to_iso = lambda t: t.astimezone(TZ).isoformat() if t else None
    return {**meta, "first": to_iso(min(firsts, default=None)), "last": to_iso(max(lasts, default=None)),
            "sync": sync_state, "tz": TZ_NAME}


@app.post("/api/sync")
async def api_sync():
    try:
        await run_sync()
    except Exception as e:
        raise HTTPException(502, f"Sync fallito: {e}")
    return sync_state


# ---------------------------------------------------------------- impostazioni

class SettingsIn(BaseModel):
    ha_url: str = ""
    ha_token: str = ""          # vuoto = lascia invariato il token salvato
    reset_token: bool = False   # true = cancella il token salvato e torna a HA_TOKEN


def settings_out() -> dict:
    """Il token non viene mai restituito: solo se è impostato e da dove arriva."""
    s = c.load_settings()
    return {"ha_url": c.ha_url(), "ha_url_custom": s.get("ha_url") or "", "ha_url_default": c.HA_URL,
            "ha_token_custom": bool(s.get("ha_token")), "ha_token_default": bool(c.HA_TOKEN)}


def checked(fn, value: str) -> str:
    try:
        return fn(value)
    except ValueError as e:
        raise HTTPException(400, str(e))


def checked_url(value: str) -> str:
    return checked(c.normalize_ha_url, value)


@app.get("/api/settings")
def api_settings():
    return settings_out()


@app.put("/api/settings")
def api_settings_save(body: SettingsIn):
    url = checked_url(body.ha_url)
    token = checked(c.normalize_ha_token, body.ha_token)
    settings = c.load_settings()
    if url:
        settings["ha_url"] = url
    else:
        settings.pop("ha_url", None)
    if token:
        settings["ha_token"] = token
    elif body.reset_token:
        settings.pop("ha_token", None)
    try:
        c.save_settings(settings)
    except OSError as e:
        raise HTTPException(500, f"Impossibile salvare le impostazioni: {e}")
    log.info("Server Home Assistant impostato a %s (token %s)", c.ha_url(),
             "da interfaccia" if settings.get("ha_token") else "da HA_TOKEN")
    return settings_out()


@app.post("/api/settings/test")
async def api_settings_test(body: SettingsIn):
    """Prova connessione e autenticazione verso un server HA senza salvarlo."""
    url = checked_url(body.ha_url) or c.HA_URL
    token = checked(c.normalize_ha_token, body.ha_token)
    if not token:
        token = c.HA_TOKEN if body.reset_token else (c.load_settings().get("ha_token") or c.HA_TOKEN)
    if not token:
        raise HTTPException(400, "Nessun token configurato: inseriscine uno")
    try:
        async with asyncio.timeout(10):
            async with c.HAClient(url, token) as ha:
                version = ha.version
    except TimeoutError:
        raise HTTPException(502, f"{url}: nessuna risposta entro 10 secondi")
    except OSError as e:
        raise HTTPException(502, f"{url}: impossibile connettersi ({e.strerror or e})")
    except Exception as e:
        raise HTTPException(502, f"{url}: {e}")
    return {"ha_url": url, "version": version}


@app.get("/api/data")
@cached
def api_data(
    start: date,
    end: date,
    gran: str = Query("day", pattern="^(hour|day|week|month|year)$"),
    prev_start: date | None = None,
    prev_end: date | None = None,
):
    if end < start:
        raise HTTPException(400, "end < start")
    days = (end - start).days + 1
    if gran == "hour" and days > MAX_HOUR_BUCKETS_DAYS:
        gran = "day"
    s, e = local_range(start, end)
    every = {"hour": "1h", "day": "1d", "week": "1d", "month": "1mo", "year": "1mo"}[gran]

    buckets = all_buckets(s, e, gran)
    index = {k: i for i, k in enumerate(buckets)}
    series: dict[str, list[float]] = {}
    for eid, t, v in windowed(s, e, "entity_id", every):
        k = bucket_key(t.astimezone(TZ), gran)
        if k in index:
            series.setdefault(eid, [0.0] * len(buckets))[index[k]] += v

    hourly = None
    if days <= MAX_HEATMAP_DAYS:
        dates = [(start + timedelta(days=i)).isoformat() for i in range(days)]
        didx = {d: i for i, d in enumerate(dates)}
        cats: dict[str, list[list[float]]] = {}
        # i dati sono già orari: niente aggregateWindow, si somma per categoria in Python
        raw = base_query(s, e) + '''
  |> filter(fn: (r) => r.categoria != "dispositivo")
  |> keep(columns: ["_time", "_value", "categoria"])'''
        for cat, t, v in run_rows(raw, "categoria"):
            t = t.astimezone(TZ)
            d = didx.get(t.date().isoformat())
            if d is None:
                continue
            grid = cats.get(cat)
            if grid is None:
                grid = cats[cat] = [[0.0] * 24 for _ in dates]
            grid[d][t.hour] += v
        for grid in cats.values():
            for row in grid:
                row[:] = [round(x, 4) for x in row]
        hourly = {"dates": dates, "cats": cats}

    prev = None
    if prev_start and prev_end and prev_end >= prev_start:
        prev = totals(*local_range(prev_start, prev_end))

    return {"gran": gran, "buckets": buckets, "series": series, "hourly": hourly, "prev_totals": prev}


@app.get("/api/monthly")
@cached
def api_monthly():
    """Totali mensili per categoria su tutto lo storico (confronto anno su anno)."""
    s = datetime(2000, 1, 1, tzinfo=TZ)
    e = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    out: dict[str, dict[str, float]] = {}
    for cat_name, t, v in windowed(s, e, "categoria", "1mo"):
        k = t.astimezone(TZ).strftime("%Y-%m")
        cat = out.setdefault(cat_name, {})
        cat[k] = cat.get(k, 0.0) + v
    return out


# ---------------------------------------------------------------- auto elettrica

HOUSE_CATS = ["solare", "rete_prelievo", "rete_immissione", "batteria_scarica", "batteria_carica"]
SESSION_GAP_H = 2  # ore senza ricarica che separano due sessioni
MIN_SESSION_KWH = 0.5


def flux_any(col: str, values) -> str:
    """r.col == v1 or r.col == v2 ... (a differenza di contains() viene eseguito sullo storage)."""
    vals = [v for v in values if v]
    return " or ".join(f"r.{col} == {json.dumps(v)}" for v in vals) or "false"


def auto_hours(meta: dict, start: date, end: date) -> tuple[dict, list]:
    """Dati orari dell'auto e sessioni di ricarica che iniziano in [start, end]."""
    ids = {m["categoria"]: sid for sid, m in meta["sensors"].items() if m["categoria"].startswith("auto_")}
    home = (meta.get("auto") or {}).get("home")
    s, e = local_range(start, end)
    qs = s - timedelta(days=1)  # margine: sessioni iniziate prima ma ancora in corso
    price = meta.get("price") or 0.0

    H: dict[int, dict] = {}
    def hour(t: datetime) -> dict:
        k = int(t.timestamp()) // 3600
        return H.setdefault(k, {"added": 0.0, "wall": 0.0, "km": 0.0, "house": dict.fromkeys(HOUSE_CATS, 0.0)})

    flux = f'''from(bucket: "{c.INFLUX_BUCKET}")
  |> range(start: {flux_time(qs)}, stop: {flux_time(e)})
  |> filter(fn: (r) => r._measurement == "{c.MEASUREMENT}" and r._field == "change")
  |> filter(fn: (r) => {flux_any("entity_id", [ids.get("auto_ricarica"), ids.get("auto_km"), home])})
  |> keep(columns: ["_time", "_value", "entity_id"])'''
    field = {ids.get("auto_ricarica"): "added", ids.get("auto_km"): "km", home: "wall"}
    for r in run_csv(flux):
        if r.get("_value") and r["entity_id"] in field:
            hour(parse_time(r["_time"]))[field[r["entity_id"]]] += max(0.0, float(r["_value"]))

    # dati della casa solo nell'intervallo in cui la presa di casa ha erogato energia
    charging = [k for k, h in H.items() if h["wall"] > 0.05]
    if charging:
        hs = datetime.fromtimestamp(min(charging) * 3600, timezone.utc)
        he = datetime.fromtimestamp((max(charging) + 1) * 3600, timezone.utc)
        flux = base_query(hs, he) + f'''
  |> filter(fn: (r) => {flux_any("categoria", HOUSE_CATS)})
  |> keep(columns: ["_time", "_value", "categoria"])'''
        for cat, t, v in run_rows(flux, "categoria"):
            k = int(t.timestamp()) // 3600
            if k in H:
                H[k]["house"][cat] += v

    flux = f'''from(bucket: "{c.INFLUX_BUCKET}")
  |> range(start: {flux_time(qs)}, stop: {flux_time(e)})
  |> filter(fn: (r) => r._measurement == "{c.MEAS_MEAN}" and ({flux_any("entity_id", [ids.get("auto_batteria"), ids.get("auto_potenza")])}))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "entity_id", "mean", "min", "max"])'''
    for r in run_csv(flux):
        h = hour(parse_time(r["_time"]))
        vals = tuple(float(r[k]) if r.get(k) else None for k in ("mean", "min", "max"))
        h["bat" if r["entity_id"] == ids.get("auto_batteria") else "pw"] = vals

    # quota solare+batteria del consumo di casa in ogni ora (ripartizione proporzionale)
    for h in H.values():
        x = h["house"]
        casa = x["solare"] + x["rete_prelievo"] + x["batteria_scarica"] - x["rete_immissione"] - x["batteria_carica"]
        h["share"] = min(1.0, max(0.0, 1 - x["rete_prelievo"] / casa)) if casa > 0.01 else 0.0

    # sessioni = ore consecutive con energia caricata (o prelevata dalla presa di casa)
    active = sorted(k for k, h in H.items() if h["added"] > 0.05 or h["wall"] > 0.05)
    groups: list[list[int]] = []
    for k in active:
        if groups and k - groups[-1][-1] <= SESSION_GAP_H:
            groups[-1].append(k)
        else:
            groups.append([k])

    s_k, e_k = int(s.timestamp()) // 3600, int(e.timestamp()) // 3600
    sessions = []
    for g in groups:
        added = sum(H[k]["added"] for k in g)
        wall = sum(H[k]["wall"] for k in g)
        if max(added, wall) < MIN_SESSION_KWH:  # rumore della presa a vuoto
            for k in g:
                H[k]["home"] = False
                H[k]["noise"] = True
            continue
        at_home = bool(home) and (wall >= 0.3 * added if added > 0.05 else wall > 0.05)
        solar = sum(H[k]["wall"] * H[k]["share"] for k in g) if at_home else 0.0
        for k in g:
            h = H[k]
            h["home"] = at_home
        if not (s_k <= g[0] < e_k):
            continue
        bats = [H[k].get("bat") for k in g]
        soc_start = next((b[1] if b[1] is not None else b[0] for b in bats if b), None)
        soc_end = next((b[2] if b[2] is not None else b[0] for b in reversed(bats) if b), None)
        pws = [H[k]["pw"][2] for k in g if H[k].get("pw") and H[k]["pw"][2] is not None]
        sessions.append({
            "start": datetime.fromtimestamp(g[0] * 3600, TZ).isoformat(),
            "end": datetime.fromtimestamp((g[-1] + 1) * 3600, TZ).isoformat(),
            "home": at_home, "added": round(added, 2), "wall": round(wall, 2),
            "solar": round(solar, 2), "cost": round((wall - solar) * price, 2),
            "soc_start": soc_start, "soc_end": soc_end, "max_power": max(pws) if pws else None,
        })
    return {k: v for k, v in H.items() if s_k <= k < e_k}, sessions


def auto_totals(H: dict) -> dict:
    t = {"home_solar": 0.0, "home_grid": 0.0, "home_added": 0.0, "away": 0.0, "km": 0.0}
    for h in H.values():
        t["km"] += h["km"]
        if h.get("home"):
            t["home_solar"] += h["wall"] * h["share"]
            t["home_grid"] += h["wall"] * (1 - h["share"])
            t["home_added"] += h["added"]
        elif not h.get("noise"):
            t["away"] += h["added"]
    return t


@app.get("/api/auto")
@cached
def api_auto(
    start: date,
    end: date,
    gran: str = Query("day", pattern="^(hour|day|week|month|year)$"),
    prev_start: date | None = None,
    prev_end: date | None = None,
):
    meta = load_meta()
    if not any(m["categoria"] == "auto_ricarica" for m in meta["sensors"].values()):
        return {"enabled": False}
    days = (end - start).days + 1
    if gran == "hour" and days > MAX_HOUR_BUCKETS_DAYS:
        gran = "day"
    s, e = local_range(start, end)
    H, sessions = auto_hours(meta, start, end)

    buckets = all_buckets(s, e, gran)
    index = {k: i for i, k in enumerate(buckets)}
    keys = ["home_solar", "home_grid", "home_added", "away", "km", "sessions"]
    series = {k: [0.0] * len(buckets) for k in keys}
    profile = {"home": [0.0] * 24, "away": [0.0] * 24}
    battery: dict[str, list] = {}
    for k, h in sorted(H.items()):
        t = datetime.fromtimestamp(k * 3600, TZ)
        i = index.get(bucket_key(t, gran))
        if i is None:
            continue
        series["km"][i] += h["km"]
        if h.get("home"):
            series["home_solar"][i] += h["wall"] * h["share"]
            series["home_grid"][i] += h["wall"] * (1 - h["share"])
            series["home_added"][i] += h["added"]
            profile["home"][t.hour] += h["wall"]
        elif not h.get("noise"):
            series["away"][i] += h["added"]
            profile["away"][t.hour] += h["added"]
        if h.get("bat"):
            bk = t.isoformat() if days <= MAX_HOUR_BUCKETS_DAYS else t.date().isoformat()
            battery.setdefault(bk, []).append(h["bat"])
    for sess in sessions:
        i = index.get(bucket_key(datetime.fromisoformat(sess["start"]), gran))
        if i is not None:
            series["sessions"][i] += 1

    bat_points = []
    for bk, vals in battery.items():
        means = [v[0] for v in vals if v[0] is not None]
        mins = [v[1] if v[1] is not None else v[0] for v in vals if v[0] is not None]
        maxs = [v[2] if v[2] is not None else v[0] for v in vals if v[0] is not None]
        if means:
            t = datetime.fromisoformat(bk) if "T" in bk else datetime.combine(date.fromisoformat(bk), time(12), TZ)
            bat_points.append([int(t.timestamp() * 1000), round(sum(means) / len(means), 1), min(mins), max(maxs)])

    prev = None
    if prev_start and prev_end and prev_end >= prev_start:
        PH, psess = auto_hours(meta, prev_start, prev_end)
        prev = {**auto_totals(PH), "sessions": len(psess)}

    return {
        "enabled": True, "gran": gran, "buckets": buckets,
        "series": {k: [round(x, 3) for x in v] for k, v in series.items()},
        "profile": {k: [round(x, 3) for x in v] for k, v in profile.items()},
        "battery": {"step": "hour" if days <= MAX_HOUR_BUCKETS_DAYS else "day", "points": bat_points},
        "sessions": sorted(sessions, key=lambda x: x["start"], reverse=True),
        "prev_totals": prev, "price": meta.get("price"),
    }


@app.get("/api/export.xlsx")
def api_export(start: date, end: date, gran: str = Query("day", pattern="^(hour|day|week|month|year)$")):
    if end < start:
        raise HTTPException(400, "end < start")
    data = api_data(start=start, end=end, gran=gran, prev_start=None, prev_end=None)
    auto = api_auto(start=start, end=end, gran=gran, prev_start=None, prev_end=None)
    content = export_xlsx.build(load_meta(), data, auto, start, end)
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="energia_{start}_{end}.xlsx"'},
    )


# ---------------------------------------------------------------- backup / restore

@app.post("/api/backup")
async def api_backup():
    async with sync_lock:  # niente sync mentre si legge il database
        return await asyncio.to_thread(BACKUP.create)


@app.get("/api/backups")
def api_backups():
    return BACKUP.list()


@app.get("/api/backups/{name}")
def api_backup_download(name: str):
    path = BACKUP.path_of(name)
    if not path:
        raise HTTPException(404, "Backup non trovato")
    return FileResponse(path, media_type="application/gzip", filename=name)


async def do_restore(path: str, protect: str | None = None) -> dict:
    try:
        await asyncio.to_thread(BACKUP.validate, path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    async with sync_lock:
        safety = await asyncio.to_thread(BACKUP.create, "pre-restore", protect)
        try:
            result = await asyncio.to_thread(BACKUP.restore, path)
        except Exception as e:
            log.exception("Restore fallito")
            raise HTTPException(500, f"Restore fallito: {e}. Stato precedente salvato in {safety['name']}")
        finally:
            _cache.clear()
    log.info("Restore completato: %d punti (backup di sicurezza %s)", result["points"], safety["name"])
    return {**result, "safety_backup": safety["name"]}


@app.post("/api/restore")
async def api_restore_upload(request: Request):
    """Restore completo da un file .tar.gz inviato come corpo della richiesta."""
    os.makedirs(BACKUP.dir, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=BACKUP.dir, suffix=".upload")
    try:
        size = 0
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "File troppo grande")
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, "File vuoto")
        return await do_restore(path)
    finally:
        os.remove(path)


@app.post("/api/restore/{name}")
async def api_restore_saved(name: str):
    """Restore completo da un backup già presente sul server."""
    path = BACKUP.path_of(name)
    if not path:
        raise HTTPException(404, "Backup non trovato")
    return await do_restore(path, protect=name)


def asset_version() -> str:
    """Cambia a ogni modifica dei file statici: forza il browser a ricaricare JS e CSS."""
    return str(int(max(os.path.getmtime(os.path.join(STATIC, f)) for f in ("app.js", "style.css", "index.html"))))


@app.get("/")
def index():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        html = f.read()
    v = asset_version()
    for asset in ("/static/app.js", "/static/style.css"):
        html = html.replace(f'"{asset}"', f'"{asset}?v={v}"')
    return Response(html, media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")  # riconvalida con ETag, niente cache "a sentimento"
    return response


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    uvicorn.run(app, host=os.environ.get("WEB_HOST", "0.0.0.0"), port=int(os.environ.get("WEB_PORT", "8090")))
