from flask import Flask, render_template, request, redirect
import folium
from folium.plugins import MarkerCluster
import re
import pandas as pd
import logging


app = Flask(__name__)


# Create a logger instance
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)
formatter = logging.Formatter('%(levelname)s - %(message)s')

# Create a file handler and set the formatter
file_handler = logging.FileHandler('error.log')
file_handler.setFormatter(formatter)

# Add the file handler to the logger
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

        print("User Input:")
        print("Place Names:", place_names)
        print("Date:", date)
        print("Date Range:", date_range)
        print("Prefectures:", prefectures)
        print("Counties:", counties)

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

    
    print(f"Date range is {begin_date} to {end_date}")

    # # old way of handling dates - single date only
    # if date:
    #     date = int(date)

    # Filter the prefectures data
    if prefectures:
        #filtered_prefectures = prefectures_df[prefectures_df['NAME_FT'].isin(place_names)]
        filtered_prefectures = prefectures_df[prefectures_df['NAME_FT'].apply(lambda x: any(name in x for name in place_names))]
        if date or date_range:
            filtered_prefectures = filtered_prefectures[
                (filtered_prefectures['BEG_YR'] <= end_date) & (filtered_prefectures['END_YR'] >= begin_date)
            ]
            #print("Date processed -- prefectures!")
        filtered_data = pd.concat([filtered_data, filtered_prefectures])
        print(f"Number of prefectures returned: {filtered_data.shape[0]}")

    # Filter the counties data
    if counties:
        #filtered_counties = counties_df[counties_df['NAME_FT'].isin(place_names)]
        filtered_counties = counties_df[counties_df['NAME_FT'].apply(lambda x: any(name in x for name in place_names))]
        if date or date_range:
            filtered_counties = filtered_counties[
                (filtered_counties['BEG_YR'] <= end_date) & (filtered_counties['END_YR'] >= begin_date)
            ]
            #print("date processed - counties!")
        filtered_data = pd.concat([filtered_data, filtered_counties])
        print(f"Number of counties returned: {filtered_data.shape[0]}")

    #print("Data filtered ok!")
    #print(filtered_data)
    return filtered_data
    

# Generate the map
def generate_map(data):
    # Create a map object centered on a specific location
    center = [30.85158, 120.10989]  # Center on the first location (or 33.86989, 109.93246)

    m = folium.Map(tiles='Stamen Terrain', location=center, zoom_start=6)
    print("map generated!")

    marker_cluster = MarkerCluster(disableClusteringAtZoom=10).add_to(m)
    
    # Add markers for each place
    for index, row in data.iterrows():
        if row['LEV_RANK'] == 3:  # Check if it's a prefecture (LEV_RANK 3)
            # Add a star marker for prefectures
            marker = folium.Marker(
                location=[row['Y_COOR'], row['X_COOR']],
                icon=folium.Icon(icon='star', prefix='fa', color='blue'),
                draggable=True,
                popup = f"<a href='https://maps.cga.harvard.edu/tgaz/placename?n={row['NAME_FT']}' target='_blank'>Link to CHGIS</a>",
                tooltip=f"<div style='font-size: 20px;'>{row['NAME_FT']}\n{row['BEG_YR']}{row['BEG_CHG_TY']}\n{row['END_YR']}{row['END_CHG_TY']}",
                name='name'
                )
            marker_cluster.add_child(marker)
            #print("Added prefecture!")
        elif row['LEV_RANK'] == 6:  # Check if it's a county (LEV_RANK 6)
            # Add a circle marker for counties
            marker = folium.CircleMarker(
                location=[row['Y_COOR'], row['X_COOR']],
                radius=5,
                color='red',
                fill=True,
                fill_color='red',
                fill_opacity=1.0,
                draggable=True,
                popup = f"<a href='https://maps.cga.harvard.edu/tgaz/placename?n={row['NAME_FT']}' target='_blank'>Link to CHGIS</a>",
                tooltip=f"<div style='font-size: 20px;'>{row['NAME_FT']}\n{row['BEG_YR']}{row['BEG_CHG_TY']}\n{row['END_YR']}{row['END_CHG_TY']}",
                name='name'
                )
            marker_cluster.add_child(marker)
            #print("Added county!")
        else:
            # Log an error for unrecognized results
            logger.error(f"Unrecognized LEV_RANK value: {row['LEV_RANK']}")
            print(f"Level Rank wrong for {row}!")
        
    m.add_child(marker_cluster)

    #m.add_child(folium.LatLngPopup())

    return m



if __name__ == "__main__":
    app.run(debug=True)

