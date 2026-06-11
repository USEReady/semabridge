import re

def fix_date_part(match):
    part = match.group(1).strip('"\'')
    return f"DATE_PART('{part.lower()}', "

sql_before = "DATE_PART('\"YEAR\"', COL_DATE)"

# Test with ? (zero-or-one quote)
fixed_question = re.sub(r"DATE_PART\s*\(\s*['\"]?([A-Za-z_]+)['\"]?\s*,", fix_date_part, sql_before, flags=re.IGNORECASE)

# Test with * (zero-or-more quotes)
fixed_star = re.sub(r"DATE_PART\s*\(\s*['\"]*([A-Za-z_]+)['\"]*\s*,", fix_date_part, sql_before, flags=re.IGNORECASE)

print("INPUT:", sql_before)
print("WITH '?' MATCH:", fixed_question)
print("WITH '*' MATCH:", fixed_star)
