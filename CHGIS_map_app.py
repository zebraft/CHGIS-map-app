from flask import Flask, render_template, request
import folium
from branca.element import MacroElement, Template
from folium.plugins import MarkerCluster
from markupsafe import Markup
import re
import pandas as pd
import logging
from functools import lru_cache
from html import escape
from urllib.parse import quote


app = Flask(__name__)

CHGIS_PLACENAME_URL = 'https://chgis.hudci.org/tgaz/placename'
MAP_TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}'
PHYSICAL_MAP_TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Physical_Map/MapServer/tile/{z}/{y}/{x}'
MAP_ATTRIBUTION = 'Tiles &copy; Esri'
MAP_CENTER = [30.85158, 120.10989]
MAP_ZOOM_START = 6
CLUSTER_DISABLE_AT_ZOOM = 13
MAP_MAX_ZOOM = CLUSTER_DISABLE_AT_ZOOM
BASEMAP_SWITCH_ZOOM = 8
MIN_TEXT_MATCH_LENGTH = 2
MAX_RENDER_ROWS = 1000
SINGLE_POINT_BOUNDS_BUFFER = 0.75
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
        submitted_selected_places = request.form.getlist('selected_places')
        detected_selection_submitted = bool(request.form.get('detected_selection_submitted'))

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

        matched_places = extract_place_names(source_text, prefectures, counties)
        selected_extracted_names = selected_detected_names(
            matched_places,
            submitted_selected_places,
            detected_selection_submitted,
        )
        candidate_query_names = combine_place_names('', [place['name'] for place in matched_places])
        candidate_data = filter_data(
            candidate_query_names,
            date_filter,
            prefectures,
            counties,
            allow_date_only=False,
        )
        matched_places = annotate_matched_places(matched_places, candidate_data, selected_extracted_names)
        matched_places = [place for place in matched_places if place['result_count'] > 0]
        query_names = combine_place_names(place_names, selected_extracted_names)

        # Process the user input and generate the map
        allow_date_only = not (source_text or '').strip() and not split_place_names(place_names)
        filtered_data = filter_data(query_names, date_filter, prefectures, counties, allow_date_only=allow_date_only)
        mappable_data, unmappable_rows = split_mappable_rows(filtered_data)

        result_warning = result_size_warning(mappable_data, len(unmappable_rows))
        map_data = mappable_data.head(MAX_RENDER_ROWS)

        generated_map = generate_map(map_data)

        # Convert the map to html string
        map_html = generated_map.get_root().render()

        # Pass the map HTML to the template
        template_data = {
            'map_data': map_html,
            'matched_places': matched_places,
            'highlighted_source_text': highlighted_source_text(source_text, matched_places),
            'unmappable_places': rows_to_place_records(unmappable_rows),
            'result_warning': result_warning,
            'form_data': request.form,
        }

        return render_template('CHGIS_map.html', **template_data)

    # Handle GET requests by rendering the index page
    return render_template('CHGIS_index.html')


# Load the datasets
prefectures_df = pd.read_csv('data/CHGIS_prefectures.csv')
counties_df = pd.read_csv('data/CHGIS_counties.csv')


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


def selected_detected_names(matched_places, submitted_selected_places, detected_selection_submitted=False):
    matched_names = [place['name'] for place in matched_places]
    if not matched_names:
        return []

    if detected_selection_submitted:
        selected_names = set(submitted_selected_places)
        return [name for name in matched_names if name in selected_names]

    return matched_names


def alias_only_match(place):
    variants = set(place.get('variants', []))
    alias_variants = set(place.get('alias_variants', []))
    return bool(alias_variants) and variants == alias_variants


def highlighted_source_text(source_text, matched_places):
    if not source_text:
        return ''

    variant_places = {}
    for place in matched_places:
        for variant in place.get('variants', []):
            variant_places.setdefault(variant, set()).add(place['name'])

    if not variant_places:
        return escape(source_text)

    matches = []
    occupied_ranges = []
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
                'variant': variant,
                'places': sorted(variant_places[variant]),
            })

    if not matches:
        return escape(source_text)

    chunks = []
    cursor = 0
    for match in sorted(matches, key=lambda item: item['start']):
        chunks.append(escape(source_text[cursor:match['start']]))
        place_names = "|".join(match['places'])
        chunks.append(
            "<mark class='source-place' data-place-names='"
            + escape(place_names, quote=True)
            + "'>"
            + escape(source_text[match['start']:match['end']])
            + "</mark>"
        )
        cursor = match['end']
    chunks.append(escape(source_text[cursor:]))

    return Markup("".join(chunks))


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


def extract_place_names(source_text, prefectures='prefectures', counties='counties'):
    if not source_text:
        return []

    occupied_ranges = []
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
                    },
                )
                match_record['variants'].add(variant)
                if name in entry['alias_names']:
                    match_record['alias_variants'].add(variant)
                match_record['count'] += 1

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
        })

    return sorted(matched_places, key=lambda place: (-place['count'], place['name']))

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
        filtered_prefectures = prefectures_df[prefectures_df['NAME_FT'].apply(lambda x: row_matches_place_names(x, place_names))]
        if date_filter:
            filtered_prefectures = filtered_prefectures[
                (filtered_prefectures['BEG_YR'] <= end_date) & (filtered_prefectures['END_YR'] >= begin_date)
            ]
        filtered_data = pd.concat([filtered_data, filtered_prefectures])
        logger.info("Number of prefectures returned: %s", filtered_prefectures.shape[0])

    # Filter the counties data
    if counties:
        #filtered_counties = counties_df[counties_df['NAME_FT'].isin(place_names)]
        filtered_counties = counties_df[counties_df['NAME_FT'].apply(lambda x: row_matches_place_names(x, place_names))]
        if date_filter:
            filtered_counties = filtered_counties[
                (filtered_counties['BEG_YR'] <= end_date) & (filtered_counties['END_YR'] >= begin_date)
            ]
        filtered_data = pd.concat([filtered_data, filtered_counties])
        logger.info("Number of counties returned: %s", filtered_counties.shape[0])

    return filtered_data


def row_has_coordinates(row):
    return not pd.isna(row['Y_COOR']) and not pd.isna(row['X_COOR'])


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
            'type': row.get('TYPE_CH', ''),
            'begin_year': row.get('BEG_YR', ''),
            'end_year': row.get('END_YR', ''),
            'url': chgis_placename_url(row.get('NAME_FT', '')),
        })
    return records


def annotate_matched_places(matched_places, filtered_data, selected_names):
    selected_name_set = set(selected_names)
    if filtered_data.empty:
        result_counts = {}
        mapped_counts = {}
        unmappable_counts = {}
    else:
        result_counts = filtered_data.groupby('NAME_FT').size().to_dict()
        mappable_data, unmappable_rows = split_mappable_rows(filtered_data)
        mapped_counts = mappable_data.groupby('NAME_FT').size().to_dict() if not mappable_data.empty else {}
        unmappable_data = pd.DataFrame(unmappable_rows)
        unmappable_counts = unmappable_data.groupby('NAME_FT').size().to_dict() if not unmappable_data.empty else {}

    annotated_places = []
    for place in matched_places:
        name = place['name']
        annotated_place = dict(place)
        annotated_place['result_count'] = int(result_counts.get(name, 0))
        annotated_place['mapped_count'] = int(mapped_counts.get(name, 0))
        annotated_place['unmappable_count'] = int(unmappable_counts.get(name, 0))
        annotated_place['selected'] = name in selected_name_set and annotated_place['result_count'] > 0
        annotated_places.append(annotated_place)

    return annotated_places


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


def chgis_popup(place_name):
    return f"<a href='{chgis_placename_url(place_name)}' target='_blank'>Link to CHGIS</a>"


def tooltip_for_row(row):
    if pd.isna(row['BEG_CHG_TY']):
        return f"<div style='font-size: 20px;'>{row['NAME_FT']}\n{row['BEG_YR']} to {row['END_YR']}"

    return f"<div style='font-size: 20px;'>{row['NAME_FT']}\n{row['BEG_YR']}{row['BEG_CHG_TY']}\n{row['END_YR']}{row['END_CHG_TY']}"


def marker_for_row(row):
    if pd.isna(row['Y_COOR']) or pd.isna(row['X_COOR']):
        logger.warning("Skipping row with missing coordinates: %s", row['NAME_FT'])
        return None

    marker_args = {
        'location': [row['Y_COOR'], row['X_COOR']],
        'draggable': True,
        'popup': chgis_popup(row['NAME_FT']),
        'tooltip': tooltip_for_row(row),
    }

    if row['LEV_RANK'] == 3:
        return folium.Marker(
            icon=folium.Icon(icon='star', prefix='fa', color='blue'),
            **marker_args
        )

    if row['LEV_RANK'] == 6:
        return folium.CircleMarker(
            radius=5,
            color='red',
            fill=True,
            fill_color='red',
            fill_opacity=1.0,
            **marker_args
        )

    return None


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
        record_type = escape(str(type_label(row)))
        years = escape(str(date_label(row)))
        url = escape(chgis_placename_url(row.get('NAME_FT', '')), quote=True)
        rows.append(
            "<tr>"
            f"<td style='padding:4px 12px 4px 0;white-space:nowrap;'><a href='{url}' target='_blank'>{name}</a></td>"
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
    if len(records) == 1:
        return f"<div style='font-size: 18px;'>{escape(first_name)}</div>"
    return f"<div style='font-size: 18px;'>{escape(first_name)} and {len(records) - 1} more</div>"


def marker_icon_for_group(records):
    levels = {row.get('LEV_RANK') for row in records}
    if 3 in levels and 6 in levels:
        return folium.Icon(icon='map-marker', prefix='fa', color='green')
    if 3 in levels:
        return folium.Icon(icon='star', prefix='fa', color='blue')
    return folium.Icon(icon='circle', prefix='fa', color='red')


def marker_for_group(location, records):
    return folium.Marker(
        location=list(location),
        icon=marker_icon_for_group(records),
        popup=folium.Popup(grouped_popup(records), max_width=660),
        tooltip=grouped_tooltip(records),
    )


# Generate the map
def generate_map(data):
    # Create a map object centered on a specific location

    #maps from https://leaflet-extras.github.io/leaflet-providers/preview/
    #https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}
    #https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png
    #https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}

    m = folium.Map(
        tiles=None,
        location=MAP_CENTER,
        zoom_start=MAP_ZOOM_START,
        max_zoom=MAP_MAX_ZOOM,
    )
    physical_layer = folium.TileLayer(
        tiles=PHYSICAL_MAP_TILE_URL,
        name='Physical map',
        attr=MAP_ATTRIBUTION,
        max_zoom=MAP_MAX_ZOOM,
        control=False,
        opacity=1,
        z_index=1,
    )
    physical_layer.add_to(m)
    shaded_layer = folium.TileLayer(
        tiles=MAP_TILE_URL,
        name='Shaded relief',
        attr=MAP_ATTRIBUTION,
        max_zoom=MAP_MAX_ZOOM,
        control=False,
        opacity=0,
        z_index=2,
    )
    shaded_layer.add_to(m)
    m.add_child(BaseLayerAutoSwitch(m, physical_layer, shaded_layer, BASEMAP_SWITCH_ZOOM))
    logger.info("Map generated")

    marker_cluster = MarkerCluster(
        disableClusteringAtZoom=CLUSTER_DISABLE_AT_ZOOM,
        spiderfyOnMaxZoom=True,
        zoomToBoundsOnClick=False,
        showCoverageOnHover=False,
        maxClusterRadius=35,
    ).add_to(m)
    m.add_child(ClusterClickSpiderfy(marker_cluster))

    grouped_records = grouped_location_records(data)
    for location, records in grouped_records:
        marker_cluster.add_child(marker_for_group(location, records))
        
    m.add_child(marker_cluster)
    bounds = map_bounds_for_locations([location for location, _records in grouped_records])
    if bounds:
        m.fit_bounds(bounds, padding=(40, 40))
    folium.LayerControl().add_to(m)

    #m.add_child(folium.LatLngPopup())

    return m



if __name__ == "__main__":
    app.run(debug=True)
