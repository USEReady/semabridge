import re

pattern = re.compile(r'(?<![\w\.\'\"])\b([A-Za-z_][A-Za-z0-9_$]*)\b(?![\w\.\'\"])')
print("With quotes:", pattern.findall('"MANUFACTURER_MFGISVANARSDEL"'))
print("Without quotes:", pattern.findall('MANUFACTURER_MFGISVANARSDEL'))
