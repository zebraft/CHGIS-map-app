import re

print("hellO")

date_entry1 = "420-480"

date_group = [date.strip() for date in re.split('-', date_entry1)]

print(date_group)

print(f"From {date_group[0]} to {date_group[1]}")
