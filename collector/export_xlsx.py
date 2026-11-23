"""Esportazione in Excel (.xlsx) dei dati del periodo selezionato."""

import io
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HOUSE = ["solare", "rete_prelievo", "rete_immissione", "batteria_carica", "batteria_scarica"]
GRAN_LABEL = {"hour": "Ora", "day": "Giorno", "week": "Settimana", "month": "Mese", "year": "Anno"}
DATE_FMT = {"hour": "dd/mm/yyyy hh:mm", "day": "ddd dd/mm/yyyy", "week": "dd/mm/yyyy", "month": "mmm yyyy", "year": "0"}

KWH = '#,##0.00'
PCT = '0.0%'
EUR = '#,##0.00 €'
HEAD_FILL = PatternFill("solid", fgColor="1F2A3A")
HEAD_FONT = Font(bold=True, color="FFFFFF")
TOTAL_FILL = PatternFill("solid", fgColor="EEF2F7")
THIN = Border(top=Side(style="thin", color="9AA5B1"))


def bucket_value(key: str, gran: str):
    """Chiave del bucket -> valore data vero per Excel."""
    if gran == "hour":
        return datetime.strptime(key, "%Y-%m-%dT%H:%M")
    if gran in ("day", "week"):
        return date.fromisoformat(key)
    if gran == "month":
        return date.fromisoformat(key + "-01")
    return int(key)


def write_table(ws, headers, rows, formats, first_fmt=None, totals=None, widths=None):
    """Tabella con intestazione, filtri, riquadri bloccati e riga totale opzionale.
    totals: lista per colonna di 'sum' | callable(riga_totale, lettera) -> formula | None."""
    ws.append(headers)
    for c in ws[1]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 32
    for r in rows:
        ws.append(r)
    n = len(rows)
    for j, fmt in enumerate(formats, start=1):
        if fmt:
            for i in range(2, n + 2):
                ws.cell(i, j).number_format = fmt
    if first_fmt:
        for i in range(2, n + 2):
            ws.cell(i, 1).number_format = first_fmt
    ws.freeze_panes = "B2"
    if n:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{n + 1}"
    if totals and n:
        t = n + 2
        ws.cell(t, 1, "Totale")
        for j, kind in enumerate(totals, start=1):
            col = get_column_letter(j)
            if kind == "sum":
                ws.cell(t, j, f"=SUM({col}2:{col}{n + 1})")
            elif callable(kind):
                ws.cell(t, j, kind(t, col))
            if formats[j - 1]:
                ws.cell(t, j).number_format = formats[j - 1]
        for c in ws[t]:
            c.font, c.fill, c.border = Font(bold=True), TOTAL_FILL, THIN
    for j, w in enumerate(widths or [], start=1):
        ws.column_dimensions[get_column_letter(j)].width = w
    return n


def build(meta: dict, data: dict, auto: dict | None, start: date, end: date) -> bytes:
    sensors = meta["sensors"]
    price = meta.get("price") or 0.0
    gran = data["gran"]
    buckets = data["buckets"]
    n = len(buckets)
    series = data["series"]

    cat = {k: [0.0] * n for k in HOUSE}
    for sid, vals in series.items():
        c = sensors.get(sid, {}).get("categoria")
        if c in cat:
            for i, v in enumerate(vals):
                cat[c][i] += v
    casa = [max(0.0, cat["solare"][i] + cat["rete_prelievo"][i] + cat["batteria_scarica"][i]
                - cat["rete_immissione"][i] - cat["batteria_carica"][i]) for i in range(n)]
    devices = [sid for sid, m in sensors.items() if m["categoria"] == "dispositivo"]
    devices.sort(key=lambda s: -sum(series.get(s, [0])))
    solar_ids = [sid for sid, m in sensors.items() if m["categoria"] == "solare"]
    periods = [bucket_value(k, gran) for k in buckets]

    wb = Workbook()

    # ------------------------------------------------ Riepilogo
    ws = wb.active
    ws.title = "Riepilogo"
    T = {k: sum(v) for k, v in cat.items()}
    T["casa"] = sum(casa)
    ws["A1"] = "Energia Casa — esportazione dati"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Periodo: {start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')} · dettaglio: {GRAN_LABEL[gran].lower()}"
    ws["A3"] = f"Generato il {datetime.now().strftime('%d/%m/%Y %H:%M')} · prezzo energia {price:.2f} €/kWh (da Home Assistant)"
    for a in ("A2", "A3"):
        ws[a].font = Font(color="666666")
    rows = [
        ("Casa", None, None),
        ("Produzione solare", T["solare"], KWH),
        ("Consumo casa", T["casa"], KWH),
        ("Prelievo dalla rete", T["rete_prelievo"], KWH),
        ("Immessa in rete", T["rete_immissione"], KWH),
        ("Carica batteria", T["batteria_carica"], KWH),
        ("Scarica batteria", T["batteria_scarica"], KWH),
        ("Autosufficienza", 1 - T["rete_prelievo"] / T["casa"] if T["casa"] > 0 else None, PCT),
        ("Autoconsumo", 1 - T["rete_immissione"] / T["solare"] if T["solare"] > 0 else None, PCT),
        ("Costo energia da rete", T["rete_prelievo"] * price, EUR),
        ("Risparmio stimato", (T["casa"] - T["rete_prelievo"]) * price, EUR),
    ]
    if auto and auto.get("enabled"):
        S = auto["series"]
        hs, hg, ha, aw, km = (sum(S[k]) for k in ("home_solar", "home_grid", "home_added", "away", "km"))
        rows += [
            ("", None, None),
            ("Auto", None, None),
            ("Energia caricata nell'auto", ha + aw, KWH),
            ("Ricaricata a casa (dalla presa)", hs + hg, KWH),
            ("  di cui da solare/batteria", hs, KWH),
            ("  di cui da rete", hg, KWH),
            ("Ricaricata fuori casa", aw, KWH),
            ("Quota solare ricariche casa", hs / (hs + hg) if hs + hg > 0 else None, PCT),
            ("Km percorsi", km, '#,##0'),
            ("Consumo medio (kWh/100 km)", 100 * (ha + aw) / km if km > 5 else None, '0.0'),
            ("Costo ricarica a casa", hg * price, EUR),
            ("Sessioni di ricarica", len(auto["sessions"]), '0'),
        ]
    r0 = 5
    for i, (label, val, fmt) in enumerate(rows):
        a, b = ws.cell(r0 + i, 1, label), ws.cell(r0 + i, 2, val)
        if fmt is None and label:
            a.font, a.fill = HEAD_FONT, HEAD_FILL
            ws.cell(r0 + i, 2).fill = HEAD_FILL
        elif fmt:
            b.number_format = fmt
            b.font = Font(bold=True)
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 16

    # ------------------------------------------------ Casa
    ws = wb.create_sheet("Casa")
    rows = []
    for i in range(n):
        c, s = casa[i], cat["solare"][i]
        rows.append([periods[i], s, c, cat["rete_prelievo"][i], cat["rete_immissione"][i],
                     cat["batteria_carica"][i], cat["batteria_scarica"][i],
                     1 - cat["rete_prelievo"][i] / c if c > 0.01 else None,
                     1 - cat["rete_immissione"][i] / s if s > 0.01 else None])
    pct_formula = lambda num, den: (lambda t, col: f'=IF({den}{t}>0,1-{num}{t}/{den}{t},"")')
    count = write_table(
        ws, [GRAN_LABEL[gran], "Solare (kWh)", "Consumo casa (kWh)", "Prelievo rete (kWh)", "Immissione rete (kWh)",
             "Carica batteria (kWh)", "Scarica batteria (kWh)", "Autosufficienza", "Autoconsumo"],
        rows, [None, KWH, KWH, KWH, KWH, KWH, KWH, PCT, PCT], DATE_FMT[gran],
        [None, "sum", "sum", "sum", "sum", "sum", "sum", pct_formula("D", "C"), pct_formula("E", "B")],
        [18, 13, 13, 13, 13, 13, 13, 14, 13])
    if count > 1:
        ch = BarChart()
        ch.title, ch.y_axis.title, ch.height, ch.width = "Solare, consumo e prelievo", "kWh", 9, 22
        ch.add_data(Reference(ws, min_col=2, max_col=4, min_row=1, max_row=count + 1), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=count + 1))
        for s_, color in zip(ch.series, ("EDA100", "EB6834", "2A78D6")):
            s_.graphicalProperties.solidFill = color
            s_.graphicalProperties.line.noFill = True
        ws.add_chart(ch, "K2")

    # ------------------------------------------------ Dispositivi
    ws = wb.create_sheet("Dispositivi")
    rows = []
    for i in range(n):
        dv = [series.get(d, [0.0] * n)[i] for d in devices]
        rows.append([periods[i], casa[i], *dv, max(0.0, casa[i] - sum(dv))])
    write_table(ws, [GRAN_LABEL[gran], "Consumo casa (kWh)", *[sensors[d]["label"] for d in devices], "Non monitorato (kWh)"],
                rows, [None] + [KWH] * (len(devices) + 2), DATE_FMT[gran],
                [None] + ["sum"] * (len(devices) + 2), [18] + [14] * (len(devices) + 2))

    # ------------------------------------------------ Solare
    ws = wb.create_sheet("Solare")
    rows = [[periods[i], *[series.get(s, [0.0] * n)[i] for s in solar_ids], cat["solare"][i]] for i in range(n)]
    write_table(ws, [GRAN_LABEL[gran], *[sensors[s]["label"] for s in solar_ids], "Totale solare (kWh)"],
                rows, [None] + [KWH] * (len(solar_ids) + 1), DATE_FMT[gran],
                [None] + ["sum"] * (len(solar_ids) + 1), [18] + [20] * (len(solar_ids) + 1))

    # ------------------------------------------------ Auto
    if auto and auto.get("enabled"):
        S = auto["series"]
        ws = wb.create_sheet("Auto")
        rows = [[periods[i], S["home_solar"][i], S["home_grid"][i], S["home_solar"][i] + S["home_grid"][i],
                 S["home_added"][i], S["away"][i], S["km"][i], int(S["sessions"][i])] for i in range(n)]
        write_table(ws, [GRAN_LABEL[gran], "Casa · da solare/batteria (kWh)", "Casa · da rete (kWh)", "Casa · dalla presa (kWh)",
                         "Casa · entrata nell'auto (kWh)", "Fuori casa (kWh)", "Km percorsi", "Sessioni"],
                    rows, [None, KWH, KWH, KWH, KWH, KWH, '#,##0', '0'], DATE_FMT[gran],
                    [None] + ["sum"] * 7, [18, 16, 14, 14, 16, 13, 12, 10])

        ws = wb.create_sheet("Sessioni ricarica")
        rows = []
        for x in sorted(auto["sessions"], key=lambda s: s["start"]):
            s_ = datetime.fromisoformat(x["start"]).replace(tzinfo=None)
            e_ = datetime.fromisoformat(x["end"]).replace(tzinfo=None)
            rows.append([s_, e_, (e_ - s_) / timedelta(hours=1), "Casa" if x["home"] else "Fuori casa",
                         x["added"], x["wall"] if x["home"] else None, x["solar"] if x["home"] else None,
                         x["solar"] / x["wall"] if x["home"] and x["wall"] > 0 else None,
                         x["soc_start"], x["soc_end"], x["max_power"], x["cost"] if x["home"] else None])
        write_table(ws, ["Inizio", "Fine", "Durata (h)", "Luogo", "kWh nell'auto", "kWh dalla presa", "kWh da solare",
                         "Quota solare", "Batteria inizio %", "Batteria fine %", "Potenza max (kW)", "Costo rete (€)"],
                    rows, ["dd/mm/yyyy hh:mm", "dd/mm/yyyy hh:mm", '0', None, KWH, KWH, KWH, PCT, '0', '0', '0.0', EUR], None,
                    [None, None, "sum", None, "sum", "sum", "sum", pct_formula_ratio("G", "F"), None, None, None, "sum"],
                    [17, 17, 10, 12, 13, 14, 13, 12, 12, 12, 13, 13])

    # ------------------------------------------------ Dati orari
    h = data.get("hourly")
    if h:
        ws = wb.create_sheet("Dati orari")
        g = {k: h["cats"].get(k) or [[0.0] * 24 for _ in h["dates"]] for k in HOUSE}
        rows = []
        now = datetime.now()
        for d, ds in enumerate(h["dates"]):
            day = date.fromisoformat(ds)
            for hh in range(24):
                t = datetime(day.year, day.month, day.day, hh)
                if t > now:
                    break
                v = {k: g[k][d][hh] for k in HOUSE}
                c = max(0.0, v["solare"] + v["rete_prelievo"] + v["batteria_scarica"] - v["rete_immissione"] - v["batteria_carica"])
                rows.append([t, v["solare"], c, v["rete_prelievo"], v["rete_immissione"], v["batteria_carica"], v["batteria_scarica"]])
        write_table(ws, ["Ora", "Solare (kWh)", "Consumo casa (kWh)", "Prelievo rete (kWh)", "Immissione rete (kWh)",
                         "Carica batteria (kWh)", "Scarica batteria (kWh)"],
                    rows, [None] + ['#,##0.000'] * 6, "dd/mm/yyyy hh:mm",
                    [None] + ["sum"] * 6, [18, 13, 13, 13, 13, 13, 13])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def pct_formula_ratio(num: str, den: str):
    return lambda t, col: f'=IF({den}{t}>0,{num}{t}/{den}{t},"")'
