"""Fold change high-fat vs control. Snapshot of analysis code."""
import csv, math, sys
rows = list(csv.reader(open(sys.argv[1]), delimiter="\t"))
for r in rows[1:]:
    c = sum(map(float, r[1:4])) / 3
    t = sum(map(float, r[4:7])) / 3
    print(r[0], round(math.log2(max(t, 1) / c), 2))
