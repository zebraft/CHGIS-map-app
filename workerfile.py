import re
import requests
import pandas as pd

print("hellO")

#NB, turns out that 'sys_id' does match up with 'hvd' ids in the api!!!

def tooltip_maker(row): # invoke when looping through 'filtered_data' dataframe

    #get basic data from the dataframe
    name = row['NAME_FT']
    began = row['BEG_YR']
    if row['BEG_CHG_TY']:
        began_reason = row['BEG_CHG_TY'] 
    else:
        began_reason = "（起）"
    ended = row['END_YR']
    if row['END_CHG_TY']:
        ended_reason = row['END_CHG_TY']
    else:
        ended_reason = "（訖）"
    system_id = row['SYS_ID'] # to use with api, below

    # feed data to api
    api_url = f"https://maps.cga.harvard.edu/tgaz/placename/json/hvd_{system_id}"
    print(api_url)
    response = requests.get(api_url)
    if response.status_code == 200:
        api_data = response.json()
    else:
        # Handle API request error
        print(f"{system_id}, {name} not found in API")
        logger.error(f"{system_id}, {name} not found in API")
        return f"{name}\n{began}{began_reason}\n{ended}{ended_reason}"
    
    # Extract information from "part of"
    part_of = []
    sub_units = []

    part_of_data = api_data.get('historical_context', {}).get('part of', [])
    for item in part_of_data:
        upper_name = item.get('name')
        begin_year = item.get('begin year')
        end_year = item.get('end year')
        part_of.append((upper_name, begin_year, end_year))

    # Extract information from "subordinate units"
    sub_units_data = api_data.get('historical_context', {}).get('subordinate units', [])
    for item in sub_units_data:
        lower_name = item.get('name')
        begin_year = item.get('begin year')
        end_year = item.get('end year')
        sub_units.append((lower_name, begin_year, end_year))

    part_of_string = "\n".join([f"{data[0]}, {data[1]}-{data[2]}" for data in part_of])
    sub_unit_string = "\n".join([f"{data[0]}, {data[1]}-{data[2]}" for data in sub_units])

    # now return all the tooltip data
    print(f"{name}\n{began}{began_reason}\n{ended}{ended_reason}\nIs part of: {part_of_string}\nSub-units: {sub_unit_string}")

    return f"{name}\n{began}{began_reason}\n{ended}{ended_reason}\nIs part of: {part_of_string}\nSub-units: {sub_unit_string}"


lister = "2628,Yanling Xian,傿陵县,傿陵縣,114.17869,34.18715,今河南鄢陵县西北古城,Xian,县,6,-202,,-196,,44348,POINT,44348,FROM_FD,,,,,新建,更名,POINT (114.17869000007009 34.18714999995262)".split(",")
columns = "ID_,NAME_PY,NAME_CH,NAME_FT,X_COOR,Y_COOR,PRES_LOC,TYPE_PY,TYPE_CH,LEV_RANK,BEG_YR,BEG_RULE,END_YR,END_RULE,NOTE_ID,OBJ_TYPE,SYS_ID,GEO_SRC,COMPILER,GECOMPLR,CHECKER,ENT_DATE,BEG_CHG_TY,END_CHG_TY,geometry".split(',')
df = pd.DataFrame(lister, columns)

#print(df[0])

tooltip_maker(df[0])

# # Inside your existing code
# tooltip = f"<div style='font-size: 20px;'>{row['NAME_FT']}\n{row['BEG_YR']}{row['BEG_CHG_TY']}\n{row['END_YR']}{row['END_CHG_TY']}"
# value_from_dataframe = row['VALUE']  # Replace 'VALUE' with the appropriate column name
# additional_info = get_additional_info(value_from_dataframe)

# if additional_info is not None:
#     tooltip += f"\nAdditional Info: {additional_info}"





# date_entry1 = "420-480"

# date_group = [date.strip() for date in re.split('-', date_entry1)]

# print(date_group)

# print(f"From {date_group[0]} to {date_group[1]}")
