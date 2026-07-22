# CHGIS Map

## Use
- a quick and easy way to see premodern Chinese locations on a map
- a geographic workspace for passages that may contain historical places, reign dates, and title-like location context
- Testing at https://zpyany.pythonanywhere.com/CHGIS_map

## Features
- uses CHGIS (China Historical Geographical Information System) data (v6), with a local TGAZ supplement for placename lookup gaps
- plots locations on a terrain/relief map
- shows prefectures (郡 etc) and counties (縣); these can be toggled
- enter a single place name, or a list separated by commas (, or ，)
- paste a text passage and get candidate places and dates back beside the map
- and/or choose by year
- if you enter a place name with no year, it will show all points for that name
- if you enter a year with no place names, it will show all places in that year

## What you see
- prefectures are marked by blue stars, counties with red circles
- if points overlap -- whether because they are in the same location, or because you are too zoomed out too far -- they collapse and a number is shown; click on the number to zoom in
- float over a point and the "tooltip" tells you the name and the time period it existed, along with an explanation of what the starting and ending dates refer to
- click on a point and there is a link to the CHGIS data; their 考證 is in there, along with other info, displayed with a modern map

## etc
- See also, their API: https://maps.cga.harvard.edu/tgaz/?.
- It's slow. I'm cheap.

## Data Notes

The app treats CHGIS v6 as the primary local spatial source. The local v6 files live in this app at:

`data/CHGISv6-2021/`

The app also uses a local public TGAZ dump as a supplemental placename source:

`../pre 2023 DH archive/DH work/CHGIS/tgaz_bak_2018/tgaz_bak_2018.sql`

This is not an arbitrary replacement for CHGIS v6. CHGIS v6 shapefiles are the main GIS/spatial layers, but the CHGIS site describes the current placename database as the Temporal Gazetteer/MariaDB. The public TGAZ repository includes `tgaz_bak_2018.zip`, published 2019-02-21, as the complete MySQL backend dump. The live TGAZ/API may include later corrections, so local TGAZ records should be understood as a public SQL snapshot rather than the final live authority.

Generated local files:

- `data/CHGIS_v6_places.csv`: direct local export from CHGIS v6 point layers
- `data/CHGIS_tgaz_supplement.csv`: TGAZ SQL records missing from the v6 point export
- `data/CHGIS_places.csv`: combined lookup file used by the app
- `data/chgis_places.sqlite`: SQLite copy of the combined lookup data
- `data/CHGISv6-2021/v6_time_pref_pgn_utf_wgs84.*`: local time-aware prefecture polygon layer used for year-relevant map overlays

Practical rule: use CHGIS v6 for map geometry and ordinary spatial records; use the TGAZ supplement for local name lookup and gaps; check the live TGAZ/API for important mismatches or uncertain records.

Possible future layer: Harvard Dataverse DOI `10.7910/DVN/E1FHML` provides the CHGIS V5 DEM, a 30-arc-second (~1 km) elevation raster based on USGS GTOPO-30, with QGIS/ArcGIS support files. It may be worth testing as a local `CHGIS DEM relief` layer, but it is not clear that it would improve much on the current combination of `plain base - CHGIS 1820`, shaded relief, and neutral topo. Treat this as optional future work rather than a priority.

## Chronicle App

The `資治通鑑` / `續資治通鑑` browser, markup, audit, feedback, and person authority workflows now live in the sibling directory:

`../ChineseChronicle/`

This app remains responsible for CHGIS/TGAZ place lookup, geographic filtering, and map generation.
