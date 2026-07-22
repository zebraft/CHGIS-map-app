from flask import Flask, jsonify, redirect, render_template, request
import folium
from branca.element import MacroElement, Template
from folium.plugins import MarkerCluster
from markupsafe import Markup
import csv
import json
import os
import re
import hashlib
import pandas as pd
import logging
import requests
import sqlite3
import struct
from functools import lru_cache
from html import escape, unescape
from itertools import product
from pathlib import Path
from urllib.parse import quote, urlencode

try:
    from pyproj import CRS, Transformer
except ImportError:
    CRS = None
    Transformer = None


APP_ROOT = Path(__file__).resolve().parent


def load_env_file(path):
    if not path.exists():
        return

    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(APP_ROOT / '.env')
load_env_file(APP_ROOT.parent / '.env')


app = Flask(__name__)

CHGIS_PLACENAME_URL = 'https://chgis.hudci.org/tgaz/placename'
CHGIS_API_TIMEOUT = 12
CBDB_REIGN_TITLES_PATH = APP_ROOT / 'data' / 'cbdb_reign_titles.csv'
CHGIS_PLACES_DB_PATH = APP_ROOT / 'data' / 'chgis_places.sqlite'
UNIHAN_VARIANTS_PATH = APP_ROOT / 'Unihan_Variants.txt'
AI_PLACE_CACHE_PATH = APP_ROOT / 'data' / 'ai_place_cleanup_cache.json'
OPENAI_RESPONSES_URL = 'https://api.openai.com/v1/responses'
AI_PLACE_MODEL = os.environ.get('CHGIS_AI_PLACE_MODEL') or os.environ.get('OPENAI_MODEL') or 'gpt-5.4-mini'
AI_PLACE_PROMPT_VERSION = 'people-reign-cleanup-v2'
AI_PLACE_MAX_CANDIDATES = 60
AI_PLACE_CONTEXT_RADIUS = 24
AI_PLACE_CACHE_MAX_ENTRIES = 500
MAP_TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}'
PHYSICAL_MAP_TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Physical_Map/MapServer/tile/{z}/{y}/{x}'
TOPO_MAP_TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}'
MAP_ATTRIBUTION = 'Tiles &copy; Esri'
MAP_CENTER = [34.5, 109.0]
MAP_ZOOM_START = 6
MAP_FIT_MAX_ZOOM = 7
MAP_FIT_PADDING = (95, 95)
CHRONICLE_CHORONYM_FOLLOWERS = (
    '太守',
    '刺史',
    '牧',
    '縣令',
    '县令',
    '令',
    '郡守',
    '都督',
    '節度使',
    '节度使',
    '防禦使',
    '防御使',
    '觀察使',
    '观察使',
    '採訪使',
    '采访使',
    '安撫使',
    '安抚使',
    '轉運使',
    '转运使',
    '留後',
    '留后',
    '元帥',
    '元帅',
    '行軍',
    '行军',
    '王',
    '公',
    '侯',
    '伯',
    '子',
    '男',
    '公主',
)
CLUSTER_DISABLE_AT_ZOOM = 13
MAP_MAX_ZOOM = CLUSTER_DISABLE_AT_ZOOM
BASEMAP_SWITCH_ZOOM = 8
CHGIS_V6_DIR = APP_ROOT / 'data' / 'CHGISv6-2021'
CHGIS_PREFECTURE_POLYGON_LAYER = CHGIS_V6_DIR / 'v6_time_pref_pgn_utf_wgs84.shp'
CHGIS_1820_PROVINCE_POLYGON_LAYER = CHGIS_V6_DIR / 'v6_1820_prov_pgn_utf.shp'
CHGIS_1820_LAKE_POLYGON_LAYER = CHGIS_V6_DIR / 'v6_1820_lks_pgn_utf.shp'
CHGIS_1820_RIVER_LINE_LAYER = CHGIS_V6_DIR / 'v6_1820_coded_rvr_lin_utf.shp'
POLYGON_BOUNDS_BUFFER = 1.25
MAX_POLYGON_FEATURES = 350
MAX_POLYGON_RING_POINTS = 100
MAX_BASE_RING_POINTS = 90
MAX_BASE_LINE_POINTS = 55
MIN_TEXT_MATCH_LENGTH = 2
MAX_RENDER_ROWS = 1000
MATCH_SOURCE_CHORONYM = 'choronym'
SINGLE_POINT_BOUNDS_BUFFER = 0.75
AUTHORITY_SEARCH_LIMIT = 20
AUTHORITY_SEARCH_MAX_QUERY_CHARS = 12
AUTHORITY_NAME_SUFFIX_CHARS = 2
ADMIN_SUFFIXES = (
    '直隸州',
    '長官司',
    '侯國',
    '郡',
    '縣',
    '县',
    '州',
    '國',
    '国',
    '軍',
    '军',
    '府',
    '廳',
    '厅',
    '衛',
    '卫',
)
REIGN_YEAR_PATTERN = re.compile(r"^[0-9〇零一二三四五六七八九十百廿卅元]+年")
REIGN_DATE_SUFFIX_PATTERN = re.compile(r"([0-9〇零一二三四五六七八九十百廿卅元]+年|初|中|末|間|间)")
PRINCE_TITLE_SPLIT_PATTERNS = (
    re.compile(r"[\u3400-\u9fff]{1,4}王法曹"),
    re.compile(r"[\u3400-\u9fff]{1,4}王令使"),
    re.compile(r"[\u3400-\u9fff]{1,4}王友"),
    re.compile(r"[\u3400-\u9fff]{1,4}王俊"),
)
FIEF_TITLE_SUFFIXES = ('王', '公', '侯')
CHRONICLE_AUDIT_FAILURE_CATEGORIES = (
    '',
    'source_gap',
    'variant_gap',
    'chronology_filter',
    'boundary_error',
    'office_as_person',
    'place_as_person',
    'common_word_as_entity',
    'place_inside_office_title',
    'fief_or_title_person',
    'short_name_or_abbrev',
    'kinship_context',
    'ambiguous_authority',
    'wrong_place_level',
    'bad_source_text',
    'uncertain',
)
CHRONICLE_AUDIT_STATUSES = ('unreviewed', 'reviewed', 'needs_ai', 'needs_data', 'deferred')


# Create a logger instance
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(levelname)s - %(message)s')

# Create a file handler and set the formatter
if not logger.handlers:
    file_handler = logging.FileHandler('error.log')
    file_handler.setLevel(logging.ERROR)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


# Landing page route
@app.route("/", methods=["GET", "POST"])
def landing_page():
    if request.method == "POST":
        return render_template("CHGIS_index.html", form_data=request.form)
    return render_template("CHGIS_index.html", form_data={})


@app.route("/CHGIS_plain_base.geojson", methods=["GET"])
def chgis_plain_base_geojson():
    return jsonify(chgis_plain_base_features())


def char_from_unihan_codepoint(value):
    codepoint = value.split("<", 1)[0]
    if not codepoint.startswith("U+"):
        return ""
    try:
        return chr(int(codepoint[2:], 16))
    except ValueError:
        return ""


@lru_cache(maxsize=1)
def traditional_display_translation():
    mapping = {
        '会': '會',
        '华': '華',
        '𥡴': '稽',
        '济': '濟',
        '颖': '潁',
        '颍': '潁',
        '钟': '鍾',
        '锺': '鍾',
        '台': '臺',
        '隶': '隸',
        '县': '縣',
        '国': '國',
        '东': '東',
        '汉': '漢',
        '晋': '晉',
        '魏': '魏',
        '梁': '梁',
        '宋': '宋',
        '朝': '朝',
        '官': '官',
        '员': '員',
    }
    if UNIHAN_VARIANTS_PATH.exists():
        with UNIHAN_VARIANTS_PATH.open(encoding='utf-8') as input_file:
            for line in input_file:
                if not line or line.startswith('#'):
                    continue
                parts = line.rstrip('\n').split('\t')
                if len(parts) != 3 or parts[1] != 'kTraditionalVariant':
                    continue
                source = char_from_unihan_codepoint(parts[0])
                target = char_from_unihan_codepoint(parts[2].split()[0])
                if source and target:
                    mapping.setdefault(source, target)
    return str.maketrans(mapping)


def traditional_display(value):
    if value is None:
        return ''
    return str(value).translate(traditional_display_translation())


def shapefile_cpg_encoding(layer_path):
    cpg_path = layer_path.with_suffix('.cpg')
    if not cpg_path.exists():
        return 'utf-8'
    cpg = cpg_path.read_text(encoding='ascii', errors='ignore').strip()
    return 'utf-8' if cpg.upper() in {'UTF-8', '65001'} else cpg or 'utf-8'


def decode_dbf_value(raw, field, encoding):
    text = raw.decode(encoding, errors='replace').strip()
    if not text:
        return ''
    return text


def read_dbf_rows(dbf_path, encoding='utf-8'):
    with dbf_path.open('rb') as dbf:
        header = dbf.read(32)
        record_count = struct.unpack('<I', header[4:8])[0]
        header_length = struct.unpack('<H', header[8:10])[0]
        record_length = struct.unpack('<H', header[10:12])[0]
        fields = []
        while True:
            descriptor = dbf.read(32)
            if descriptor[0] == 0x0D:
                break
            fields.append({
                'name': descriptor[:11].split(b'\x00', 1)[0].decode('ascii', errors='replace'),
                'length': descriptor[16],
            })

        dbf.seek(header_length)
        rows = []
        for _ in range(record_count):
            record = dbf.read(record_length)
            if not record or record[0:1] == b'*':
                continue
            offset = 1
            row = {}
            for field in fields:
                raw = record[offset:offset + field['length']]
                offset += field['length']
                row[field['name']] = decode_dbf_value(raw, field, encoding)
            rows.append(row)
        return rows


def simplify_ring(points, max_points=MAX_POLYGON_RING_POINTS):
    if len(points) <= max_points:
        return points
    step = max(1, len(points) // max_points)
    simplified = points[::step]
    if simplified[-1] != points[-1]:
        simplified.append(points[-1])
    if simplified[0] != simplified[-1]:
        simplified.append(simplified[0])
    return simplified


def simplify_points(points, max_points=MAX_BASE_LINE_POINTS):
    if len(points) <= max_points:
        return points
    step = max(1, len(points) // max_points)
    simplified = points[::step]
    if simplified[-1] != points[-1]:
        simplified.append(points[-1])
    return simplified


@lru_cache(maxsize=8)
def transformer_for_shapefile(shp_path):
    if CRS is None or Transformer is None:
        return None
    prj_path = Path(shp_path).with_suffix('.prj')
    if not prj_path.exists():
        return None
    try:
        source_crs = CRS.from_wkt(prj_path.read_text(encoding='utf-8', errors='ignore'))
        return Transformer.from_crs(source_crs, 'EPSG:4326', always_xy=True)
    except Exception as exc:
        logger.warning("Could not create transformer for %s: %s", shp_path, exc)
        return None


def transform_xy(point, transformer=None):
    if transformer is None:
        return point
    longitude, latitude = transformer.transform(point[0], point[1])
    return [longitude, latitude]


def read_polygon_geometries(shp_path, transformer=None, max_points=MAX_POLYGON_RING_POINTS):
    geometries = []
    with shp_path.open('rb') as shp:
        shp.seek(100)
        while True:
            record_header = shp.read(8)
            if not record_header:
                break
            if len(record_header) != 8:
                raise ValueError(f"Malformed shapefile record header in {shp_path}")
            _record_number, content_length_words = struct.unpack('>2i', record_header)
            content = shp.read(content_length_words * 2)
            if len(content) < 44:
                geometries.append(None)
                continue
            shape_type = struct.unpack('<i', content[:4])[0]
            if shape_type not in {5, 15, 25}:
                geometries.append(None)
                continue
            min_x, min_y, max_x, max_y = struct.unpack('<4d', content[4:36])
            num_parts, num_points = struct.unpack('<2i', content[36:44])
            parts_start = 44
            points_start = parts_start + (num_parts * 4)
            if len(content) < points_start + (num_points * 16):
                geometries.append(None)
                continue
            parts = list(struct.unpack(f'<{num_parts}i', content[parts_start:points_start]))
            point_values = struct.unpack(f'<{num_points * 2}d', content[points_start:points_start + (num_points * 16)])
            points = [
                transform_xy([point_values[index], point_values[index + 1]], transformer)
                for index in range(0, len(point_values), 2)
            ]
            rings = []
            for index, start in enumerate(parts):
                end = parts[index + 1] if index + 1 < len(parts) else len(points)
                ring = points[start:end]
                if len(ring) >= 4:
                    rings.append(simplify_ring(ring, max_points=max_points))
            geometries.append({
                'bbox': [min_x, min_y, max_x, max_y],
                'coordinates': rings,
            })
    return geometries


def read_polyline_geometries(shp_path, transformer=None, max_points=MAX_BASE_LINE_POINTS):
    geometries = []
    with shp_path.open('rb') as shp:
        shp.seek(100)
        while True:
            record_header = shp.read(8)
            if not record_header:
                break
            if len(record_header) != 8:
                raise ValueError(f"Malformed shapefile record header in {shp_path}")
            _record_number, content_length_words = struct.unpack('>2i', record_header)
            content = shp.read(content_length_words * 2)
            if len(content) < 44:
                geometries.append(None)
                continue
            shape_type = struct.unpack('<i', content[:4])[0]
            if shape_type not in {3, 13, 23}:
                geometries.append(None)
                continue
            min_x, min_y, max_x, max_y = struct.unpack('<4d', content[4:36])
            num_parts, num_points = struct.unpack('<2i', content[36:44])
            parts_start = 44
            points_start = parts_start + (num_parts * 4)
            if len(content) < points_start + (num_points * 16):
                geometries.append(None)
                continue
            parts = list(struct.unpack(f'<{num_parts}i', content[parts_start:points_start]))
            point_values = struct.unpack(f'<{num_points * 2}d', content[points_start:points_start + (num_points * 16)])
            points = [
                transform_xy([point_values[index], point_values[index + 1]], transformer)
                for index in range(0, len(point_values), 2)
            ]
            lines = []
            for index, start in enumerate(parts):
                end = parts[index + 1] if index + 1 < len(parts) else len(points)
                line = points[start:end]
                if len(line) >= 2:
                    lines.append(simplify_points(line, max_points=max_points))
            geometries.append({
                'bbox': [min_x, min_y, max_x, max_y],
                'coordinates': lines,
            })
    return geometries


def safe_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def row_active_in_date_filter(row, date_filter):
    if not date_filter:
        return False
    begin_filter, end_filter = date_filter
    begin_year = safe_int(row.get('BEG_YR'))
    end_year = safe_int(row.get('END_YR'))
    if begin_year is None or end_year is None:
        return False
    return begin_year <= end_filter and end_year >= begin_filter


def bounds_intersect(first, second):
    if not first or not second:
        return True
    first_west, first_south, first_east, first_north = first
    second_west, second_south, second_east, second_north = second
    return not (
        first_east < second_west
        or first_west > second_east
        or first_north < second_south
        or first_south > second_north
    )


def leaflet_bounds_to_bbox(bounds):
    if not bounds:
        return None
    (south, west), (north, east) = bounds
    return [
        west - POLYGON_BOUNDS_BUFFER,
        south - POLYGON_BOUNDS_BUFFER,
        east + POLYGON_BOUNDS_BUFFER,
        north + POLYGON_BOUNDS_BUFFER,
    ]


@lru_cache(maxsize=1)
def prefecture_polygon_features():
    shp_path = CHGIS_PREFECTURE_POLYGON_LAYER
    if not shp_path.exists():
        logger.warning("CHGIS prefecture polygon layer not found: %s", shp_path)
        return []
    rows = read_dbf_rows(shp_path.with_suffix('.dbf'), shapefile_cpg_encoding(shp_path))
    geometries = read_polygon_geometries(shp_path)
    features = []
    for row, geometry in zip(rows, geometries):
        if not geometry or not geometry.get('coordinates'):
            continue
        features.append({
            'type': 'Feature',
            'bbox': geometry['bbox'],
            'properties': {
                'polygon_id': "|".join(
                    str(value or '')
                    for value in (
                        row.get('SYS_ID'),
                        row.get('NAME_FT') or row.get('NAME_CH'),
                        row.get('BEG_YR'),
                        row.get('END_YR'),
                    )
                ),
                'name': row.get('NAME_FT') or row.get('NAME_CH') or '',
                'type': row.get('TYPE_CH') or '',
                'sys_id': row.get('SYS_ID') or '',
                'begin_year': safe_int(row.get('BEG_YR')),
                'end_year': safe_int(row.get('END_YR')),
            },
            'geometry': {
                'type': 'Polygon',
                'coordinates': geometry['coordinates'],
            },
        })
    return features


def bbox_contains_location(bbox, location):
    if not bbox or not location:
        return False
    west, south, east, north = bbox
    latitude, longitude = location
    return (
        west - POLYGON_BOUNDS_BUFFER <= longitude <= east + POLYGON_BOUNDS_BUFFER
        and south - POLYGON_BOUNDS_BUFFER <= latitude <= north + POLYGON_BOUNDS_BUFFER
    )


def year_relevant_prefecture_polygons(date_filter, map_bounds=None, locations=None):
    if not date_filter:
        return []
    bbox = leaflet_bounds_to_bbox(map_bounds)
    locations = list(locations or [])
    use_point_context = 0 < len(locations) <= 25
    features = [
        feature
        for feature in prefecture_polygon_features()
        if row_active_in_date_filter(
            {
                'BEG_YR': feature['properties'].get('begin_year'),
                'END_YR': feature['properties'].get('end_year'),
            },
            date_filter,
        )
        and (
            any(bbox_contains_location(feature.get('bbox'), location) for location in locations)
            if use_point_context
            else bounds_intersect(feature.get('bbox'), bbox)
        )
    ]
    return features[:MAX_POLYGON_FEATURES]


def polygon_features_for_map_data(map_data, date_filter):
    if map_data.empty:
        return []
    grouped_records = grouped_location_records(map_data)
    locations = [location for location, _records in grouped_records]
    bounds = map_bounds_for_locations(locations)
    return year_relevant_prefecture_polygons(date_filter, bounds, locations=locations)


def polygon_selection_records(features):
    records = []
    for feature in features:
        props = feature.get('properties', {})
        records.append({
            'id': props.get('polygon_id') or '',
            'name': props.get('name') or '',
            'type': props.get('type') or '',
            'sys_id': props.get('sys_id') or '',
            'begin_year': props.get('begin_year') or '',
            'end_year': props.get('end_year') or '',
        })
    return sorted(
        records,
        key=lambda record: (
            str(record['name']),
            str(record['type']),
            record['begin_year'] if isinstance(record['begin_year'], int) else 0,
            record['end_year'] if isinstance(record['end_year'], int) else 0,
            str(record['sys_id']),
        ),
    )


def feature_name(row):
    for column in ('NAME_FT', 'NAME_CH', 'NAME', 'NAME_EN', 'H_CHINPROV', 'H_PROVINCE'):
        value = row.get(column)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def polygon_features_from_layer(layer_path, feature_kind, max_points=MAX_BASE_RING_POINTS):
    if not layer_path.exists():
        logger.warning("CHGIS base polygon layer not found: %s", layer_path)
        return []
    transformer = transformer_for_shapefile(str(layer_path))
    if transformer is None:
        logger.warning("Skipping projected CHGIS base layer without pyproj transformer: %s", layer_path)
        return []
    rows = read_dbf_rows(layer_path.with_suffix('.dbf'), shapefile_cpg_encoding(layer_path))
    geometries = read_polygon_geometries(layer_path, transformer=transformer, max_points=max_points)
    features = []
    for row, geometry in zip(rows, geometries):
        if not geometry or not geometry.get('coordinates'):
            continue
        features.append({
            'type': 'Feature',
            'properties': {
                'name': feature_name(row),
                'kind': feature_kind,
            },
            'geometry': {
                'type': 'Polygon',
                'coordinates': geometry['coordinates'],
            },
        })
    return features


def line_features_from_layer(layer_path, feature_kind, max_points=MAX_BASE_LINE_POINTS):
    if not layer_path.exists():
        logger.warning("CHGIS base line layer not found: %s", layer_path)
        return []
    transformer = transformer_for_shapefile(str(layer_path))
    if transformer is None:
        logger.warning("Skipping projected CHGIS base layer without pyproj transformer: %s", layer_path)
        return []
    rows = read_dbf_rows(layer_path.with_suffix('.dbf'), shapefile_cpg_encoding(layer_path))
    geometries = read_polyline_geometries(layer_path, transformer=transformer, max_points=max_points)
    features = []
    for row, geometry in zip(rows, geometries):
        if not geometry or not geometry.get('coordinates'):
            continue
        coordinates = geometry['coordinates']
        features.append({
            'type': 'Feature',
            'properties': {
                'name': feature_name(row),
                'kind': feature_kind,
            },
            'geometry': {
                'type': 'MultiLineString' if len(coordinates) > 1 else 'LineString',
                'coordinates': coordinates if len(coordinates) > 1 else coordinates[0],
            },
        })
    return features


@lru_cache(maxsize=1)
def chgis_plain_base_features():
    return {
        'province_boundaries': polygon_features_from_layer(CHGIS_1820_PROVINCE_POLYGON_LAYER, 'province'),
        'lakes': polygon_features_from_layer(CHGIS_1820_LAKE_POLYGON_LAYER, 'lake'),
        'rivers': line_features_from_layer(CHGIS_1820_RIVER_LINE_LAYER, 'river'),
    }


# Map page route
@app.route("/CHGIS_map", methods=["GET", "POST"])
def chgis_map():
    if request.method == 'POST':
        # Retrieve user input from the form
        place_names = request.form.get('place_names')
        source_text = request.form.get('source_text')
        date = request.form.get('date')
        date_range = request.form.get('date_range')
        prefectures = request.form.get('prefectures')
        counties = request.form.get('counties')
        style_options_submitted = bool(request.form.get('style_options_submitted'))
        label_markers = bool(request.form.get('label_markers')) if style_options_submitted else True
        submitted_selected_records = request.form.getlist('selected_records')
        selected_records_state = request.form.get('selected_records_state')
        detected_selection_submitted = bool(request.form.get('detected_selection_submitted'))
        if selected_records_state is not None:
            try:
                selected_records_payload = json.loads(selected_records_state)
                if isinstance(selected_records_payload, list):
                    submitted_selected_records = [str(record_id) for record_id in selected_records_payload]
                    detected_selection_submitted = True
            except json.JSONDecodeError:
                logger.warning("Ignoring malformed selected_records_state payload")

        logger.info(
            "User input: place_names=%s source_text_length=%s date=%s date_range=%s prefectures=%s counties=%s",
            place_names,
            len(source_text or ''),
            date,
            date_range,
            prefectures,
            counties,
        )

        date_filter, date_error = parse_date_filter(date, date_range)
        if date_error:
            return render_template(
                "CHGIS_index.html",
                form_data=request.form,
                error_message=date_error,
            )

        matched_dates = extract_reign_dates(source_text)
        matched_dates = dated_timeline_matches(matched_dates)
        active_date_filter = date_filter or inferred_date_filter(matched_dates)
        matched_places = extract_place_names(source_text, prefectures, counties)
        contextual_markers = extract_contextual_markers(source_text, matched_places)
        matched_places, ai_cleanup = ai_cleaned_place_matches(
            source_text,
            matched_places,
            matched_dates,
            [],
            contextual_markers,
            include_contextual_locations=False,
        )
        allow_date_only = not (source_text or '').strip() and not split_place_names(place_names)
        date_only_candidate_data = pd.DataFrame()
        if allow_date_only and active_date_filter:
            date_only_candidate_data = filter_data(
                '',
                active_date_filter,
                prefectures,
                counties,
                allow_date_only=True,
            )
            candidate_data = date_only_candidate_data
        else:
            candidate_query_names = combine_place_names('', [place['name'] for place in matched_places])
            candidate_data = filter_data(
                candidate_query_names,
                active_date_filter,
                prefectures,
                counties,
                allow_date_only=False,
            )
        selected_record_ids = selected_detected_record_ids(
            candidate_data,
            submitted_selected_records,
            detected_selection_submitted,
        )
        selected_extracted_names = (
            []
            if not date_only_candidate_data.empty
            else selected_names_for_records(candidate_data, selected_record_ids)
        )
        matched_places = annotate_matched_places(matched_places, candidate_data, selected_record_ids)
        matched_places = [place for place in matched_places if place['result_count'] > 0]
        query_names = combine_place_names(place_names, selected_extracted_names)

        # Process the user input and generate the map
        filtered_data = filter_data(query_names, active_date_filter, prefectures, counties, allow_date_only=allow_date_only)
        filtered_data = apply_detected_record_selection(
            filtered_data,
            candidate_data,
            selected_record_ids,
            split_place_names(place_names),
        )
        filtered_data = tag_choronym_rows(filtered_data, matched_places)
        mappable_data, unmappable_rows = split_mappable_rows(filtered_data)
        display_matched_places = matched_places
        if not display_matched_places and split_place_names(place_names):
            display_matched_places = manual_matched_places_from_data(place_names, filtered_data)
        date_only_records = date_only_place_records(date_only_candidate_data, selected_record_ids)

        result_warning = result_size_warning(mappable_data, len(unmappable_rows))
        map_data = mappable_data.head(MAX_RENDER_ROWS)
        polygon_features = polygon_features_for_map_data(map_data, active_date_filter)

        generated_map = generate_map(
            map_data,
            date_filter=active_date_filter,
            polygon_features=polygon_features,
            label_markers=label_markers,
            fit_bounds=True,
            fit_bounds_padding=MAP_FIT_PADDING,
            fit_bounds_max_zoom=MAP_FIT_MAX_ZOOM,
        )

        # Convert the map to html string
        map_html = generated_map.get_root().render()

        # Pass the map HTML to the template
        template_data = {
            'map_data': map_html,
            'matched_places': display_matched_places,
            'date_only_records': date_only_records,
            'date_only_record_count': len(date_only_records),
            'selected_record_count': len(selected_record_ids),
            'rendered_record_count': len(map_data),
            'polygon_records': polygon_selection_records(polygon_features),
            'matched_dates': matched_dates,
            'ai_cleanup': ai_cleanup,
            'highlighted_source_text': highlighted_source_text(
                source_text,
                matched_places,
                matched_dates,
                contextual_markers=contextual_markers,
            ),
            'unmappable_places': rows_to_place_records(unmappable_rows),
            'result_warning': result_warning,
            'form_data': request.form,
        }

        return render_template('CHGIS_map.html', **template_data)

    # Handle GET requests by rendering the index page
    return render_template('CHGIS_index.html', form_data={})


def load_chgis_data():
    combined_path = Path('data/CHGIS_places.csv')
    if combined_path.exists():
        data = pd.read_csv(combined_path, dtype={'SYS_ID': str, 'LEV_RANK': str, 'V6_SYS_ID': str, 'HVD_ID': str})
        data['LEV_RANK'] = pd.to_numeric(data['LEV_RANK'], errors='coerce').fillna(0).astype(int)
        for column in ('X_COOR', 'Y_COOR', 'BEG_YR', 'END_YR'):
            data[column] = pd.to_numeric(data[column], errors='coerce')
        upper_units = data[(data['LEV_RANK'] > 0) & (data['LEV_RANK'] < 6)].copy()
        county_units = data[data['LEV_RANK'] >= 6].copy()
        logger.info(
            "Loaded CHGIS combined v6 gazetteer: %s upper-level rows, %s point-level rows",
            len(upper_units),
            len(county_units),
        )
        return upper_units, county_units

    tgaz_path = Path('data/CHGIS_tgaz_places.csv')
    if tgaz_path.exists():
        data = pd.read_csv(tgaz_path, dtype={'SYS_ID': str, 'LEV_RANK': str})
        data['LEV_RANK'] = pd.to_numeric(data['LEV_RANK'], errors='coerce').fillna(0).astype(int)
        for column in ('BEG_YR', 'END_YR', 'X_COOR', 'Y_COOR'):
            data[column] = pd.to_numeric(data[column], errors='coerce')
        upper_units = data[(data['LEV_RANK'] > 0) & (data['LEV_RANK'] < 6)].copy()
        county_units = data[data['LEV_RANK'] >= 6].copy()
        logger.info(
            "Loaded CHGIS TGAZ export: %s upper-level rows, %s point-level rows",
            upper_units.shape[0],
            county_units.shape[0],
        )
        return upper_units, county_units

    return pd.read_csv('data/CHGIS_prefectures.csv'), pd.read_csv('data/CHGIS_counties.csv')


# Load the datasets
prefectures_df, counties_df = load_chgis_data()


def chgis_record_id(row):
    return "|".join(
        str(row.get(column, ''))
        for column in ('LEV_RANK', 'SYS_ID', 'NAME_FT', 'BEG_YR', 'END_YR')
    )


def add_record_ids(dataframe):
    dataframe = dataframe.copy()
    dataframe['RECORD_ID'] = dataframe.apply(chgis_record_id, axis=1)
    return dataframe


prefectures_df = add_record_ids(prefectures_df)
counties_df = add_record_ids(counties_df)


def selected_dataframes(prefectures, counties):
    dataframes = []
    if prefectures:
        dataframes.append(prefectures_df)
    if counties:
        dataframes.append(counties_df)
    return dataframes


def selected_layer_key(prefectures, counties):
    return bool(prefectures), bool(counties)


def parse_date_filter(date, date_range):
    date = (date or '').strip()
    date_range = (date_range or '').strip()

    if date and date_range:
        return None, "Use either a single date or a date range, not both."

    if date:
        try:
            year = int(date)
        except ValueError:
            return None, "Date must be a whole year, for example 1200."
        return (year, year), None

    if date_range:
        date_parts = [part.strip() for part in re.split(r",|，", date_range) if part.strip()]
        if len(date_parts) != 2:
            return None, "Date range must have two years separated by a comma, for example 1100,1300."

        try:
            begin_date = int(date_parts[0])
            end_date = int(date_parts[1])
        except ValueError:
            return None, "Date range years must be whole numbers."

        if begin_date > end_date:
            return None, "Date range must begin before it ends."

        return (begin_date, end_date), None

    return None, None


def split_place_names(place_names):
    if not place_names:
        return []

    pattern = r",|，"
    return [name.strip() for name in re.split(pattern, place_names) if name.strip()]


def combine_place_names(place_names, extracted_names):
    names = split_place_names(place_names)
    names.extend(name for name in extracted_names if name)
    return "，".join(dict.fromkeys(names))


def manual_place_matches(place_names):
    return [
        {
            'name': name,
            'count': 1,
            'variants': [name],
            'alias_variants': [],
            'variant_display': name,
            'alias_variant_display': '',
            'matched_by_alias': False,
            'first_start': 0,
        }
        for name in split_place_names(place_names)
    ]


def selected_detected_names(matched_places, submitted_selected_places, detected_selection_submitted=False):
    matched_names = [place['name'] for place in matched_places]
    if not matched_names:
        return []

    if detected_selection_submitted:
        selected_names = set(submitted_selected_places)
        return [name for name in matched_names if name in selected_names]

    return matched_names


def selected_detected_record_ids(candidate_data, submitted_selected_records, detected_selection_submitted=False):
    if candidate_data.empty:
        return set()

    candidate_record_ids = set(candidate_data['RECORD_ID'].astype(str))
    if detected_selection_submitted:
        submitted_record_ids = set(submitted_selected_records)
        return candidate_record_ids & submitted_record_ids

    return candidate_record_ids


def selected_names_for_records(candidate_data, selected_record_ids):
    if candidate_data.empty or not selected_record_ids:
        return []

    selected_rows = candidate_data[candidate_data['RECORD_ID'].astype(str).isin(selected_record_ids)]
    return list(dict.fromkeys(selected_rows['NAME_FT'].dropna().astype(str)))


def apply_detected_record_selection(filtered_data, candidate_data, selected_record_ids, manual_names):
    if filtered_data.empty:
        return filtered_data

    candidate_record_ids = set(candidate_data['RECORD_ID'].astype(str)) if not candidate_data.empty else set()
    selected_record_ids = set(selected_record_ids)

    def keep_row(row):
        record_id = str(row.get('RECORD_ID'))
        if record_id in candidate_record_ids:
            return record_id in selected_record_ids
        if manual_names and row_matches_place_queries(row, manual_names):
            return True
        return True

    return filtered_data[filtered_data.apply(keep_row, axis=1)]


def tag_choronym_rows(filtered_data, matched_places):
    if filtered_data.empty:
        return filtered_data

    choronym_names = {place['name'] for place in matched_places or []}
    if not choronym_names:
        return filtered_data

    tagged_data = filtered_data.copy()
    if 'MATCH_SOURCE' not in tagged_data.columns:
        tagged_data['MATCH_SOURCE'] = ''
    tagged_data.loc[tagged_data['NAME_FT'].isin(choronym_names), 'MATCH_SOURCE'] = MATCH_SOURCE_CHORONYM
    return tagged_data


def alias_only_match(place):
    variants = set(place.get('variants', []))
    alias_variants = set(place.get('alias_variants', []))
    return bool(alias_variants) and variants == alias_variants


def place_highlight_matches(source_text, matched_places):
    matches = []
    occupied_ranges = []
    variant_places = {}
    for place in matched_places:
        for variant in place.get('variants', []):
            variant_places.setdefault(variant, set()).add(place['name'])

    variants = sorted(variant_places.keys(), key=lambda variant: (-len(variant), variant))
    for variant in variants:
        for match in re.finditer(re.escape(variant), source_text):
            match_range = match.span()
            if followed_by_reign_year(source_text, match_range[1]):
                continue
            if any(ranges_overlap(match_range, occupied) for occupied in occupied_ranges):
                continue
            occupied_ranges.append(match_range)
            matches.append({
                'start': match_range[0],
                'end': match_range[1],
                'kind': 'choronym',
                'places': sorted(variant_places[variant]),
            })

    return matches


def extract_contextual_markers(source_text, matched_places):
    if not source_text:
        return []

    markers = []
    variants = sorted(
        {
            variant
            for place in matched_places
            for variant in place.get('variants', [])
            if variant
        },
        key=lambda variant: (-len(variant), variant),
    )
    for variant in variants:
        for match in re.finditer(re.escape(variant), source_text):
            suffix = source_text[match.end():match.end() + 1]
            if suffix in FIEF_TITLE_SUFFIXES:
                markers.append({
                    'start': match.start(),
                    'end': match.end() + 1,
                    'kind': 'fief_title',
                    'variant': variant,
                    'text': source_text[match.start():match.end() + 1],
                })

    markers.sort(key=lambda marker: (marker['start'], -(marker['end'] - marker['start']), marker['variant']))
    filtered_markers = []
    occupied_ranges = []
    for marker in markers:
        marker_range = (marker['start'], marker['end'])
        if any(ranges_overlap(marker_range, occupied) for occupied in occupied_ranges):
            continue
        occupied_ranges.append(marker_range)
        filtered_markers.append(marker)
    return filtered_markers


def contextual_marker_ranges(contextual_markers):
    return [
        (marker['start'], marker['end'])
        for marker in contextual_markers or []
    ]


def contextual_highlight_matches(contextual_markers):
    return [
        {
            'start': marker['start'],
            'end': marker['end'],
            'kind': marker['kind'],
            'variant': marker.get('variant', ''),
            'text': marker.get('text', ''),
        }
        for marker in contextual_markers or []
    ]


def date_highlight_matches(matched_dates):
    return [
        {
            'start': match['start'],
            'end': match['end'],
            'kind': 'date',
            'date_index': index,
            'title': match['title'],
        }
        for index, match in enumerate(matched_dates, start=1)
    ]


def highlighted_source_text(source_text, matched_places, matched_dates=None, _matched_people=None, contextual_markers=None):
    if not source_text:
        return ''

    matched_dates = matched_dates or []
    contextual_markers = contextual_markers or []
    matches = []
    occupied_ranges = []
    candidates = sorted(
        date_highlight_matches(matched_dates)
        + contextual_highlight_matches(contextual_markers)
        + place_highlight_matches(source_text, matched_places),
        key=lambda item: (
            item['start'],
            {'date': 0, 'fief_title': 1, 'choronym': 2, 'place': 3}[item['kind']],
            -(item['end'] - item['start']),
        ),
    )
    for match in candidates:
        match_range = (match['start'], match['end'])
        if any(ranges_overlap(match_range, occupied) for occupied in occupied_ranges):
            continue
        occupied_ranges.append(match_range)
        matches.append(match)

    if not matches:
        return escape(source_text)

    chunks = []
    cursor = 0
    for match in sorted(matches, key=lambda item: item['start']):
        chunks.append(escape(source_text[cursor:match['start']]))
        if match['kind'] == 'date':
            chunks.append(
                "<mark class='source-date' tabindex='0' title='Show detected date in list' data-date-index='"
                + escape(str(match['date_index']), quote=True)
                + "'>"
                + escape(source_text[match['start']:match['end']])
                + "</mark>"
            )
        elif match['kind'] == 'fief_title':
            chunks.append(
                "<mark class='source-fief-title' title='Fief title'>"
                + escape(source_text[match['start']:match['end']])
                + "</mark>"
            )
        else:
            place_names = "|".join(match['places'])
            primary_place = match['places'][0] if match['places'] else ''
            chunks.append(
                "<mark class='source-place' data-marker='choronym' tabindex='0' title='Show detected choronym in list' data-place-names='"
                + escape(place_names, quote=True)
                + "' data-primary-place='"
                + escape(primary_place, quote=True)
                + "'>"
                + escape(source_text[match['start']:match['end']])
                + "</mark>"
            )
        cursor = match['end']
    chunks.append(escape(source_text[cursor:]))

    return Markup("".join(chunks))


@lru_cache(maxsize=1)
def reign_title_entries():
    if not CBDB_REIGN_TITLES_PATH.exists():
        logger.warning("CBDB reign-title file not found: %s", CBDB_REIGN_TITLES_PATH)
        return []

    entries_by_title = {}
    reign_titles = pd.read_csv(CBDB_REIGN_TITLES_PATH)
    for _, row in reign_titles.iterrows():
        title = str(row.get('title', '')).strip()
        if len(title) < MIN_TEXT_MATCH_LENGTH:
            continue
        entry = entries_by_title.setdefault(title, {'title': title, 'records': []})
        entry['records'].append({
            'dynasty': row.get('dynasty', '') if isinstance(row.get('dynasty', ''), str) else '',
            'first_year': int(row['first_year']),
            'last_year': int(row['last_year']),
        })

    return sorted(entries_by_title.values(), key=lambda entry: (-len(entry['title']), entry['title']))


def matched_reign_text(source_text, start, title):
    suffix_match = REIGN_DATE_SUFFIX_PATTERN.match(source_text[start + len(title):])
    if suffix_match:
        return start + len(title) + suffix_match.end()
    return None


def year_label(first_year, last_year):
    if first_year == last_year:
        return str(first_year)
    return f"{first_year}-{last_year}"


def timeline_label(record):
    if not record:
        return ''
    return year_label(record['first_year'], record['last_year'])


def extract_reign_dates(source_text):
    if not source_text:
        return []

    matches = []
    occupied_ranges = []
    for entry in reign_title_entries():
        title = entry['title']
        for match in re.finditer(re.escape(title), source_text):
            end = matched_reign_text(source_text, match.start(), title)
            if end is None:
                continue
            match_range = (match.start(), end)
            if any(ranges_overlap(match_range, occupied) for occupied in occupied_ranges):
                continue
            occupied_ranges.append(match_range)
            matches.append({
                'title': title,
                'matched_text': source_text[match.start():end],
                'start': match.start(),
                'end': end,
                'records': entry['records'],
                'record_count': len(entry['records']),
                'date_display': "；".join(
                    f"{record['dynasty']} {year_label(record['first_year'], record['last_year'])}".strip()
                    for record in entry['records'][:5]
                ),
            })

    return sorted(matches, key=lambda item: item['start'])


def date_match_ranges(matched_dates):
    return [(match['start'], match['end']) for match in matched_dates]


def coherent_reign_records(matched_dates):
    record_groups = [
        match.get('records', [])
        for match in matched_dates
        if match.get('records')
    ]
    if not record_groups:
        return []

    if len(record_groups) == 1:
        return [record_groups[0][0]]

    combinations = product(*record_groups)
    best_records = None
    best_range = None
    best_width = None
    for records in combinations:
        begin = min(record['first_year'] for record in records)
        end = max(record['last_year'] for record in records)
        width = end - begin
        if best_width is None or width < best_width:
            best_records = list(records)
            best_range = (begin, end)
            best_width = width

    return best_records or []


def inferred_date_filter(matched_dates):
    coherent_records = coherent_reign_records(matched_dates)
    if not coherent_records:
        return None

    begin = min(record['first_year'] for record in coherent_records)
    end = max(record['last_year'] for record in coherent_records)
    return begin, end


def timeline_position(record, timeline_begin, timeline_end):
    span = timeline_end - timeline_begin
    if span <= 0:
        return 0, 100

    left = ((record['first_year'] - timeline_begin) / span) * 100
    width = ((record['last_year'] - record['first_year']) / span) * 100
    return max(0, min(100, left)), max(2, min(100, width))


def dated_timeline_matches(matched_dates):
    if not matched_dates:
        return []

    coherent_records = coherent_reign_records(matched_dates)
    if not coherent_records:
        return matched_dates

    timeline_begin = min(record['first_year'] for record in coherent_records)
    timeline_end = max(record['last_year'] for record in coherent_records)
    if timeline_begin == timeline_end:
        timeline_begin -= 1
        timeline_end += 1

    dated_matches = []
    coherent_index = 0
    for match in matched_dates:
        dated_match = dict(match)
        if match.get('records'):
            record = coherent_records[coherent_index]
            coherent_index += 1
            left, width = timeline_position(record, timeline_begin, timeline_end)
            dated_match['timeline_label'] = timeline_label(record)
            dated_match['timeline_left'] = f"{left:.2f}"
            dated_match['timeline_width'] = f"{width:.2f}"
            dated_match['timeline_dynasty'] = record.get('dynasty', '')
        dated_matches.append(dated_match)

    return dated_matches


@lru_cache(maxsize=4)
def gazetteer_entries(prefectures, counties):
    entries_by_variant = {}

    for dataframe in selected_dataframes(prefectures, counties):
        for _, row in dataframe.iterrows():
            canonical_name = row.get('NAME_FT')
            if not isinstance(canonical_name, str) or not canonical_name.strip():
                continue

            for variant, is_alias in place_name_variants(row):
                entry = entries_by_variant.setdefault(
                    variant,
                    {
                        'variant': variant,
                        'names': set(),
                        'alias_names': set(),
                    },
                )
                entry['names'].add(canonical_name.strip())
                if is_alias:
                    entry['alias_names'].add(canonical_name.strip())

    return sorted(
        entries_by_variant.values(),
        key=lambda entry: (-len(entry['variant']), entry['variant']),
    )


def place_name_variants(row):
    variants = []
    seen_variants = set()

    for column in ('NAME_FT', 'NAME_CH'):
        variant = row.get(column)
        if not isinstance(variant, str):
            continue

        variant = variant.strip()
        if len(variant) < MIN_TEXT_MATCH_LENGTH:
            continue

        if variant not in seen_variants:
            variants.append((variant, False))
            seen_variants.add(variant)

        alias = short_alias(variant)
        if alias and alias not in seen_variants:
            variants.append((alias, True))
            seen_variants.add(alias)

    aliases = row.get('ALIASES', '')
    if isinstance(aliases, str):
        for alias_variant in aliases.split('|'):
            alias_variant = alias_variant.strip()
            if len(alias_variant) < MIN_TEXT_MATCH_LENGTH or alias_variant in seen_variants:
                continue
            variants.append((alias_variant, True))
            seen_variants.add(alias_variant)
            short = short_alias(alias_variant)
            if short and short not in seen_variants:
                variants.append((short, True))
                seen_variants.add(short)

    return variants


def short_alias(place_name):
    for suffix in ADMIN_SUFFIXES:
        if place_name.endswith(suffix):
            alias = place_name[:-len(suffix)].strip()
            if len(alias) >= MIN_TEXT_MATCH_LENGTH:
                return alias
    return None


def ranges_overlap(first, second):
    return first[0] < second[1] and second[0] < first[1]


def followed_by_reign_year(source_text, end_index):
    return bool(REIGN_YEAR_PATTERN.match(source_text[end_index:]))


def row_matches_place_names(place_name, query_names):
    return isinstance(place_name, str) and ('' in query_names or place_name in query_names)


def row_matches_place_queries(row, query_names):
    if '' in query_names:
        return True

    variants = {variant for variant, _is_alias in place_name_variants(row)}
    return bool(variants.intersection(query_names))


CHGIS_API_RESULT_RE = re.compile(
    r'<dt class="pnt"><a href="placename/(?P<hvd>[^"]+)">(?P=hvd)</a>\s*<b>(?P<name>[^<]+)</b>\s*</dt>\s*'
    r'<dd class="pnd">\s*\((?P<pinyin>[^)]*)\)\s*begin\s*(?P<begin>-?\d+)\s*CE\s*end\s*(?P<end>-?\d+)\s*CE\s*'
    r'\[(?P<x>-?\d+(?:\.\d+)?),\s*(?P<y>-?\d+(?:\.\d+)?)\]',
    re.S,
)


def chgis_api_level_and_type(name):
    if name.endswith(('郡', '廳', '厅', '州', '府', '軍', '军', '路')):
        return 3, name[-1]
    if name.endswith(('縣', '县')):
        return 6, name[-1]
    return 6, ''


@lru_cache(maxsize=128)
def chgis_api_rows(place_name):
    place_name = str(place_name or '').strip()
    if not place_name:
        return pd.DataFrame()

    try:
        response = requests.get(chgis_placename_url(place_name), timeout=CHGIS_API_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as error:
        logger.warning("CHGIS API lookup failed for %s: %s", place_name, error)
        return pd.DataFrame()

    rows = []
    for match in CHGIS_API_RESULT_RE.finditer(response.text):
        name = unescape(match.group('name')).strip()
        lev_rank, type_ch = chgis_api_level_and_type(name)
        x_coord = float(match.group('x'))
        y_coord = float(match.group('y'))
        rows.append({
            'OBJECTID': '',
            'PY_NAME': match.group('pinyin').strip(),
            'NAME_CH': name,
            'NAME_FT': name,
            'X_COOR': x_coord,
            'Y_COOR': y_coord,
            'PRES_LOC': '',
            'TYPE_PY': '',
            'TYPE_CH': type_ch,
            'LEV_RANK': lev_rank,
            'BEG_YR': int(match.group('begin')),
            'BEG_MO': '',
            'END_YR': int(match.group('end')),
            'END_MO': '',
            'SYS_ID': match.group('hvd'),
            'GEO_SRC': 'CHGIS_API',
            'COMPILER': '',
            'GEOM_TYPE': 'POINT',
            'BEG_CHG_TY': '',
            'END_CHG_TY': '',
            'MATCH_SOURCE': 'chgis_api',
        })

    if not rows:
        return pd.DataFrame()
    return add_record_ids(pd.DataFrame(rows))


def chgis_api_data_for_queries(query_names, date_filter=None):
    dataframes = []
    for query_name in query_names:
        api_data = chgis_api_rows(query_name)
        if api_data.empty:
            continue
        if date_filter:
            begin_date, end_date = date_filter
            api_data = api_data[
                (api_data['BEG_YR'] <= end_date) & (api_data['END_YR'] >= begin_date)
            ]
        dataframes.append(api_data)

    if not dataframes:
        return pd.DataFrame()
    return pd.concat(dataframes, ignore_index=True).drop_duplicates(subset=['RECORD_ID'])


def extract_place_names(source_text, prefectures='prefectures', counties='counties', excluded_ranges=None):
    if not source_text:
        return []

    occupied_ranges = list(excluded_ranges or [])
    matches_by_name = {}
    prefectures_key, counties_key = selected_layer_key(prefectures, counties)

    for entry in gazetteer_entries(prefectures_key, counties_key):
        variant = entry['variant']
        for match in re.finditer(re.escape(variant), source_text):
            match_range = match.span()
            if followed_by_reign_year(source_text, match_range[1]):
                continue
            if any(ranges_overlap(match_range, occupied) for occupied in occupied_ranges):
                continue

            occupied_ranges.append(match_range)
            for name in entry['names']:
                match_record = matches_by_name.setdefault(
                    name,
                    {
                        'name': name,
                        'variants': set(),
                        'alias_variants': set(),
                        'count': 0,
                        'first_start': match_range[0],
                    },
                )
                match_record['variants'].add(variant)
                if name in entry['alias_names']:
                    match_record['alias_variants'].add(variant)
                match_record['count'] += 1
                match_record['first_start'] = min(match_record['first_start'], match_range[0])

    matched_places = []
    for match_record in matches_by_name.values():
        variants = sorted(match_record['variants'])
        alias_variants = sorted(match_record['alias_variants'])
        matched_places.append({
            'name': match_record['name'],
            'variants': variants,
            'alias_variants': alias_variants,
            'variant_display': "，".join(variants),
            'alias_variant_display': "，".join(alias_variants),
            'matched_by_alias': bool(alias_variants),
            'count': match_record['count'],
            'first_start': match_record['first_start'],
        })

    return sorted(matched_places, key=lambda place: (place['first_start'], place['name']))


def context_window(source_text, start, end, radius=AI_PLACE_CONTEXT_RADIUS):
    context_start = max(0, start - radius)
    context_end = min(len(source_text), end + radius)
    return source_text[context_start:context_end]


def ai_candidate_places(source_text, matched_places):
    candidates_by_variant = {}
    for place in matched_places:
        for variant in place.get('variants', []):
            if not variant:
                continue
            candidate = candidates_by_variant.setdefault(
                variant,
                {
                    'phrase': variant,
                    'candidate_names': set(),
                    'contexts': [],
                },
            )
            candidate['candidate_names'].add(place['name'])

    candidates = []
    for variant, candidate in candidates_by_variant.items():
        for match in re.finditer(re.escape(variant), source_text):
            candidate['contexts'].append(context_window(source_text, match.start(), match.end()))
        if not candidate['contexts']:
            continue
        candidates.append({
            'phrase': candidate['phrase'],
            'candidate_names': sorted(candidate['candidate_names']),
            'contexts': candidate['contexts'][:3],
        })

    return sorted(candidates, key=lambda item: source_text.find(item['phrase']))[:AI_PLACE_MAX_CANDIDATES]


def openai_api_key():
    return os.environ.get('OPENAI_API_KEY', '').strip()


def ai_place_cache_key(source_text, candidates):
    payload = json.dumps(
        {
            'model': AI_PLACE_MODEL,
            'prompt_version': AI_PLACE_PROMPT_VERSION,
            'source_text': source_text,
            'candidates': candidates,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def read_ai_place_cache():
    if not AI_PLACE_CACHE_PATH.exists():
        return {}

    try:
        with AI_PLACE_CACHE_PATH.open(encoding='utf-8') as cache_file:
            cache = json.load(cache_file)
    except (OSError, json.JSONDecodeError) as error:
        logger.error("AI place cleanup cache read failed: %s", error)
        return {}

    return cache if isinstance(cache, dict) else {}


def write_ai_place_cache(cache):
    try:
        AI_PLACE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AI_PLACE_CACHE_PATH.open('w', encoding='utf-8') as cache_file:
            json.dump(cache, cache_file, ensure_ascii=False, indent=2)
    except OSError as error:
        logger.error("AI place cleanup cache write failed: %s", error)


def cached_ai_place_judgments(source_text, candidates):
    if not candidates:
        return None

    cache = read_ai_place_cache()
    key = ai_place_cache_key(source_text, candidates)
    entry = cache.get(key)
    if isinstance(entry, dict) and isinstance(entry.get('judgments'), list):
        return entry['judgments']
    return None


def store_ai_place_judgments(source_text, candidates, judgments):
    if not candidates or judgments is None:
        return

    cache = read_ai_place_cache()
    key = ai_place_cache_key(source_text, candidates)
    cache[key] = {
        'model': AI_PLACE_MODEL,
        'prompt_version': AI_PLACE_PROMPT_VERSION,
        'judgments': judgments,
    }
    if len(cache) > AI_PLACE_CACHE_MAX_ENTRIES:
        cache = dict(list(cache.items())[-AI_PLACE_CACHE_MAX_ENTRIES:])
    write_ai_place_cache(cache)


def extract_response_text(response_json):
    output_text = response_json.get('output_text')
    if isinstance(output_text, str):
        return output_text

    chunks = []
    for output in response_json.get('output', []):
        for content in output.get('content', []):
            text = content.get('text')
            if isinstance(text, str):
                chunks.append(text)
    return ''.join(chunks)


def parse_ai_json(text):
    text = (text or '').strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        json_match = re.search(r"\[[\s\S]*\]", text)
        if json_match:
            return json.loads(json_match.group(0))
        raise


def judge_place_candidates_with_ai(source_text, candidates):
    api_key = openai_api_key()
    if not api_key or not source_text or not candidates:
        return []

    cached_judgments = cached_ai_place_judgments(source_text, candidates)
    if cached_judgments is not None:
        return cached_judgments

    instructions = (
        "You receive a passage and a deterministic list of possible CHGIS place-name matches. "
        "Read the passage and remove candidates that are not actually being used as places. "
        "Reject a candidate when the matched characters are actually a person's name, a person's title, "
        "part of a personal name/title phrase, a reign name/date, an office title, a verb phrase, "
        "or ordinary wording. Keep a candidate only when the phrase is being used semantically as "
        "a place or administrative geography in this passage. "
        "Important classical Chinese title cases: 建安王法曹 means 建安王 + 法曹, so 王法 is not a place; "
        "晉王令使者 means 晉王 + 令, so 王令 is not a place; 西陽王友 means 西陽王 + 友, so 王友 is not a place; "
        "秦王俊 means 秦王 + 俊, so 王俊 is not a place. "
        "Return only compact JSON: an array of objects with phrase, is_place, and reason. "
        "Use JSON booleans true/false, not strings."
    )
    payload = {
        'model': AI_PLACE_MODEL,
        'instructions': instructions,
        'input': json.dumps(
            {
                'passage': source_text,
                'candidates': candidates,
            },
            ensure_ascii=False,
        ),
        'max_output_tokens': 1600,
    }
    headers = {
        'Authorization': f"Bearer {api_key}",
        'Content-Type': 'application/json',
    }

    try:
        response = requests.post(OPENAI_RESPONSES_URL, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        judgments = parse_ai_json(extract_response_text(response.json()))
    except Exception as error:
        logger.error("AI place cleanup failed: %s", error)
        return []

    if not isinstance(judgments, list):
        return []
    judgments = [judgment for judgment in judgments if isinstance(judgment, dict)]
    store_ai_place_judgments(source_text, candidates, judgments)
    return judgments


def ai_is_false(value):
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {'false', 'no', 'not_place', 'not a place'}
    return False


def cleanup_removed_place_notice(phrase, place_names, reason, method='AI'):
    return {
        'phrase': phrase,
        'names': "，".join(place_names),
        'reason': reason or 'Cleanup judged this phrase was not being used as a place.',
        'method': method,
    }


def prince_title_split_reason(source_text, variant):
    if not variant.startswith('王'):
        return None

    occurrence_reasons = []
    for match in re.finditer(re.escape(variant), source_text):
        context = context_window(source_text, match.start(), match.end(), radius=6)
        if any(pattern.search(context) for pattern in PRINCE_TITLE_SPLIT_PATTERNS):
            occurrence_reasons.append(
                f"{variant} appears inside a princely title or title phrase, not as a place name."
            )
        else:
            return None

    if occurrence_reasons:
        return occurrence_reasons[0]
    return None


def cleaned_place_matches_by_variant_judgment(matched_places, judgment_by_variant, method='AI'):
    removed = []
    cleaned_places = []

    for place in matched_places:
        approved_variants = []
        removed_variants = []
        for variant in place.get('variants', []):
            judgment = judgment_by_variant.get(variant)
            if judgment and ai_is_false(judgment.get('is_place')):
                removed_variants.append(variant)
            else:
                approved_variants.append(variant)

        if approved_variants:
            cleaned_place = dict(place)
            cleaned_place['variants'] = approved_variants
            cleaned_place['alias_variants'] = [
                variant for variant in place.get('alias_variants', [])
                if variant in approved_variants
            ]
            cleaned_place['variant_display'] = "，".join(cleaned_place['variants'])
            cleaned_place['alias_variant_display'] = "，".join(cleaned_place['alias_variants'])
            cleaned_place['matched_by_alias'] = bool(cleaned_place['alias_variants'])
            cleaned_places.append(cleaned_place)
        else:
            place_names = [place['name']]
            phrase = removed_variants[0] if removed_variants else place.get('name', '')
            reason = judgment_by_variant.get(phrase, {}).get('reason', '')
            removed.append(cleanup_removed_place_notice(phrase, place_names, reason, method=method))

    return cleaned_places, removed


def reign_date_place_judgments(matched_dates, matched_places):
    judgments = {}
    reign_texts = set()
    for matched_date in matched_dates or []:
        title = str(matched_date.get('title', '')).strip()
        matched_text = str(matched_date.get('matched_text', '')).strip()
        if title:
            reign_texts.add(title)
        if matched_text:
            reign_texts.add(matched_text)

    if not reign_texts:
        return judgments

    for place in matched_places:
        for variant in place.get('variants', []):
            if variant in reign_texts:
                judgments[variant] = {
                    'phrase': variant,
                    'is_place': False,
                    'reason': f"{variant} is being used as a reign name/date in this passage.",
                }
    return judgments


def fief_title_place_judgments(contextual_markers, matched_places):
    judgments = {}
    fief_variants = {
        marker.get('variant')
        for marker in contextual_markers or []
        if marker.get('kind') == 'fief_title' and marker.get('variant')
    }
    if not fief_variants:
        return judgments

    for place in matched_places:
        for variant in place.get('variants', []):
            if variant in fief_variants:
                judgments[variant] = {
                    'phrase': variant,
                    'is_place': False,
                    'reason': f"{variant} is being used inside a fief title in this passage.",
                }
    return judgments


def rule_place_judgments(
    source_text,
    matched_places,
    matched_dates=None,
    _matched_people=None,
    contextual_markers=None,
    include_contextual_locations=False,
):
    judgments = {}
    for place in matched_places:
        for variant in place.get('variants', []):
            reason = prince_title_split_reason(source_text, variant)
            if reason:
                judgments[variant] = {
                    'phrase': variant,
                    'is_place': False,
                    'reason': reason,
                }
    judgments.update(reign_date_place_judgments(matched_dates, matched_places))
    if not include_contextual_locations:
        judgments.update(fief_title_place_judgments(contextual_markers, matched_places))
    return judgments


def contextual_marker_variants(contextual_markers):
    return {
        marker.get('variant')
        for marker in contextual_markers or []
        if marker.get('kind') == 'fief_title' and marker.get('variant')
    }


def ai_cleaned_place_matches(
    source_text,
    matched_places,
    matched_dates=None,
    _matched_people=None,
    contextual_markers=None,
    include_contextual_locations=False,
):
    candidates = ai_candidate_places(source_text, matched_places)
    judgments = judge_place_candidates_with_ai(source_text, candidates)
    if include_contextual_locations:
        fief_variants = contextual_marker_variants(contextual_markers)
        judgments = [
            judgment for judgment in judgments
            if str(judgment.get('phrase', '')).strip() not in fief_variants
        ]
    rule_judgments = rule_place_judgments(
        source_text,
        matched_places,
        matched_dates,
        _matched_people,
        contextual_markers,
        include_contextual_locations=include_contextual_locations,
    )
    cleanup = {
        'ran': bool(judgments) or bool(rule_judgments),
        'candidate_count': len(candidates),
        'judgment_count': len(judgments),
        'model': AI_PLACE_MODEL if judgments else '',
        'removed': [],
    }

    judgments_by_phrase = {
        str(judgment.get('phrase', '')).strip(): judgment
        for judgment in judgments
        if str(judgment.get('phrase', '')).strip()
    }
    judgments_by_phrase.update(rule_judgments)
    if not judgments_by_phrase:
        return matched_places, cleanup

    cleaned_places, removed = cleaned_place_matches_by_variant_judgment(
        matched_places,
        judgments_by_phrase,
        method='cleanup',
    )

    cleanup['removed'].extend(removed)
    return cleaned_places, cleanup


# Process user input and filter the data
def filter_data(place_names, date_filter=None, prefectures=None, counties=None, allow_date_only=True):
    filtered_data = pd.DataFrame()  # Create an empty DataFrame for the filtered data

    # split up place names, if necessary
    place_names = split_place_names(place_names)
    if not place_names:
        if not date_filter or not allow_date_only:
            return filtered_data
        place_names = ['']

    if date_filter:
        begin_date, end_date = date_filter

    # Filter the prefectures data
    if prefectures:
        #filtered_prefectures = prefectures_df[prefectures_df['NAME_FT'].isin(place_names)]
        filtered_prefectures = prefectures_df[prefectures_df.apply(lambda row: row_matches_place_queries(row, place_names), axis=1)]
        if date_filter:
            filtered_prefectures = filtered_prefectures[
                (filtered_prefectures['BEG_YR'] <= end_date) & (filtered_prefectures['END_YR'] >= begin_date)
            ]
        filtered_data = pd.concat([filtered_data, filtered_prefectures])
        logger.info("Number of prefectures returned: %s", filtered_prefectures.shape[0])

    # Filter the counties data
    if counties:
        #filtered_counties = counties_df[counties_df['NAME_FT'].isin(place_names)]
        filtered_counties = counties_df[counties_df.apply(lambda row: row_matches_place_queries(row, place_names), axis=1)]
        if date_filter:
            filtered_counties = filtered_counties[
                (filtered_counties['BEG_YR'] <= end_date) & (filtered_counties['END_YR'] >= begin_date)
            ]
        filtered_data = pd.concat([filtered_data, filtered_counties])
        logger.info("Number of counties returned: %s", filtered_counties.shape[0])

    return filtered_data


def row_has_coordinates(row):
    if pd.isna(row['Y_COOR']) or pd.isna(row['X_COOR']):
        return False
    latitude = float(row['Y_COOR'])
    longitude = float(row['X_COOR'])
    if latitude == 0.0 or longitude == 0.0:
        return False
    return -15.0 <= latitude <= 60.0 and 60.0 <= longitude <= 150.0


def split_mappable_rows(data):
    if data.empty:
        return data, []

    mappable_mask = data.apply(row_has_coordinates, axis=1)
    return data[mappable_mask], data[~mappable_mask].to_dict('records')


def rows_to_place_records(rows):
    records = []
    for row in rows:
        records.append({
            'name': row.get('NAME_FT', ''),
            'sys_id': chgis_display_id(row),
            'type': row.get('TYPE_CH', ''),
            'begin_year': row.get('BEG_YR', ''),
            'end_year': row.get('END_YR', ''),
            'url': chgis_record_url(row),
        })
    return records


def place_record(row, selected_record_ids):
    record_id = str(row.get('RECORD_ID', ''))
    return {
        'id': record_id,
        'sys_id': chgis_display_id(row),
        'name': row.get('NAME_FT', ''),
        'type': type_label(row),
        'begin_year': row.get('BEG_YR', ''),
        'end_year': row.get('END_YR', ''),
        'selected': record_id in selected_record_ids,
        'mappable': row_has_coordinates(row),
        'url': chgis_record_url(row),
    }


def date_only_place_records(candidate_data, selected_record_ids):
    if candidate_data.empty:
        return []

    selected_record_ids = set(selected_record_ids)
    sort_columns = [
        column
        for column in ('LEV_RANK', 'NAME_FT', 'BEG_YR', 'END_YR', 'TYPE_CH', 'SYS_ID')
        if column in candidate_data.columns
    ]
    rows = candidate_data.sort_values(sort_columns) if sort_columns else candidate_data
    return [place_record(row, selected_record_ids) for _, row in rows.iterrows()]


def annotate_matched_places(matched_places, filtered_data, selected_record_ids):
    selected_record_ids = set(selected_record_ids)
    if filtered_data.empty:
        result_counts = {}
        mapped_counts = {}
        unmappable_counts = {}
        records_by_name = {}
    else:
        result_counts = filtered_data.groupby('NAME_FT').size().to_dict()
        mappable_data, unmappable_rows = split_mappable_rows(filtered_data)
        mapped_counts = mappable_data.groupby('NAME_FT').size().to_dict() if not mappable_data.empty else {}
        unmappable_data = pd.DataFrame(unmappable_rows)
        unmappable_counts = unmappable_data.groupby('NAME_FT').size().to_dict() if not unmappable_data.empty else {}
        records_by_name = {
            name: [
                place_record(row, selected_record_ids)
                for _, row in rows.sort_values(['BEG_YR', 'END_YR', 'TYPE_CH', 'SYS_ID']).iterrows()
            ]
            for name, rows in filtered_data.groupby('NAME_FT')
        }

    annotated_places = []
    for place in matched_places:
        name = place['name']
        annotated_place = dict(place)
        annotated_place['result_count'] = int(result_counts.get(name, 0))
        annotated_place['mapped_count'] = int(mapped_counts.get(name, 0))
        annotated_place['unmappable_count'] = int(unmappable_counts.get(name, 0))
        annotated_place['records'] = records_by_name.get(name, [])
        annotated_place['selected'] = any(record['selected'] for record in annotated_place['records'])
        annotated_places.append(annotated_place)

    return annotated_places


def manual_matched_places_from_data(place_names, filtered_data):
    if filtered_data.empty:
        return []

    places = []
    for query_name in split_place_names(place_names):
        matching_rows = filtered_data[
            filtered_data.apply(lambda row: row_matches_place_queries(row, [query_name]), axis=1)
        ]
        if matching_rows.empty:
            continue

        selected_record_ids = set(matching_rows['RECORD_ID'].astype(str))
        mappable_rows, unmappable_rows = split_mappable_rows(matching_rows)
        records = [
            place_record(row, selected_record_ids)
            for _, row in matching_rows.sort_values(['BEG_YR', 'END_YR', 'TYPE_CH', 'SYS_ID']).iterrows()
        ]
        places.append({
            'name': query_name,
            'count': 1,
            'variants': [query_name],
            'alias_variants': [],
            'variant_display': query_name,
            'alias_variant_display': '',
            'matched_by_alias': False,
            'result_count': len(matching_rows),
            'mapped_count': len(mappable_rows),
            'unmappable_count': len(unmappable_rows),
            'records': records,
            'selected': bool(records),
        })
    return places


def result_size_warning(mappable_data, unmappable_count):
    if len(mappable_data) <= MAX_RENDER_ROWS and unmappable_count == 0:
        return None

    messages = []
    if len(mappable_data) > MAX_RENDER_ROWS:
        messages.append(
            f"{len(mappable_data)} mappable records matched; showing the first {MAX_RENDER_ROWS}. Add names or dates to narrow the result."
        )
    if unmappable_count:
        messages.append(f"{unmappable_count} matched records have no CHGIS coordinates and are listed separately.")

    return " ".join(messages)
    
# def tooltip_maker(row)
# not implemented -- CHGIS 'sys_id' cannot be matched with API 'hvd" ids???


def chgis_placename_url(place_name):
    return f"{CHGIS_PLACENAME_URL}?n={quote(str(place_name))}"


def chgis_record_url(row):
    source_data = str(row.get('SOURCE_DATA', '')).strip()
    hvd_id = str(row.get('HVD_ID', '')).strip()
    sys_id = str(row.get('SYS_ID', '')).strip()
    if source_data == 'TGAZ_2018_SUPPLEMENT' and hvd_id.startswith('hvd_'):
        return f"{CHGIS_PLACENAME_URL}/{quote(hvd_id)}"
    if sys_id.startswith('hvd_'):
        return f"{CHGIS_PLACENAME_URL}/{quote(sys_id)}"
    return chgis_placename_url(row.get('NAME_FT', ''))


def chgis_display_id(row):
    source_data = str(row.get('SOURCE_DATA', '')).strip()
    if source_data == 'CHGIS_V6_2021':
        v6_id = str(row.get('V6_SYS_ID') or row.get('SYS_ID') or '').strip()
        return f"V6 {v6_id}" if v6_id else 'V6'
    hvd_id = str(row.get('HVD_ID') or row.get('SYS_ID') or '').strip()
    if hvd_id:
        return f"TGAZ {hvd_id}" if source_data == 'TGAZ_2018_SUPPLEMENT' else hvd_id
    return ''


def chgis_popup(place_name):
    return f"<a href='{chgis_placename_url(place_name)}' target='_blank'>Link to CHGIS</a>"


def tooltip_for_row(row):
    chgis_id = chgis_display_id(row)
    id_line = f"<br><span style='font-size:13px;color:#555;'>{escape(str(chgis_id))}</span>" if chgis_id != '' else ''
    if pd.isna(row['BEG_CHG_TY']):
        return f"<div style='font-size: 20px;'>{escape(str(row['NAME_FT']))}<br>{row['BEG_YR']} to {row['END_YR']}{id_line}</div>"

    return (
        f"<div style='font-size: 20px;'>{escape(str(row['NAME_FT']))}<br>"
        f"{row['BEG_YR']}{escape(str(row['BEG_CHG_TY']))}<br>"
        f"{row['END_YR']}{escape(str(row['END_CHG_TY']))}{id_line}</div>"
    )


def marker_for_row(row):
    if not row_has_coordinates(row):
        logger.warning("Skipping row with missing coordinates: %s", row['NAME_FT'])
        return None

    marker_args = {
        'location': [row['Y_COOR'], row['X_COOR']],
        'draggable': True,
        'popup': chgis_popup(row['NAME_FT']),
        'tooltip': tooltip_for_row(row),
    }

    if row.get('MATCH_SOURCE') == MATCH_SOURCE_CHORONYM:
        return folium.Marker(
            icon=folium.Icon(icon='map-marker', prefix='fa', color='orange'),
            **marker_args
        )

    if row['LEV_RANK'] == 6:
        return folium.Marker(
            icon=folium.Icon(icon='star', prefix='fa', color='red'),
            **marker_args
        )

    if row['LEV_RANK'] > 6:
        return folium.CircleMarker(
            radius=4,
            color='#555',
            fill=True,
            fill_color='#555',
            fill_opacity=0.9,
            **marker_args
        )

    return folium.Marker(icon=folium.Icon(icon='square', prefix='fa', color='blue'), **marker_args)


class ClusterClickSpiderfy(MacroElement):
    _template = Template("""
        {% macro script(this, kwargs) %}
            {{ this.cluster_name }}.on('clusterclick', function(event) {
                event.layer.spiderfy();
            });
        {% endmacro %}
    """)

    def __init__(self, marker_cluster):
        super().__init__()
        self._name = 'ClusterClickSpiderfy'
        self.cluster_name = marker_cluster.get_name()


class ChgisSelectableMapLayer(MacroElement):
    _template = Template("""
        {% macro script(this, kwargs) %}
            window.CHGISMapSelection = window.CHGISMapSelection || {layers: []};
            window.CHGISMapSelection.layers.push({
                layer: {{ this.layer_name }},
                parent: {{ this.parent_name }},
                recordIds: {{ this.record_ids_json }},
                visible: true
            });
            window.CHGISMapSelection.apply = function(selectedRecordIds) {
                var selected = new Set(selectedRecordIds || []);
                window.CHGISMapSelection.layers.forEach(function(entry) {
                    var shouldShow = entry.recordIds.some(function(recordId) {
                        return selected.has(recordId);
                    });
                    if (shouldShow && !entry.visible) {
                        entry.parent.addLayer(entry.layer);
                        entry.visible = true;
                    } else if (!shouldShow && entry.visible) {
                        entry.parent.removeLayer(entry.layer);
                        entry.visible = false;
                    }
                });
            };
        {% endmacro %}
    """)

    def __init__(self, layer, parent, record_ids):
        super().__init__()
        self._name = 'ChgisSelectableMapLayer'
        self.layer_name = layer.get_name()
        self.parent_name = parent.get_name()
        self.record_ids_json = Markup(json.dumps([str(record_id) for record_id in record_ids]))


class ChgisSelectablePolygonLayer(MacroElement):
    _template = Template("""
        {% macro script(this, kwargs) %}
            window.CHGISPolygonSelection = window.CHGISPolygonSelection || {layers: []};
            window.CHGISPolygonSelection.bringToFront = function() {
                window.CHGISPolygonSelection.layers.forEach(function(entry) {
                    if (typeof entry.layer.bringToFront === 'function') {
                        entry.layer.bringToFront();
                    }
                });
            };
            window.CHGISPolygonSelection.restack = function() {
                if ({{ this.map_name }}.hasLayer({{ this.group_name }})) {
                    {{ this.map_name }}.removeLayer({{ this.group_name }});
                    {{ this.group_name }}.addTo({{ this.map_name }});
                }
                window.CHGISPolygonSelection.bringToFront();
            };
            window.CHGISPolygonSelection.unselectedStyle = window.CHGISPolygonSelection.unselectedStyle || {
                color: '#465f63',
                weight: 1.15,
                opacity: 0.7,
                fillColor: '#9ab3a5',
                fillOpacity: 0.03
            };
            window.CHGISPolygonSelection.selectedStyle = window.CHGISPolygonSelection.selectedStyle || {
                color: '#263f42',
                weight: 1.8,
                opacity: 0.9,
                fillColor: '#4f8f73',
                fillOpacity: 0.32
            };
            {{ this.geo_json_name }}.eachLayer(function(layer) {
                var props = layer.feature && layer.feature.properties ? layer.feature.properties : {};
                if (!props.polygon_id || typeof layer.setStyle !== 'function') {
                    return;
                }
                layer.setStyle(window.CHGISPolygonSelection.unselectedStyle);
                window.CHGISPolygonSelection.layers.push({
                    layer: layer,
                    polygonId: props.polygon_id
                });
            });
            window.CHGISPolygonSelection.apply = function(selectedPolygonIds) {
                var selected = new Set(selectedPolygonIds || []);
                window.CHGISPolygonSelection.layers.forEach(function(entry) {
                    entry.layer.setStyle(
                        selected.has(entry.polygonId)
                            ? window.CHGISPolygonSelection.selectedStyle
                            : window.CHGISPolygonSelection.unselectedStyle
                    );
                });
                window.CHGISPolygonSelection.restack();
            };
            window.setTimeout(window.CHGISPolygonSelection.restack, 0);
            window.setTimeout(window.CHGISPolygonSelection.restack, 500);
        {% endmacro %}
    """)

    def __init__(self, folium_map, polygon_group, geo_json):
        super().__init__()
        self._name = 'ChgisSelectablePolygonLayer'
        self.map_name = folium_map.get_name()
        self.group_name = polygon_group.get_name()
        self.geo_json_name = geo_json.get_name()


class ChgisMapPanes(MacroElement):
    _template = Template("""
        {% macro script(this, kwargs) %}
            if (!{{ this.map_name }}.getPane('chgisPlainBasePane')) {
                {{ this.map_name }}.createPane('chgisPlainBasePane');
                {{ this.map_name }}.getPane('chgisPlainBasePane').style.zIndex = 180;
            }
            if (!{{ this.map_name }}.getPane('chgisHistoricalOverlayPane')) {
                {{ this.map_name }}.createPane('chgisHistoricalOverlayPane');
                {{ this.map_name }}.getPane('chgisHistoricalOverlayPane').style.zIndex = 460;
            }
        {% endmacro %}
    """)

    def __init__(self, folium_map):
        super().__init__()
        self._name = 'ChgisMapPanes'
        self.map_name = folium_map.get_name()


class ChgisPlainBaseLoader(MacroElement):
    _template = Template("""
        {% macro script(this, kwargs) %}
            var {{ this.loaded_name }} = false;
            function {{ this.load_name }}() {
                if ({{ this.loaded_name }}) {
                    return;
                }
                {{ this.loaded_name }} = true;
                L.rectangle([[-85, -180], [85, 180]], {
                    color: '#d7e7ea',
                    fill: true,
                    fillColor: '#d7e7ea',
                    fillOpacity: 1,
                    weight: 0,
                    pane: 'chgisPlainBasePane',
                    interactive: false
                }).addTo({{ this.layer_name }});
                fetch('{{ this.base_url }}')
                    .then(function(response) { return response.json(); })
                    .then(function(baseData) {
                        if (baseData.province_boundaries) {
                            L.geoJson(baseData.province_boundaries, {
                                pane: 'chgisPlainBasePane',
                                style: {
                                    color: '#8a8c82',
                                    weight: 0.8,
                                    opacity: 0.72,
                                    fillColor: '#f8f4e8',
                                    fillOpacity: 0.86
                                }
                            }).addTo({{ this.layer_name }});
                        }
                        if (baseData.lakes) {
                            L.geoJson(baseData.lakes, {
                                pane: 'chgisPlainBasePane',
                                style: {
                                    color: '#91a9b3',
                                    weight: 0.6,
                                    opacity: 0.7,
                                    fillColor: '#cbdde0',
                                    fillOpacity: 0.45
                                }
                            }).addTo({{ this.layer_name }});
                        }
                        if (baseData.rivers) {
                            L.geoJson(baseData.rivers, {
                                pane: 'chgisPlainBasePane',
                                style: {
                                    color: '#7f9da8',
                                    weight: 0.55,
                                    opacity: 0.58
                                }
                            }).addTo({{ this.layer_name }});
                        }
                        if (window.CHGISPolygonSelection && typeof window.CHGISPolygonSelection.restack === 'function') {
                            window.CHGISPolygonSelection.restack();
                            window.setTimeout(window.CHGISPolygonSelection.restack, 0);
                            window.setTimeout(window.CHGISPolygonSelection.restack, 350);
                        }
                    })
                    .catch(function(error) {
                        console.warn('Could not load plain base - CHGIS 1820', error);
                    });
            }
            {{ this.map_name }}.on('baselayerchange', function(event) {
                if (event.name === 'plain base - CHGIS 1820') {
                    {{ this.load_name }}();
                }
            });
            if ({{ this.map_name }}.hasLayer({{ this.layer_name }})) {
                {{ this.load_name }}();
            }
        {% endmacro %}
    """)

    def __init__(self, folium_map, base_layer, base_url):
        super().__init__()
        self._name = 'ChgisPlainBaseLoader'
        self.map_name = folium_map.get_name()
        self.layer_name = base_layer.get_name()
        self.base_url = base_url
        self.loaded_name = f"{self.get_name()}_loaded"
        self.load_name = f"{self.get_name()}_load"


class BaseLayerAutoSwitch(MacroElement):
    _template = Template("""
        {% macro script(this, kwargs) %}
            var {{ this.force_shaded_name }} = false;
            function {{ this.switch_name }}() {
                if ({{ this.force_shaded_name }} || {{ this.map_name }}.getZoom() >= {{ this.switch_zoom }}) {
                    {{ this.physical_name }}.setOpacity(0);
                    {{ this.shaded_name }}.setOpacity(1);
                } else {
                    {{ this.physical_name }}.setOpacity(1);
                    {{ this.shaded_name }}.setOpacity(0);
                }
            }
            {{ this.physical_name }}.on('tileerror', function() {
                {{ this.force_shaded_name }} = true;
                {{ this.switch_name }}();
            });
            {{ this.map_name }}.on('zoomstart', function() {
                {{ this.force_shaded_name }} = false;
            });
            {{ this.map_name }}.on('zoomend load', {{ this.switch_name }});
            window.setTimeout({{ this.switch_name }}, 0);
        {% endmacro %}
    """)

    def __init__(self, folium_map, physical_layer, shaded_layer, switch_zoom):
        super().__init__()
        self._name = 'BaseLayerAutoSwitch'
        self.map_name = folium_map.get_name()
        self.physical_name = physical_layer.get_name()
        self.shaded_name = shaded_layer.get_name()
        self.switch_zoom = switch_zoom
        self.switch_name = f"{self.get_name()}_switch"
        self.force_shaded_name = f"{self.get_name()}_forceShaded"


def location_key(row):
    return round(float(row['Y_COOR']), 6), round(float(row['X_COOR']), 6)


def type_label(row):
    type_text = row.get('TYPE_CH')
    if isinstance(type_text, str) and type_text.strip():
        return type_text.strip()

    lev_rank = row.get('LEV_RANK')
    if lev_rank == 3:
        return 'prefecture'
    if lev_rank == 6:
        return 'county'
    return ''


def date_label(row):
    return f"{row.get('BEG_YR', '')}-{row.get('END_YR', '')}"


def grouped_location_records(data):
    groups = {}
    for _, row in data.iterrows():
        key = location_key(row)
        group = groups.setdefault(key, [])
        group.append(row)

    return sorted(groups.items(), key=lambda item: (item[0][0], item[0][1]))


def record_ids_for_group(records):
    return [str(row.get('RECORD_ID')) for row in records if row.get('RECORD_ID') is not None]


def map_bounds_for_locations(locations):
    if not locations:
        return None

    latitudes = [location[0] for location in locations]
    longitudes = [location[1] for location in locations]
    min_lat, max_lat = min(latitudes), max(latitudes)
    min_lon, max_lon = min(longitudes), max(longitudes)

    if min_lat == max_lat:
        min_lat -= SINGLE_POINT_BOUNDS_BUFFER
        max_lat += SINGLE_POINT_BOUNDS_BUFFER
    if min_lon == max_lon:
        min_lon -= SINGLE_POINT_BOUNDS_BUFFER
        max_lon += SINGLE_POINT_BOUNDS_BUFFER

    return [[min_lat, min_lon], [max_lat, max_lon]]


def grouped_popup(records):
    rows = []
    for row in sorted(records, key=lambda item: (str(item.get('NAME_FT')), item.get('BEG_YR', 0), item.get('END_YR', 0))):
        name = escape(str(row.get('NAME_FT', '')))
        chgis_id = escape(chgis_display_id(row))
        record_type = escape(str(type_label(row)))
        years = escape(str(date_label(row)))
        url = escape(chgis_record_url(row), quote=True)
        rows.append(
            "<tr>"
            f"<td style='padding:4px 12px 4px 0;white-space:nowrap;'><a href='{url}' target='_blank'>{name}</a></td>"
            f"<td style='padding:4px 12px 4px 0;color:#555;white-space:nowrap;'>{chgis_id}</td>"
            f"<td style='padding:4px 12px 4px 0;color:#555;white-space:nowrap;'>{record_type}</td>"
            f"<td style='padding:4px 0;color:#555;white-space:nowrap;'>{years}</td>"
            "</tr>"
        )

    return (
        "<div style='font-size:14px;min-width:420px;max-width:620px;'>"
        f"<strong>{len(records)} CHGIS record{'s' if len(records) != 1 else ''} at this location</strong>"
        "<table style='border-collapse:collapse;margin-top:8px;width:100%;'>"
        + "".join(rows)
        + "</table></div>"
    )


def grouped_tooltip(records):
    first_name = str(records[0].get('NAME_FT', ''))
    ids = [chgis_display_id(row) for row in records if chgis_display_id(row)]
    id_text = ", ".join(ids[:4])
    if len(ids) > 4:
        id_text += ", ..."
    id_line = f"<br><span style='font-size:12px;color:#555;'>CHGIS {escape(id_text)}</span>" if id_text else ''
    if len(records) == 1:
        return f"<div style='font-size: 18px;'>{escape(first_name)}{id_line}</div>"
    return f"<div style='font-size: 18px;'>{escape(first_name)} and {len(records) - 1} more{id_line}</div>"


def marker_icon_for_group(records):
    if any(row.get('MATCH_SOURCE') == MATCH_SOURCE_CHORONYM for row in records):
        return folium.Icon(icon='map-marker', prefix='fa', color='orange')

    if all(row.get('MATCH_STATUS') == 'candidate' for row in records):
        return folium.Icon(icon='question', prefix='fa', color='lightgray')

    levels = {row.get('LEV_RANK') for row in records}
    if 3 in levels and 6 in levels:
        return folium.Icon(icon='map-marker', prefix='fa', color='green')
    if 3 in levels:
        return folium.Icon(icon='star', prefix='fa', color='blue')
    return folium.Icon(icon='circle', prefix='fa', color='red')


def label_shape_for_group(records):
    type_text = ''.join(str(type_label(row)) for row in records)
    levels = {row.get('LEV_RANK') for row in records}
    if '縣' in type_text or '县' in type_text or (levels and all(level >= 6 for level in levels)):
        return 'round'
    if '郡' in type_text or '州' in type_text or any(0 < level < 6 for level in levels):
        return 'square'
    return 'square'


def level_class_for_group(records):
    levels = [row.get('LEV_RANK') for row in records if pd.notna(row.get('LEV_RANK'))]
    if not levels:
        return 'unknown'
    min_level = min(levels)
    if min_level <= 2:
        return 'regional'
    if min_level < 6:
        return 'prefectural'
    return 'county'


def category_text_for_group(records):
    labels = [str(type_label(row)).strip() for row in records if str(type_label(row)).strip()]
    return " / ".join(dict.fromkeys(labels[:2]))


def label_text_for_group(records):
    sorted_records = sorted(
        records,
        key=lambda row: (
            0 if row.get('MATCH_STATUS') == 'selected' else 1,
            str(row.get('NAME_FT') or row.get('NAME_CH') or ''),
            row.get('BEG_YR', 0),
            row.get('END_YR', 0),
        ),
    )
    label = str(sorted_records[0].get('NAME_FT') or sorted_records[0].get('NAME_CH') or '').strip()
    if len(records) > 1:
        label = f"{label} +{len(records) - 1}"
    return label


def label_html_for_group(records):
    shape = label_shape_for_group(records)
    level_class = level_class_for_group(records)
    if level_class == 'regional':
        radius = '1px'
        border_color = '#283c43'
        font_size = '17px'
        padding = '5px 8px'
        background = 'rgba(255,255,255,.72)'
    elif level_class == 'prefectural':
        radius = '2px'
        border_color = '#263f42'
        font_size = '15px'
        padding = '5px 8px'
        background = 'rgba(255,255,255,.9)'
    else:
        radius = '999px' if shape == 'round' else '2px'
        border_color = '#8a3c2b' if shape == 'round' else '#263f42'
        font_size = '14px'
        padding = '5px 8px'
        background = 'rgba(255,255,255,.94)'
    label = escape(label_text_for_group(records))
    return (
        "<div style=\""
        f"background:{background};"
        f"border:2px solid {border_color};"
        f"border-radius:{radius};"
        "box-shadow:0 2px 7px rgba(20,30,28,.18);"
        "box-sizing:border-box;"
        "color:#20211f;"
        "display:inline-block;"
        "font-family:'Songti SC','STSong','Noto Serif CJK TC','Noto Serif CJK SC',serif;"
        f"font-size:{font_size};"
        "line-height:1.1;"
        f"padding:{padding};"
        "text-align:center;"
        "white-space:nowrap;"
        "\">"
        f"{label}"
        "</div>"
    )


def label_marker_for_group(location, records):
    return folium.Marker(
        location=list(location),
        pane='chgisHistoricalOverlayPane',
        icon=folium.DivIcon(
            html=label_html_for_group(records),
            class_name='historical-place-label-icon',
            icon_size=None,
        ),
        popup=folium.Popup(grouped_popup(records), max_width=660),
        tooltip=grouped_tooltip(records),
    )


def marker_for_group(location, records, label_markers=False):
    if label_markers:
        return label_marker_for_group(location, records)
    return folium.Marker(
        location=list(location),
        pane='chgisHistoricalOverlayPane',
        icon=marker_icon_for_group(records),
        popup=folium.Popup(grouped_popup(records), max_width=660),
        tooltip=grouped_tooltip(records),
    )


def polygon_popup_html(feature):
    props = feature.get('properties', {})
    name = escape(str(props.get('name') or ''))
    record_type = escape(str(props.get('type') or ''))
    sys_id = escape(str(props.get('sys_id') or ''))
    begin_year = escape(str(props.get('begin_year') or ''))
    end_year = escape(str(props.get('end_year') or ''))
    return (
        "<div style='font-size:14px;min-width:220px;'>"
        f"<strong>{name}</strong>"
        f"<div style='color:#555;margin-top:4px;'>{record_type} {begin_year}-{end_year}</div>"
        f"<div style='color:#555;'>CHGIS V6 {sys_id}</div>"
        "</div>"
    )


def add_prefecture_polygon_layer(folium_map, date_filter, bounds, locations=None, features=None):
    features = features if features is not None else year_relevant_prefecture_polygons(date_filter, bounds, locations=locations)
    if not features:
        return 0

    feature_collection = {
        'type': 'FeatureCollection',
        'features': features,
    }
    polygon_layer = folium.FeatureGroup(name='Year-relevant CHGIS prefecture polygons', control=True, show=True)
    geo_json = folium.GeoJson(
        feature_collection,
        name='Year-relevant CHGIS prefecture polygons',
        style_function=lambda _feature: {
            'color': '#465f63',
            'weight': 1.15,
            'opacity': 0.7,
            'fillColor': '#9ab3a5',
            'fillOpacity': 0.03,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=['name', 'type', 'begin_year', 'end_year'],
            aliases=['Name', 'Type', 'Begin', 'End'],
            sticky=True,
        ),
        popup=folium.GeoJsonPopup(
            fields=['name', 'type', 'sys_id', 'begin_year', 'end_year'],
            aliases=['Name', 'Type', 'CHGIS V6', 'Begin', 'End'],
            localize=True,
        ),
    )
    geo_json.add_to(polygon_layer)
    polygon_layer.add_to(folium_map)
    folium_map.add_child(ChgisSelectablePolygonLayer(folium_map, polygon_layer, geo_json))
    return len(features)


def add_chgis_plain_base_layer(folium_map):
    base_layer = folium.FeatureGroup(name='plain base - CHGIS 1820', overlay=False, control=True, show=True)
    base_layer.add_to(folium_map)
    folium_map.add_child(ChgisPlainBaseLoader(folium_map, base_layer, '/CHGIS_plain_base.geojson'))


# Generate the map
def generate_map(
    data,
    center=None,
    zoom_start=None,
    fit_bounds=True,
    label_markers=False,
    date_filter=None,
    polygon_features=None,
    fit_bounds_padding=(40, 40),
    fit_bounds_max_zoom=None,
):
    # Create a map object centered on a specific location

    #maps from https://leaflet-extras.github.io/leaflet-providers/preview/
    #https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}
    #https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png
    #https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}

    m = folium.Map(
        tiles=None,
        location=center or MAP_CENTER,
        zoom_start=zoom_start or MAP_ZOOM_START,
        max_zoom=MAP_MAX_ZOOM,
    )
    m.add_child(ChgisMapPanes(m))
    shaded_layer = folium.TileLayer(
        tiles=MAP_TILE_URL,
        name='Shaded relief',
        attr=MAP_ATTRIBUTION,
        max_zoom=MAP_MAX_ZOOM,
        control=True,
        opacity=1,
        z_index=1,
        show=False,
    )
    shaded_layer.add_to(m)
    topo_layer = folium.TileLayer(
        tiles=TOPO_MAP_TILE_URL,
        name='Neutral topo map',
        attr=MAP_ATTRIBUTION,
        max_zoom=MAP_MAX_ZOOM,
        control=True,
        opacity=0.82,
        z_index=2,
        show=False,
    )
    topo_layer.add_to(m)
    add_chgis_plain_base_layer(m)
    logger.info("Map generated")

    grouped_records = grouped_location_records(data)
    locations = [location for location, _records in grouped_records]
    bounds = map_bounds_for_locations(locations)
    if grouped_records:
        add_prefecture_polygon_layer(m, date_filter, bounds, locations=locations, features=polygon_features)
    if label_markers:
        marker_layer = folium.FeatureGroup(name='Historical places', control=False).add_to(m)
        for location, records in grouped_records:
            marker = marker_for_group(location, records, label_markers=True)
            marker_layer.add_child(marker)
            m.add_child(ChgisSelectableMapLayer(marker, marker_layer, record_ids_for_group(records)))
    else:
        marker_cluster = MarkerCluster(
            disableClusteringAtZoom=CLUSTER_DISABLE_AT_ZOOM,
            spiderfyOnMaxZoom=True,
            zoomToBoundsOnClick=False,
            showCoverageOnHover=False,
            maxClusterRadius=35,
        ).add_to(m)
        m.add_child(ClusterClickSpiderfy(marker_cluster))
        for location, records in grouped_records:
            marker = marker_for_group(location, records)
            marker_cluster.add_child(marker)
            m.add_child(ChgisSelectableMapLayer(marker, marker_cluster, record_ids_for_group(records)))
        m.add_child(marker_cluster)
    if fit_bounds and bounds:
        fit_options = {'padding': fit_bounds_padding}
        if fit_bounds_max_zoom is not None:
            fit_options['max_zoom'] = fit_bounds_max_zoom
        m.fit_bounds(bounds, **fit_options)
    folium.LayerControl().add_to(m)

    #m.add_child(folium.LatLngPopup())

    return m



if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", "5001")))
