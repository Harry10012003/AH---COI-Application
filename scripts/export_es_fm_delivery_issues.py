"""Export the six-month Engelbert Strauss FM quality/delivery data.

The report is intentionally line-grain: one row per PPO lot, fabric quality,
fabric type and colour/combo.  This prevents unrelated quality dates from
being merged into a single PPO-level value.

Sources
-------
* Main SQL ``dbo.V_Knit_PPO_Infor``
  * PPO issued date: ``Create Date``
  * requested fabric delivery date: ``Fabric Delivery Date``
* Shipment SQL ``GAK_ShipmentDetail_EGV/EAV``
  * final delivery date: latest ``delivery_date`` for the matched PPO/lot/
    usage/combo shipment record.

The local SQL snapshot is used only to obtain the GO-to-PPO mapping and to
scope the export to the already verified ES brand.  The dated fields are read
live from SQL Server.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from backend.engine import sql_live_engine as sql_engine  # noqa: E402


BRAND_NAME = "ENGELBERT STRAUSS"
BRAND_CODE = "IX5"
DEFAULT_SNAPSHOT = PROJECT_DIR / "data" / "cache" / "live_sheet_snapshot_v56.db"
DEFAULT_EXPORT_DIR = PROJECT_DIR / "data" / "exports"
PPO_QUERY_BATCH_SIZE = 20

CSV_COLUMNS = [
    "GO",
    "PPO",
    "PPO Lot",
    "Brand",
    "Brand Code",
    "Final Garment Factory",
    "Supplier Code",
    "Customer Style",
    "Fabric Quality Code",
    "Fabric Type",
    "Fabric Part",
    "Colour / Combo",
    "PPO Issued Date (Create Date)",
    "Requested Fabric Delivery Date",
    "Final Delivery Date to TGV/TDV",
    "Final Delivery Source",
    "Final Delivery Match",
    "Delivery Variance Days",
    "Delivery Issue",
]


def _add_months(value: date, months: int) -> date:
    """Return a calendar-month shift without adding a dateutil dependency."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    days_per_month = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30,
                      31, 31, 30, 31, 30, 31)
    return date(year, month, min(value.day, days_per_month[month - 1]))


def _as_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for layout in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:19], layout).date()
        except ValueError:
            continue
    return None


def _date_text(value: object) -> str:
    parsed = _as_date(value)
    return parsed.isoformat() if parsed else ""


def _text(value: object) -> str:
    return str(value or "").strip()


def _norm(value: object) -> str:
    return " ".join(_text(value).upper().split())


def _combo_keys(value: object) -> set[str]:
    raw = _norm(value)
    if not raw:
        return set()
    values = {raw}
    if "@" in raw:
        values.update(part.strip() for part in raw.split("@") if part.strip())
    return values


def _lot_key(value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        numeric = float(text)
    except ValueError:
        return text
    if numeric.is_integer():
        return str(int(numeric))
    return text


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _load_es_go_ppos(snapshot: Path) -> tuple[dict[str, list[str]], dict[str, set[str]], list[str]]:
    if not snapshot.is_file():
        raise RuntimeError(f"SQL snapshot not found: {snapshot}")
    with sqlite3.connect(snapshot) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT m.go_no, UPPER(TRIM(m.ppo_no)) AS ppo_no, m.lot_no
            FROM sql_go_ppo_mapping AS m
            INNER JOIN sql_go_jo_color_qty AS color ON color.go_no = m.go_no
            WHERE UPPER(TRIM(COALESCE(color.brand_name, ''))) = ?
              AND TRIM(COALESCE(m.ppo_no, '')) <> ''
            ORDER BY m.go_no, ppo_no
            """,
            (BRAND_NAME,),
        ).fetchall()

    go_by_ppo: dict[str, list[str]] = defaultdict(list)
    lots_by_ppo: dict[str, set[str]] = defaultdict(set)
    for go_no, ppo_no, lot_no in rows:
        go = _text(go_no).upper()
        ppo = _text(ppo_no).upper()
        if go and ppo and go not in go_by_ppo[ppo]:
            go_by_ppo[ppo].append(go)
        lot = _lot_key(lot_no)
        if ppo and lot and lot != "0":
            lots_by_ppo[ppo].add(lot)
    return dict(go_by_ppo), dict(lots_by_ppo), sorted(go_by_ppo)


def _fetch_knit_ppo_rows(ppos: list[str]) -> list[dict]:
    if not ppos:
        return []
    result: list[dict] = []
    with sql_engine._connect() as conn:
        cursor = conn.cursor()
        for batch_number, batch in enumerate(_chunks(ppos, PPO_QUERY_BATCH_SIZE), start=1):
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(
                f"""
                SELECT
                    [PPO NO] AS ppo_no,
                    [Lot_No] AS lot_no,
                    [Brand Code] AS brand_code,
                    [Final Garment Factory Code] AS final_factory,
                    [Fabric Supplier Code] AS supplier_code,
                    [Customer Style No] AS customer_style,
                    [Fabric Type Code] AS fabric_type,
                    [Fabric Part] AS fabric_part,
                    [Quality Code] AS quality_code,
                    [Combo Name] AS combo_name,
                    [Create Date] AS ppo_issued_date,
                    [Fabric Delivery Date] AS requested_delivery_date
                FROM dbo.V_Knit_PPO_Infor
                WHERE [PPO NO] IN ({placeholders})
                """,
                batch,
            )
            columns = [column[0] for column in cursor.description]
            result.extend({**dict(zip(columns, row)), "ppo_source": "V_Knit_PPO_Infor"} for row in cursor.fetchall())
            print(f"Main SQL: PPO batch {batch_number} complete ({len(batch)} PPOs)", flush=True)
    return result


def _fetch_woven_ppo_rows(ppos: list[str]) -> list[dict]:
    """Return the equivalent fields for ES PPOs not present in the Knit view.

    Woven PPO has no separate quality-code field, so its Fabric Code is the
    authoritative quality/fabric identifier for this export.
    """
    if not ppos:
        return []
    result: list[dict] = []
    with sql_engine._connect() as conn:
        cursor = conn.cursor()
        for batch_number, batch in enumerate(_chunks(ppos, PPO_QUERY_BATCH_SIZE), start=1):
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(
                f"""
                SELECT
                    [PPO NO] AS ppo_no,
                    [PPO_Lot_No] AS lot_no,
                    [Brand Code] AS brand_code,
                    [Final Garment Factory Code] AS final_factory,
                    [Fabric Supplier Code] AS supplier_code,
                    [Customer Style No] AS customer_style,
                    [Fabric Type Code] AS fabric_type,
                    [Fabric Type] AS fabric_part,
                    [Fabric Code] AS quality_code,
                    [Combo Name] AS combo_name,
                    [Create Date] AS ppo_issued_date,
                    [Fabric Delivery Date] AS requested_delivery_date
                FROM dbo.V_Woven_PPO_Infor
                WHERE [PPO NO] IN ({placeholders})
                """,
                batch,
            )
            columns = [column[0] for column in cursor.description]
            result.extend({**dict(zip(columns, row)), "ppo_source": "V_Woven_PPO_Infor"} for row in cursor.fetchall())
            print(f"Woven SQL: PPO batch {batch_number} complete ({len(batch)} PPOs)", flush=True)
    return result


def _shipment_database(factory: object) -> tuple[str, str]:
    if _norm(factory) == "EAV":
        return sql_engine.SHIPMENT_SQL_EAV_DATABASE, sql_engine.SHIPMENT_SQL_EAV_TABLE
    return sql_engine.SHIPMENT_SQL_EGV_DATABASE, sql_engine.SHIPMENT_SQL_EGV_TABLE


def _fetch_final_shipment_rows(rows: list[dict]) -> list[dict]:
    ppos_by_factory: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        ppo = _norm(row.get("ppo_no"))
        if ppo:
            ppos_by_factory[_shipment_database(row.get("final_factory"))].add(ppo)

    result: list[dict] = []
    for (database, table_name), ppos in ppos_by_factory.items():
        with sql_engine._connect_shipment(database) as conn:
            cursor = conn.cursor()
            for batch in _chunks(sorted(ppos), 200):
                placeholders = ",".join("?" for _ in batch)
                cursor.execute(
                    f"""
                    SELECT
                        ppo_no,
                        original_lot_no AS lot_no,
                        RTRIM([usage]) AS fabric_type,
                        combo AS combo_name,
                        MAX(delivery_date) AS final_delivery_date,
                        MAX(ship_date) AS latest_ship_date,
                        MAX(RTRIM(ship_type)) AS ship_type
                    FROM {table_name}
                    WHERE ppo_no IN ({placeholders})
                    GROUP BY ppo_no, original_lot_no, RTRIM([usage]), combo
                    """,
                    batch,
                )
                columns = [column[0] for column in cursor.description]
                for values in cursor.fetchall():
                    result.append({**dict(zip(columns, values)), "source": f"{database}.{table_name}"})
    return result


def _build_shipment_lookup(rows: list[dict]) -> dict[tuple[str, str, str, str], dict]:
    lookup: dict[tuple[str, str, str, str], dict] = {}
    for row in rows:
        ppo = _norm(row.get("ppo_no"))
        lot = _lot_key(row.get("lot_no"))
        fabric_type = _norm(row.get("fabric_type"))
        final_date = _as_date(row.get("final_delivery_date"))
        if not ppo or not final_date:
            continue
        for combo in _combo_keys(row.get("combo_name")):
            key = (ppo, lot, fabric_type, combo)
            current = lookup.get(key)
            if current is None or final_date > _as_date(current.get("final_delivery_date")):
                lookup[key] = row
    return lookup


def _shipment_for_row(row: dict, lookup: dict[tuple[str, str, str, str], dict]) -> tuple[dict | None, str]:
    ppo = _norm(row.get("ppo_no"))
    lot = _lot_key(row.get("lot_no"))
    fabric_type = _norm(row.get("fabric_type"))
    combos = _combo_keys(row.get("combo_name"))
    candidates: list[tuple[str, tuple[str, str, str, str]]] = []
    for combo in combos:
        candidates.extend(
            [
                ("PPO + lot + type + combo", (ppo, lot, fabric_type, combo)),
                ("PPO + type + combo (lot unavailable)", (ppo, "", fabric_type, combo)),
            ]
        )
    for match_label, key in candidates:
        shipment = lookup.get(key)
        if shipment is not None:
            return shipment, match_label
    return None, "No matching shipment delivery record"


def _unique_current_rows(rows: list[dict]) -> list[dict]:
    unique: dict[tuple[str, ...], dict] = {}
    for row in rows:
        key = (
            _norm(row.get("ppo_no")),
            _lot_key(row.get("lot_no")),
            _norm(row.get("fabric_type")),
            _norm(row.get("quality_code")),
            _norm(row.get("combo_name")),
            _date_text(row.get("requested_delivery_date")),
        )
        if key not in unique:
            unique[key] = row
    return list(unique.values())


def _report_rows(
    main_rows: list[dict],
    go_by_ppo: dict[str, list[str]],
    lots_by_ppo: dict[str, set[str]],
    shipment_lookup: dict[tuple[str, str, str, str], dict],
    cutoff: date,
    as_of: date,
) -> list[dict]:
    report: list[dict] = []
    for source in _unique_current_rows(main_rows):
        if _norm(source.get("brand_code")) != BRAND_CODE:
            continue
        requested = _as_date(source.get("requested_delivery_date"))
        if requested is None or requested < cutoff or requested > as_of:
            continue
        ppo = _norm(source.get("ppo_no"))
        lot = _lot_key(source.get("lot_no"))
        mapped_lots = lots_by_ppo.get(ppo, set())
        if lot and mapped_lots and lot not in mapped_lots:
            # A PPO can retain historical/internal lots which are not assigned
            # to an ES GO.  They must not be represented as GO delivery lines.
            continue
        shipment, match_label = _shipment_for_row(source, shipment_lookup)
        final_delivery = _as_date((shipment or {}).get("final_delivery_date"))
        variance = (final_delivery - requested).days if final_delivery else None
        if final_delivery is None:
            issue = "No final delivery record"
        elif variance > 0:
            issue = "Late"
        else:
            issue = "On/before requested date"
        report.append(
            {
                "GO": ", ".join(go_by_ppo.get(ppo, [])),
                "PPO": ppo,
                "PPO Lot": lot,
                "Brand": BRAND_NAME,
                "Brand Code": BRAND_CODE,
                "Final Garment Factory": _norm(source.get("final_factory")),
                "Supplier Code": _norm(source.get("supplier_code")),
                "Customer Style": _text(source.get("customer_style")),
                "Fabric Quality Code": _norm(source.get("quality_code")),
                "Fabric Type": _norm(source.get("fabric_type")),
                "Fabric Part": _text(source.get("fabric_part")),
                "Colour / Combo": _text(source.get("combo_name")),
                "PPO Issued Date (Create Date)": _date_text(source.get("ppo_issued_date")),
                "Requested Fabric Delivery Date": _date_text(requested),
                "Final Delivery Date to TGV/TDV": _date_text(final_delivery),
                "Final Delivery Source": _text((shipment or {}).get("source")),
                "Final Delivery Match": match_label,
                "Delivery Variance Days": "" if variance is None else variance,
                "Delivery Issue": issue,
            }
        )
    return sorted(
        report,
        key=lambda item: (
            item["Requested Fabric Delivery Date"],
            item["GO"],
            item["PPO"],
            item["PPO Lot"],
            item["Fabric Quality Code"],
            item["Colour / Combo"],
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export ES FM quality/delivery lines for the prior six months.")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today(), help="Inclusive end date (YYYY-MM-DD).")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT, help="SQLite snapshot containing GO/PPO mapping.")
    parser.add_argument("--output", type=Path, help="CSV path. Defaults under data/exports.")
    args = parser.parse_args()

    as_of = args.as_of
    cutoff = _add_months(as_of, -6)
    output = args.output or DEFAULT_EXPORT_DIR / f"es_fm_delivery_issues_{as_of.isoformat()}.csv"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    go_by_ppo, lots_by_ppo, scoped_ppos = _load_es_go_ppos(args.snapshot.resolve())
    knit_rows = _fetch_knit_ppo_rows(scoped_ppos)
    knit_ppos = {_norm(row.get("ppo_no")) for row in knit_rows if _norm(row.get("ppo_no"))}
    woven_rows = _fetch_woven_ppo_rows([ppo for ppo in scoped_ppos if ppo not in knit_ppos])
    main_rows = [*knit_rows, *woven_rows]
    shipment_rows = _fetch_final_shipment_rows(main_rows)
    report = _report_rows(main_rows, go_by_ppo, lots_by_ppo, _build_shipment_lookup(shipment_rows), cutoff, as_of)

    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(report)

    sourced_ppos = {_norm(row.get("ppo_no")) for row in main_rows if _norm(row.get("ppo_no"))}
    metadata = {
        "brand": BRAND_NAME,
        "brand_code": BRAND_CODE,
        "as_of": as_of.isoformat(),
        "requested_delivery_window": {"from": cutoff.isoformat(), "to": as_of.isoformat()},
        "go_count": len({go for gos in go_by_ppo.values() for go in gos}),
        "scoped_ppo_count": len(scoped_ppos),
        "main_sql_ppo_count": len(sourced_ppos),
        "main_sql_missing_ppo_count": len(set(scoped_ppos) - sourced_ppos),
        "main_sql_missing_ppos": sorted(set(scoped_ppos) - sourced_ppos),
        "shipment_group_count": len(shipment_rows),
        "report_row_count": len(report),
        "late_row_count": sum(row["Delivery Issue"] == "Late" for row in report),
        "no_final_delivery_row_count": sum(row["Delivery Issue"] == "No final delivery record" for row in report),
        "sources": {
            "ppo_dates": "ESQ_DATA.dbo.V_Knit_PPO_Infor and dbo.V_Woven_PPO_Infor",
            "final_delivery": "Shipment SQL GAK_ShipmentDetail_EGV/EAV",
            "go_mapping": str(args.snapshot.resolve()),
        },
        "field_definition": {
            "PPO Issued Date (Create Date)": "V_Knit_PPO_Infor/V_Woven_PPO_Infor.[Create Date]",
            "Requested Fabric Delivery Date": "V_Knit_PPO_Infor/V_Woven_PPO_Infor.[Fabric Delivery Date]",
            "Final Delivery Date to TGV/TDV": "MAX(GAK_ShipmentDetail_[EGV/EAV].delivery_date) for matched PPO/lot/type/combo",
        },
    }
    metadata_path = output.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"csv": str(output), "metadata": str(metadata_path), **metadata}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
