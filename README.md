# Chinese historical geography

## Use
- a quick and easy way to see premodern Chinese locations on a map
- Testing at https://zpyany.pythonanywhere.com/CHGIS_map

## Features
- uses CHGIS (China Historical Geographical Information System) data (v6)
- plotted on Stamen Terrain map (less info, but clean to look at...)
- shows prefectures (郡 etc) and counties (縣); these can be toggled
- enter a single place name, or a list separated by commas (, or ，)
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
