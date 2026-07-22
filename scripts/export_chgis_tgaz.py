import argparse
import csv
import sqlite3
from collections import defaultdict
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQL_PATH = (
    APP_ROOT.parent
    / "pre 2023 DH archive"
    / "DH work"
    / "CHGIS"
    / "tgaz_bak_2018"
    / "tgaz_bak_2018.sql"
)
DEFAULT_CSV_PATH = APP_ROOT / "data" / "CHGIS_tgaz_places.csv"
DEFAULT_DB_PATH = APP_ROOT / "data" / "chgis_tgaz.sqlite"

TARGET_TABLES = {"ftype", "placename", "spelling"}
CSV_FIELDS = [
    "NAME_PY",
    "NAME_CH",
    "NAME_FT",
    "ALIASES",
    "X_COOR",
    "Y_COOR",
    "PRES_LOC",
    "TYPE_PY",
    "TYPE_CH",
    "TYPE_EN",
    "LEV_RANK",
    "BEG_YR",
    "BEG_RULE",
    "END_YR",
    "END_RULE",
    "NOTE_ID",
    "OBJ_TYPE",
    "SYS_ID",
    "GEO_SRC",
    "BEG_CHG_TY",
    "END_CHG_TY",
    "geometry",
]


def parse_insert_values(value_text):
    rows = []
    row = None
    token = []
    in_string = False
    i = 0

    def finish_token():
        raw = "".join(token).strip()
        token.clear()
        if raw.upper() == "NULL":
            return None
        return raw

    while i < len(value_text):
        char = value_text[i]
        if in_string:
            if char == "\\" and i + 1 < len(value_text):
                nxt = value_text[i + 1]
                token.append({"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(nxt, nxt))
                i += 2
                continue
            if char == "'":
                if i + 1 < len(value_text) and value_text[i + 1] == "'":
                    token.append("'")
                    i += 2
                    continue
                in_string = False
                i += 1
                continue
            token.append(char)
            i += 1
            continue

        if char == "'":
            in_string = True
        elif char == "(":
            row = []
            token.clear()
        elif char == "," and row is not None:
            row.append(finish_token())
        elif char == ")" and row is not None:
            row.append(finish_token())
            rows.append(row)
            row = None
        elif char == ";" and row is None:
            break
        elif row is not None:
            token.append(char)
        i += 1
    return rows


def iter_table_rows(sql_path):
    with sql_path.open(encoding="utf-8", errors="replace") as sql_file:
        for line in sql_file:
            if not line.startswith("INSERT INTO `"):
                continue
            table = line.split("`", 2)[1]
            if table not in TARGET_TABLES:
                continue
            values = line.split(" VALUES ", 1)[1]
            for row in parse_insert_values(values):
                yield table, row


def choose_name(forms, *, prefer_traditional=False, prefer_transcription=False):
    if prefer_transcription:
        for form in forms:
            if form["trsys_id"] != "na":
                return form["written_form"]
        for form in forms:
            if not contains_cjk(form["written_form"]):
                return form["written_form"]
        return ""

    preferred_scripts = ("1", "2") if prefer_traditional else ("2", "1")
    for script_id in preferred_scripts:
        defaults = [
            form["written_form"]
            for form in forms
            if form["script_id"] == script_id and form["default_per_type"] == "1"
        ]
        if defaults:
            return defaults[0]
    for script_id in preferred_scripts:
        for form in forms:
            if form["script_id"] == script_id:
                return form["written_form"]
    for form in forms:
        if contains_cjk(form["written_form"]):
            return form["written_form"]
    return ""


def contains_cjk(value):
    return any("\u3400" <= char <= "\u9fff" for char in str(value or ""))


def clean_year(value):
    if value in (None, ""):
        return ""
    return str(value)


def point_wkt(x_coord, y_coord):
    if x_coord in (None, "") or y_coord in (None, ""):
        return ""
    return f"POINT ({x_coord} {y_coord})"


def build_rows(sql_path):
    ftypes = {}
    placenames = {}
    spellings = defaultdict(list)

    for table, row in iter_table_rows(sql_path):
        if table == "ftype":
            ftypes[row[0]] = {
                "name_vn": row[1] or "",
                "name_alt": row[2] or "",
                "name_tr": row[3] or "",
                "name_en": row[4] or "",
            }
        elif table == "placename":
            sys_id = row[1] or ""
            if not sys_id.startswith("hvd_") or row[3] != "CHGIS":
                continue
            placenames[row[0]] = row
        elif table == "spelling":
            placename_id = row[1]
            if placename_id in placenames:
                spellings[placename_id].append(
                    {
                        "script_id": row[2] or "",
                        "written_form": row[3] or "",
                        "trsys_id": row[5] or "",
                        "default_per_type": row[6] or "",
                    }
                )

    output_rows = []
    for placename_id, row in placenames.items():
        forms = spellings.get(placename_id, [])
        if not forms:
            continue
        ftype = ftypes.get(row[2], {})
        x_coord = row[14] or ""
        y_coord = row[15] or ""
        aliases = sorted({form["written_form"] for form in forms if form["written_form"]})
        output_rows.append(
            {
                "NAME_PY": choose_name(forms, prefer_transcription=True),
                "NAME_CH": choose_name(forms, prefer_traditional=False),
                "NAME_FT": choose_name(forms, prefer_traditional=True),
                "ALIASES": "|".join(aliases),
                "X_COOR": x_coord,
                "Y_COOR": y_coord,
                "PRES_LOC": "",
                "TYPE_PY": ftype.get("name_tr", ""),
                "TYPE_CH": ftype.get("name_vn", ""),
                "TYPE_EN": ftype.get("name_en", ""),
                "LEV_RANK": row[7] or "",
                "BEG_YR": clean_year(row[8]),
                "BEG_RULE": row[9] or "",
                "END_YR": clean_year(row[10]),
                "END_RULE": row[11] or "",
                "NOTE_ID": row[5] or "",
                "OBJ_TYPE": row[12] or "",
                "SYS_ID": row[1] or "",
                "GEO_SRC": row[16] or "",
                "BEG_CHG_TY": "",
                "END_CHG_TY": "",
                "geometry": point_wkt(x_coord, y_coord),
            }
        )

    output_rows.sort(key=lambda item: (item["LEV_RANK"], item["NAME_FT"], int(item["BEG_YR"] or 999999), item["SYS_ID"]))
    return output_rows


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
        conn.execute(
            """
            CREATE TABLE chgis_place (
                name_py TEXT,
                name_ch TEXT,
                name_ft TEXT,
                aliases TEXT,
                x_coor REAL,
                y_coor REAL,
                pres_loc TEXT,
                type_py TEXT,
                type_ch TEXT,
                type_en TEXT,
                lev_rank INTEGER,
                beg_yr INTEGER,
                beg_rule TEXT,
                end_yr INTEGER,
                end_rule TEXT,
                note_id TEXT,
                obj_type TEXT,
                sys_id TEXT PRIMARY KEY,
                geo_src TEXT,
                beg_chg_ty TEXT,
                end_chg_ty TEXT,
                geometry TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO chgis_place VALUES (
                :NAME_PY, :NAME_CH, :NAME_FT, :ALIASES, :X_COOR, :Y_COOR, :PRES_LOC,
                :TYPE_PY, :TYPE_CH, :TYPE_EN, :LEV_RANK, :BEG_YR, :BEG_RULE, :END_YR,
                :END_RULE, :NOTE_ID, :OBJ_TYPE, :SYS_ID, :GEO_SRC, :BEG_CHG_TY,
                :END_CHG_TY, :geometry
            )
            """,
            rows,
        )
        conn.execute("CREATE INDEX chgis_place_name_ft_idx ON chgis_place(name_ft)")
        conn.execute("CREATE INDEX chgis_place_name_ch_idx ON chgis_place(name_ch)")
        conn.execute("CREATE INDEX chgis_place_year_idx ON chgis_place(beg_yr, end_yr)")
        conn.execute("CREATE INDEX chgis_place_lev_rank_idx ON chgis_place(lev_rank)")


def main():
    parser = argparse.ArgumentParser(description="Export local CHGIS TGAZ MySQL dump to app-friendly CSV/SQLite.")
    parser.add_argument("--sql", type=Path, default=DEFAULT_SQL_PATH)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    rows = build_rows(args.sql)
    write_csv(rows, args.csv)
    write_sqlite(rows, args.sqlite)
    print(f"Wrote {len(rows)} CHGIS TGAZ rows to {args.csv} and {args.sqlite}.")


if __name__ == "__main__":
    main()
