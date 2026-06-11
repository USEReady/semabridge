import re

# Patch tmdl_to_osi.py
file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\converter\tmdl_to_osi.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

if 'from semabridge.converter.fabric_utils import FabricModelUtils' not in content:
    content = content.replace('from semabridge.core.interfaces import BaseConverter', 'from semabridge.core.interfaces import BaseConverter\nfrom semabridge.converter.fabric_utils import FabricModelUtils')

content = re.sub(
    r'self\._map_tmdl_data_type\((.*?)\)',
    r'OSIDataType[FabricModelUtils.map_data_type(\1)]',
    content
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)


# Patch tmsl_to_osi.py
file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\converter\tmsl_to_osi.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

if 'from semabridge.converter.fabric_utils import FabricModelUtils' not in content:
    content = content.replace('from semabridge.core.interfaces import BaseConverter', 'from semabridge.core.interfaces import BaseConverter\nfrom semabridge.converter.fabric_utils import FabricModelUtils')

content = re.sub(
    r'self\._map_tmsl_data_type\((.*?)\)',
    r'OSIDataType[FabricModelUtils.map_data_type(\1)]',
    content
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Patched TMDL and TMSL")
