from flask import Flask, render_template, request, redirect
import folium
from folium.plugins import MarkerCluster
import re
import pandas as pd
import logging
from urllib.parse import quote


app = Flask(__name__)

CHGIS_PLACENAME_URL = 'https://chgis.hudci.org/tgaz/placename'
MAP_TILE_URL = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}'
MAP_ATTRIBUTION = 'Tiles &copy; Esri'
MAP_CENTER = [30.85158, 120.10989]
MAP_ZOOM_START = 6
CLUSTER_DISABLE_AT_ZOOM = 13
MAP_MAX_ZOOM = CLUSTER_DISABLE_AT_ZOOM


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
        # Handle form submission and retrieve user input
        # Process the user input and redirect to the map page
        return redirect("/CHGIS_map")
    return render_template("CHGIS_index.html")

# Map page route
@app.route("/CHGIS_map", methods=["GET", "POST"])
def chgis_map():
    if request.method == 'POST':
        # Retrieve user input from the form
        place_names = request.form.get('place_names')
        date = request.form.get('date')
        date_range = request.form.get('date_range')
        prefectures = request.form.get('prefectures')
        counties = request.form.get('counties')

        logger.info(
            "User input: place_names=%s date=%s date_range=%s prefectures=%s counties=%s",
            place_names,
            date,
            date_range,
            prefectures,
            counties,
        )

        # Process the user input and generate the map
        filtered_data = filter_data(place_names, date, date_range, prefectures, counties)

        generated_map = generate_map(filtered_data)

        # Convert the map to html string
        map_html = generated_map.get_root().render()

        # Pass the map HTML to the template
        template_data = {
            'map_data': map_html
        }

        return render_template('CHGIS_map.html', **template_data)

    # Handle GET requests by rendering the index page
    return render_template('CHGIS_index.html')


# Load the datasets
prefectures_df = pd.read_csv('data/CHGIS_prefectures.csv')
counties_df = pd.read_csv('data/CHGIS_counties.csv')

# Process user input and filter the data
def filter_data(place_names, date, date_range, prefectures, counties):
    filtered_data = pd.DataFrame()  # Create an empty DataFrame for the filtered data

    # split up place names, if necessary
    pattern = r",|，"
    if re.search(pattern, place_names):
        # Split the place_names using the regular expression pattern
        place_names = [name.strip() for name in re.split(pattern, place_names)]
    else:
        # Single place name, convert it to a list
        place_names = [place_names]

    # single date
    if date:
        begin_date = int(date)
        end_date = int(date)

  
    if date_range:
        date_group = [date.strip() for date in re.split(pattern, date_range)]
        begin_date = int(date_group[0])
        end_date = int(date_group[1])

    # Filter the prefectures data
    if prefectures:
        #filtered_prefectures = prefectures_df[prefectures_df['NAME_FT'].isin(place_names)]
        filtered_prefectures = prefectures_df[prefectures_df['NAME_FT'].apply(lambda x: any(name in x for name in place_names))]
        if date or date_range:
            filtered_prefectures = filtered_prefectures[
                (filtered_prefectures['BEG_YR'] <= end_date) & (filtered_prefectures['END_YR'] >= begin_date)
            ]
        filtered_data = pd.concat([filtered_data, filtered_prefectures])
        logger.info("Number of prefectures returned: %s", filtered_prefectures.shape[0])

    # Filter the counties data
    if counties:
        #filtered_counties = counties_df[counties_df['NAME_FT'].isin(place_names)]
        filtered_counties = counties_df[counties_df['NAME_FT'].apply(lambda x: any(name in x for name in place_names))]
        if date or date_range:
            filtered_counties = filtered_counties[
                (filtered_counties['BEG_YR'] <= end_date) & (filtered_counties['END_YR'] >= begin_date)
            ]
        filtered_data = pd.concat([filtered_data, filtered_counties])
        logger.info("Number of counties returned: %s", filtered_counties.shape[0])

    return filtered_data
    
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


# Generate the map
def generate_map(data):
    # Create a map object centered on a specific location

    #maps from https://leaflet-extras.github.io/leaflet-providers/preview/
    #https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}
    #https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png
    #https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}

    m = folium.Map(
        tiles=MAP_TILE_URL,
        location=MAP_CENTER,
        zoom_start=MAP_ZOOM_START,
        max_zoom=MAP_MAX_ZOOM,
        attr=MAP_ATTRIBUTION,
    )
    logger.info("Map generated")

    marker_cluster = MarkerCluster(
        disableClusteringAtZoom=CLUSTER_DISABLE_AT_ZOOM,
        spiderfyOnMaxZoom=True,
        showCoverageOnHover=False,
        maxClusterRadius=35,
    ).add_to(m)

    # Add markers for each place
    for index, row in data.iterrows():

        marker = marker_for_row(row)
        if marker:
            marker_cluster.add_child(marker)
        else:
            # Log an error for unrecognized results
            logger.error(f"Unrecognized LEV_RANK value: {row['LEV_RANK']}")
        
    m.add_child(marker_cluster)

    #m.add_child(folium.LatLngPopup())

    return m



if __name__ == "__main__":
    app.run(debug=True)
