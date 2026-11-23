"""Backup e restore completo dei dati (InfluxDB + metadati) in un archivio .tar.gz.

Formato dell'archivio (versione 1):
  manifest.json            info sul backup (app, formato, data, punti per measurement, periodo)
  meta.json                nomi sensori, prezzo energia (se presente)
  data/<measurement>.lp    tutti i punti in InfluxDB line protocol, precisione secondi
"""

import io
import json
import os
import re
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone

from influxdb_client import WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

FORMAT = 1
APP = "energia"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+\.tar\.gz$")
FIELDS = {"change", "sum", "state", "mean", "min", "max"}
SKIP_COLS = {"", "result", "table", "_start", "_stop", "_time", "_measurement"}
MAX_LP_BYTES = 2 * 1024**3  # protezione contro archivi anomali
WRITE_BATCH = 5000


def _esc_meas(s: str) -> str:
    return s.replace("\\", "\\\\").replace(",", r"\,").replace(" ", r"\ ")


def _esc_tag(s: str) -> str:
    return s.replace("\\", "\\\\").replace(",", r"\,").replace("=", r"\=").replace(" ", r"\ ")


def _ts(v: str) -> int:
    return int(datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())


class Backup:
    def __init__(self, influx, csv_dialect, org: str, bucket: str, measurements: list[str], meta_path: str, backup_dir: str, keep: int = 10):
        self.influx, self.dialect, self.org, self.bucket = influx, csv_dialect, org, bucket
        self.measurements, self.meta_path, self.dir, self.keep = measurements, meta_path, backup_dir, keep

    # ------------------------------------------------------------ backup
    def _dump_measurement(self, meas: str, out) -> tuple[int, int | None, int | None]:
        flux = f'''from(bucket: "{self.bucket}")
  |> range(start: 0)
  |> filter(fn: (r) => r._measurement == "{meas}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")'''
        header, tag_cols, field_cols = None, [], []
        n, first, last = 0, None, None
        m = _esc_meas(meas)
        for row in self.influx.query_api().query_csv(flux, org=self.org, dialect=self.dialect):
            if not row or len(row) < 3:
                continue
            if "_time" in row:
                header = row
                field_cols = [(i, c) for i, c in enumerate(row) if c in FIELDS]
                tag_cols = [(i, c) for i, c in enumerate(row) if c not in FIELDS and c not in SKIP_COLS]
                continue
            if header is None:
                continue
            fields = ",".join(f"{c}={float(row[i])!r}" for i, c in field_cols if row[i] != "")
            if not fields:
                continue
            tags = "".join(f",{_esc_tag(c)}={_esc_tag(row[i])}" for i, c in tag_cols if row[i] != "")
            ts = _ts(row[header.index("_time")])
            out.write(f"{m}{tags} {fields} {ts}\n".encode())
            n += 1
            first = ts if first is None else min(first, ts)
            last = ts if last is None else max(last, ts)
        return n, first, last

    def create(self, prefix: str = "energia_backup", protect: str | None = None) -> dict:
        os.makedirs(self.dir, exist_ok=True)
        now = datetime.now()
        name = f"{prefix}_{now.strftime('%Y%m%d_%H%M%S')}.tar.gz"
        path = os.path.join(self.dir, name)
        manifest = {"app": APP, "format": FORMAT, "created": datetime.now(timezone.utc).isoformat(),
                    "bucket": self.bucket, "measurements": {}}
        tmp = path + ".part"
        with tarfile.open(tmp, "w:gz", compresslevel=6) as tar:
            for meas in self.measurements:
                with tempfile.TemporaryFile() as f:
                    n, first, last = self._dump_measurement(meas, f)
                    size = f.tell()
                    f.seek(0)
                    info = tarfile.TarInfo(f"data/{meas}.lp")
                    info.size, info.mtime = size, int(now.timestamp())
                    tar.addfile(info, f)
                manifest["measurements"][meas] = {
                    "points": n,
                    "first": datetime.fromtimestamp(first, timezone.utc).isoformat() if first else None,
                    "last": datetime.fromtimestamp(last, timezone.utc).isoformat() if last else None,
                }
            if os.path.exists(self.meta_path):
                tar.add(self.meta_path, arcname="meta.json")
            raw = json.dumps(manifest, indent=1).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mtime = len(raw), int(now.timestamp())
            tar.addfile(info, io.BytesIO(raw))
        os.replace(tmp, path)
        self._prune(protect)
        return {"name": name, "size": os.path.getsize(path), **self._summary(manifest)}

    def _prune(self, protect: str | None = None) -> None:
        files = sorted((f for f in os.listdir(self.dir) if NAME_RE.match(f) and f != protect),
                       key=lambda f: os.path.getmtime(os.path.join(self.dir, f)), reverse=True)
        for f in files[self.keep - (1 if protect else 0):]:
            os.remove(os.path.join(self.dir, f))

    @staticmethod
    def _summary(manifest: dict) -> dict:
        ms = manifest.get("measurements", {})
        firsts = [m["first"] for m in ms.values() if m.get("first")]
        lasts = [m["last"] for m in ms.values() if m.get("last")]
        return {"points": sum(m.get("points", 0) for m in ms.values()),
                "first": min(firsts) if firsts else None, "last": max(lasts) if lasts else None,
                "created": manifest.get("created")}

    def list(self) -> list[dict]:
        if not os.path.isdir(self.dir):
            return []
        out = []
        for f in os.listdir(self.dir):
            p = os.path.join(self.dir, f)
            if NAME_RE.match(f) and os.path.isfile(p):
                info = {"name": f, "size": os.path.getsize(p),
                        "mtime": datetime.fromtimestamp(os.path.getmtime(p), timezone.utc).isoformat()}
                try:
                    info.update(self._summary(self.read_manifest(p)))
                except (ValueError, tarfile.TarError, OSError):
                    info["invalid"] = True
                out.append(info)
        return sorted(out, key=lambda x: x["mtime"], reverse=True)

    def path_of(self, name: str) -> str | None:
        if not NAME_RE.match(name):
            return None
        p = os.path.join(self.dir, name)
        return p if os.path.isfile(p) else None

    # ------------------------------------------------------------ restore
    @staticmethod
    def read_manifest(path: str) -> dict:
        with tarfile.open(path, "r:gz") as tar:
            m = tar.getmember("manifest.json")
            if not m.isfile() or m.size > 1_000_000:
                raise ValueError("manifest.json non valido")
            manifest = json.load(tar.extractfile(m))
        if manifest.get("app") != APP:
            raise ValueError("Il file non è un backup di questa applicazione")
        if manifest.get("format") != FORMAT:
            raise ValueError(f"Formato di backup non supportato: {manifest.get('format')}")
        return manifest

    def validate(self, path: str) -> dict:
        """Controlla l'archivio senza toccare il database."""
        try:
            manifest = self.read_manifest(path)
        except KeyError:
            raise ValueError("manifest.json mancante: il file non è un backup valido")
        except (tarfile.TarError, OSError, EOFError) as e:
            raise ValueError(f"Archivio non leggibile: {e}")
        with tarfile.open(path, "r:gz") as tar:
            names = {m.name: m for m in tar.getmembers()}
        for meas in manifest["measurements"]:
            if meas not in self.measurements:
                raise ValueError(f"Measurement sconosciuto nel backup: {meas}")
            m = names.get(f"data/{meas}.lp")
            if m is None or not m.isfile() or m.size > MAX_LP_BYTES:
                raise ValueError(f"File dati mancante o non valido: data/{meas}.lp")
        return manifest

    def _delete_all(self) -> None:
        """Cancella tutti i punti delle measurement gestite."""
        delete_api = self.influx.delete_api()
        stop = datetime.now(timezone.utc) + timedelta(days=366)
        for meas in self.measurements:
            delete_api.delete(datetime(1970, 1, 1, tzinfo=timezone.utc), stop,
                              f'_measurement="{meas}"', bucket=self.bucket, org=self.org)

    def wipe(self) -> None:
        """Cancellazione completa: dati in InfluxDB e metadati dei sensori (non backup e impostazioni)."""
        self._delete_all()
        try:
            os.remove(self.meta_path)
        except FileNotFoundError:
            pass

    def restore(self, path: str) -> dict:
        manifest = self.validate(path)
        self._delete_all()  # restore completo
        write_api = self.influx.write_api(write_options=SYNCHRONOUS)
        written = {}
        with tarfile.open(path, "r:gz") as tar:
            for meas in manifest["measurements"]:
                prefix = _esc_meas(meas) + ","
                f = tar.extractfile(f"data/{meas}.lp")
                batch, n = [], 0
                for raw in f:
                    line = raw.decode().rstrip("\n")
                    if not line:
                        continue
                    if not line.startswith(prefix):
                        raise ValueError(f"Riga non valida in data/{meas}.lp")
                    batch.append(line)
                    if len(batch) >= WRITE_BATCH:
                        write_api.write(bucket=self.bucket, org=self.org, record=batch, write_precision=WritePrecision.S)
                        n += len(batch)
                        batch = []
                if batch:
                    write_api.write(bucket=self.bucket, org=self.org, record=batch, write_precision=WritePrecision.S)
                    n += len(batch)
                written[meas] = n
            if "meta.json" in tar.getnames():
                m = tar.getmember("meta.json")
                if m.isfile() and m.size < 5_000_000:
                    meta = json.load(tar.extractfile(m))
                    tmp = self.meta_path + ".tmp"
                    with open(tmp, "w") as out:
                        json.dump(meta, out, ensure_ascii=False, indent=1)
                    os.replace(tmp, self.meta_path)
        return {"written": written, "points": sum(written.values()), **self._summary(manifest)}
