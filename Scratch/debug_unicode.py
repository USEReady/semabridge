import unicodedata

s1 = "Café"
s2 = "Cafe\u0301"

n1 = unicodedata.normalize("NFKC", s1)
n2 = unicodedata.normalize("NFKC", s2)

print(f"S1: {s1!r} -> N1: {n1!r}")
print(f"S2: {s2!r} -> N2: {n2!r}")
print(f"Equal? {n1 == n2}")
print(f"Lower Equal? {n1.lower() == n2.lower()}")
