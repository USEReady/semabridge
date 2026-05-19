import re
entry = 'PRODUCT.NEWCOUNT_OF_PRODUCT as COUNT_IF(PRODUCT."PRODUCT" IS NULL) with synonyms=(\'New Count Of "PRODUCT"\')'
print("Original code match:", bool(re.match(r'(\w+)\."?(\w+)"?\s+AS\s+(.+)', entry, re.IGNORECASE)))
