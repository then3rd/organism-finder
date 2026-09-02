"""Search the LANDFIRE EVT attribute table by keyword.

EVT class names are plant *community* names, so which ones count as a given tree is a
judgment call. Run this before adding a species to scripts/species.py:

    just evt-classes pinyon
    just evt-classes ponderosa jeffrey

With no keywords, shows what each registered taxon currently selects. A taxon screened
from occurrence records has no EVT keywords at all and is listed as such - LANDFIRE names
woody communities, so nothing herbaceous will ever match here.
"""
from pathlib import Path
import csv
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
import species as species_mod  # noqa: E402
from common import get  # noqa: E402

EVT_CSV = "https://landfire.gov/sites/default/files/CSV/LF2023/LF23_EVT_240.csv"


def rows():
    csv_path = paths.evt_csv()
    if not csv_path.exists():
        print(f"fetching {EVT_CSV} ...", flush=True)
        csv_path.write_bytes(get(EVT_CSV).content)
    return list(csv.DictReader(open(csv_path)))


def main():
    table = rows()
    keywords = [k.lower() for k in sys.argv[1:]]

    if not keywords:
        print(f"{len(table)} EVT classes; registered taxa select:\n")
        for sp in species_mod.TAXA.values():
            if sp.cover != species_mod.EVT:
                print(f"  {sp.slug:10}   -  {'':8}  {sp.common_name:<28} "
                      f"[screened from {sp.cover} records]")
                continue
            hits = [r for r in table if sp.matches(r["EVT_NAME"])]
            kw = " ".join(sp.evt_include)
            kw += "".join(f" -{x}" for x in sp.evt_exclude)
            print(f"  {sp.slug:10} {len(hits):>3} classes  {sp.common_name:<28} [{kw}]")
        print("\nPass keywords to see the class names themselves.")
        return

    for kw in keywords:
        hits = [r for r in table if kw in r["EVT_NAME"].lower()]
        print(f"\n=== {kw!r}: {len(hits)} classes ===")
        for r in hits:
            print(f"  {int(r['VALUE']):>5}  {r['EVT_NAME']}   [{r['EVT_PHYS']}]")
    if len(keywords) > 1:
        union = {r["VALUE"] for r in table
                 if any(k in r["EVT_NAME"].lower() for k in keywords)}
        print(f"\nunion of all {len(keywords)} keywords: {len(union)} classes")


if __name__ == "__main__":
    main()
