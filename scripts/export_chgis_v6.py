import argparse
import csv
import sqlite3
import struct
from pathlib import Path

from export_chgis_tgaz import (
    CSV_FIELDS as BASE_CSV_FIELDS,
    DEFAULT_SQL_PATH,
    build_rows as build_tgaz_rows,
)


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V6_DIR = (
    APP_ROOT
    / "data"
    / "CHGISv6-2021"
)
DEFAULT_CSV_PATH = APP_ROOT / "data" / "CHGIS_places.csv"
DEFAULT_V6_CSV_PATH = APP_ROOT / "data" / "CHGIS_v6_places.csv"
DEFAULT_SUPPLEMENT_CSV_PATH = APP_ROOT / "data" / "CHGIS_tgaz_supplement.csv"
DEFAULT_DB_PATH = APP_ROOT / "data" / "chgis_places.sqlite"

V6_POINT_LAYERS = (
    ("v6_time_pref_pts_utf_wgs84", "v6_prefecture_points"),
    ("v6_time_cnty_pts_utf_wgs84", "v6_county_points"),
)

EXTRA_FIELDS = ["SOURCE_DATA", "SOURCE_LAYER", "V6_SYS_ID", "HVD_ID"]
CSV_FIELDS = BASE_CSV_FIELDS + EXTRA_FIELDS


def decode_dbf_value(raw, field, encoding):
    text = raw.decode(encoding, errors="replace").strip()
    if not text:
        return ""
    if field["type"] in {"N", "F", "I"}:
        return text
    return text


def read_dbf(dbf_path, encoding="utf-8"):
    with dbf_path.open("rb") as dbf:
        header = dbf.read(32)
        record_count = struct.unpack("<I", header[4:8])[0]
        header_length = struct.unpack("<H", header[8:10])[0]
        record_length = struct.unpack("<H", header[10:12])[0]

        fields = []
        while True:
            descriptor = dbf.read(32)
            if descriptor[0] == 0x0D:
                break
            name = descriptor[:11].split(b"\x00", 1)[0].decode("ascii", errors="replace")
            fields.append(
                {
                    "name": name,
                    "type": chr(descriptor[11]),
                    "length": descriptor[16],
                    "decimal_count": descriptor[17],
                }
            )

        dbf.seek(header_length)
        rows = []
        for _ in range(record_count):
            record = dbf.read(record_length)
            if not record or record[0:1] == b"*":
                continue
            offset = 1
            row = {}
            for field in fields:
                raw = record[offset : offset + field["length"]]
                offset += field["length"]
                row[field["name"]] = decode_dbf_value(raw, field, encoding)
            rows.append(row)
        return rows


def read_shp_geometries(shp_path):
    geometries = []
    with shp_path.open("rb") as shp:
        shp.seek(100)
        while True:
            record_header = shp.read(8)
            if not record_header:
                break
            if len(record_header) != 8:
                raise ValueError(f"Malformed shapefile record header in {shp_path}")
            _record_number, content_length_words = struct.unpack(">2i", record_header)
            content = shp.read(content_length_words * 2)
            if len(content) < 4:
                geometries.append({"wkt": "", "x": "", "y": "", "bbox": ""})
                continue
            shape_type = struct.unpack("<i", content[:4])[0]
            if shape_type == 0:
                geometries.append({"wkt": "", "x": "", "y": "", "bbox": ""})
            elif shape_type == 1 and len(content) >= 20:
                x_coord, y_coord = struct.unpack("<2d", content[4:20])
                geometries.append(
                    {
                        "wkt": f"POINT ({x_coord:.12g} {y_coord:.12g})",
                        "x": f"{x_coord:.12g}",
                        "y": f"{y_coord:.12g}",
                        "bbox": "",
                    }
                )
            elif shape_type in {3, 5, 8, 13, 15, 18, 23, 25, 28, 31} and len(content) >= 36:
                min_x, min_y, max_x, max_y = struct.unpack("<4d", content[4:36])
                bbox_wkt = (
                    f"POLYGON (({min_x:.12g} {min_y:.12g}, {max_x:.12g} {min_y:.12g}, "
                    f"{max_x:.12g} {max_y:.12g}, {min_x:.12g} {max_y:.12g}, "
                    f"{min_x:.12g} {min_y:.12g}))"
                )
                geometries.append({"wkt": bbox_wkt, "x": "", "y": "", "bbox": bbox_wkt})
            else:
                geometries.append({"wkt": "", "x": "", "y": "", "bbox": ""})
    return geometries


def cpg_encoding(layer_path):
    cpg_path = layer_path.with_suffix(".cpg")
    if not cpg_path.exists():
        return "utf-8"
    cpg = cpg_path.read_text(encoding="ascii", errors="ignore").strip()
    return "utf-8" if cpg.upper() in {"UTF-8", "65001"} else cpg or "utf-8"


def normalize_v6_row(row, geometry, source_layer):
    sys_id = row.get("SYS_ID", "")
    x_coord = row.get("X_COOR") or geometry.get("x", "")
    y_coord = row.get("Y_COOR") or geometry.get("y", "")
    output = {field: "" for field in CSV_FIELDS}
    output.update(
        {
            "NAME_PY": row.get("NAME_PY", ""),
            "NAME_CH": row.get("NAME_CH", ""),
            "NAME_FT": row.get("NAME_FT", ""),
            "ALIASES": "|".join(
                sorted(
                    {
                        value
                        for value in (row.get("NAME_PY", ""), row.get("NAME_CH", ""), row.get("NAME_FT", ""))
                        if value
                    }
                )
            ),
            "X_COOR": x_coord,
            "Y_COOR": y_coord,
            "PRES_LOC": row.get("PRES_LOC", "") or row.get("PERS_LOC", ""),
            "TYPE_PY": row.get("TYPE_PY", ""),
            "TYPE_CH": row.get("TYPE_CH", ""),
            "TYPE_EN": "",
            "LEV_RANK": row.get("LEV_RANK", ""),
            "BEG_YR": row.get("BEG_YR", ""),
            "BEG_RULE": row.get("BEG_RULE", ""),
            "END_YR": row.get("END_YR", ""),
            "END_RULE": row.get("END_RULE", ""),
            "NOTE_ID": row.get("NOTE_ID", ""),
            "OBJ_TYPE": row.get("OBJ_TYPE", ""),
            "SYS_ID": sys_id,
            "GEO_SRC": row.get("GEO_SRC", ""),
            "BEG_CHG_TY": row.get("BEG_CHG_TY", ""),
            "END_CHG_TY": row.get("END_CHG_TY", ""),
            "geometry": geometry.get("wkt", ""),
            "SOURCE_DATA": "CHGIS_V6_2021",
            "SOURCE_LAYER": source_layer,
            "V6_SYS_ID": sys_id,
            "HVD_ID": f"hvd_{sys_id}" if sys_id else "",
        }
    )
    return output


def read_v6_layer(v6_dir, layer_name, source_layer):
    layer_path = v6_dir / f"{layer_name}.shp"
    rows = read_dbf(layer_path.with_suffix(".dbf"), cpg_encoding(layer_path))
    geometries = read_shp_geometries(layer_path)
    if len(rows) != len(geometries):
        raise ValueError(f"{layer_name} DBF row count does not match SHP geometry count")
    return [normalize_v6_row(row, geometry, source_layer) for row, geometry in zip(rows, geometries)]


def build_v6_rows(v6_dir):
    rows = []
    for layer_name, source_layer in V6_POINT_LAYERS:
        rows.extend(read_v6_layer(v6_dir, layer_name, source_layer))
    rows.sort(key=sort_key)
    return rows


def tgaz_numeric_id(sys_id):
    if not sys_id.startswith("hvd_"):
        return ""
    return sys_id.removeprefix("hvd_")


def normalize_tgaz_supplement_row(row):
    output = {field: row.get(field, "") for field in CSV_FIELDS}
    output["SOURCE_DATA"] = "TGAZ_2018_SUPPLEMENT"
    output["SOURCE_LAYER"] = "tgaz_bak_2018"
    output["HVD_ID"] = row.get("SYS_ID", "")
    output["V6_SYS_ID"] = tgaz_numeric_id(row.get("SYS_ID", ""))
    return output


def build_tgaz_supplement_rows(sql_path, existing_v6_ids, include_low_rank=False):
    rows = []
    for row in build_tgaz_rows(sql_path):
        numeric_id = tgaz_numeric_id(row.get("SYS_ID", ""))
        if numeric_id in existing_v6_ids:
            continue
        try:
            lev_rank = int(row.get("LEV_RANK") or 0)
        except ValueError:
            lev_rank = 0
        if not include_low_rank and not (0 < lev_rank <= 6):
            continue
        rows.append(normalize_tgaz_supplement_row(row))
    rows.sort(key=sort_key)
    return rows


def sort_key(row):
    def as_int(value, default):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    return (
        as_int(row.get("LEV_RANK"), 999),
        row.get("NAME_FT") or row.get("NAME_CH") or row.get("NAME_PY") or "",
        as_int(row.get("BEG_YR"), 999999),
        row.get("SYS_ID", ""),
    )


def write_csv(rows, csv_path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_sqlite(rows, db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        columns = ", ".join(f"{field.lower()} TEXT" for field in CSV_FIELDS)
        conn.execute(f"CREATE TABLE chgis_place ({columns})")
        placeholders = ", ".join(f":{field}" for field in CSV_FIELDS)
        conn.executemany(f"INSERT INTO chgis_place VALUES ({placeholders})", rows)
        conn.execute("CREATE INDEX chgis_place_name_ft_idx ON chgis_place(name_ft)")
        conn.execute("CREATE INDEX chgis_place_name_ch_idx ON chgis_place(name_ch)")
        conn.execute("CREATE INDEX chgis_place_aliases_idx ON chgis_place(aliases)")
        conn.execute("CREATE INDEX chgis_place_year_idx ON chgis_place(beg_yr, end_yr)")
        conn.execute("CREATE INDEX chgis_place_lev_rank_idx ON chgis_place(lev_rank)")
        conn.execute("CREATE INDEX chgis_place_hvd_idx ON chgis_place(hvd_id)")


def main():
    parser = argparse.ArgumentParser(
        description="Export CHGIS v6 as the primary app gazetteer, with optional TGAZ supplement."
    )
    parser.add_argument("--v6-dir", type=Path, default=DEFAULT_V6_DIR)
    parser.add_argument("--tgaz-sql", type=Path, default=DEFAULT_SQL_PATH)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--v6-csv", type=Path, default=DEFAULT_V6_CSV_PATH)
    parser.add_argument("--supplement-csv", type=Path, default=DEFAULT_SUPPLEMENT_CSV_PATH)
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--no-tgaz-supplement",
        action="store_true",
        help="Write only the CHGIS v6 rows, with no TGAZ/HVD supplemental records.",
    )
    parser.add_argument(
        "--include-tgaz-low-rank",
        action="store_true",
        help="Include TGAZ supplemental rows below county level. Default is ranks 1-6 only.",
    )
    args = parser.parse_args()

    v6_rows = build_v6_rows(args.v6_dir)
    v6_ids = {row["V6_SYS_ID"] for row in v6_rows if row.get("V6_SYS_ID")}
    supplement_rows = []
    if not args.no_tgaz_supplement:
        supplement_rows = build_tgaz_supplement_rows(
            args.tgaz_sql,
            v6_ids,
            include_low_rank=args.include_tgaz_low_rank,
        )
    combined_rows = sorted(v6_rows + supplement_rows, key=sort_key)

    write_csv(v6_rows, args.v6_csv)
    write_csv(supplement_rows, args.supplement_csv)
    write_csv(combined_rows, args.csv)
    write_sqlite(combined_rows, args.sqlite)

    print(f"Wrote {len(v6_rows)} CHGIS v6 rows to {args.v6_csv}.")
    print(f"Wrote {len(supplement_rows)} TGAZ supplement rows to {args.supplement_csv}.")
    print(f"Wrote {len(combined_rows)} combined rows to {args.csv} and {args.sqlite}.")


if __name__ == "__main__":
    main()
