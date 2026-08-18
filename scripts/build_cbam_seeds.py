"""Transcribe Annex I of Implementing Regulation (EU) 2025/2621 into two dbt seeds.

The CBAM default values are regulatory reference data: they are versioned by
amendment, not by scrape, so they belong in `dbt/seeds/` rather than behind a dlt
resource. This script exists so that the *next* amendment is a re-run rather than
a re-transcription — the Commission publishes an XLSX of the annex "for
information purposes only, while the legally binding values are set out in"
the regulation itself.

    uv run python -m scripts.build_cbam_seeds            # download, then write
    uv run python -m scripts.build_cbam_seeds --xlsx X   # from a local copy

Two seeds, because normalising the goods out is worth 1.6 MB. 12,532 value rows
share 287 distinct (product group, CN code, description) triples, and the
descriptions are long — one of them runs to 250 characters. Repeated per row they
are 1.6 MB of CSV and the same again in the warehouse table; as a dimension they
are 38 kB.

What this script deliberately does *not* do is clean the numbers. The annex is
the legal instrument, so the seed is a faithful transcription of it, defects
included, and `fct_cbam_exposure` is where the published values meet the rules
that are supposed to govern them. Three defects are known and none are ours:

- **Albania / 2523 21 00 (white Portland cement)** has `–` for direct, indirect
  and total, and 1,230 / 0,140 / 1,370 sitting in the three *mark-up* columns —
  i.e. the values shifted three columns right. It reads identically in the OJ
  text and in the Commission's XLSX, so it is the regulation that says this, not
  a transcription error. Handled by reading the mark-up columns only when a total
  is present, which lands the row on the annex's own "field shows `–` → use
  *Other countries and territories*" rule.
- **Five cement rows** (Angola grey clinker / grey Portland / grey hydraulic,
  Argentina grey clinker / grey Portland) compound the mark-up — x1.1, x1.21,
  x1.331 — where every other row adds it: x1.1, x1.2, x1.3.
- **Fertilisers carry a 1% mark-up in all three years**, not 1/2/3% and not
  10/20/30%. That is 2,416 rows and it is consistent across every country, so it
  reads as intended rather than as a defect — but it means "the mark-up" is a
  property of the product group, and a model that hardcodes 10/20/30% overstates
  every fertiliser line by 9 points in 2026 and 27 in 2028.

`dbt/seeds/_seeds.yml` and `dbt/models/marts/_marts.yml` test every one of those,
so a corrected amendment shows up as a failing test rather than as a silent change
in a published euro figure.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from urllib.request import urlopen

from modern_data_stack.paths import project_root

# "for information purposes only" per the Commission's own page; the binding text
# is the OJ. Kept here rather than in the docs because it is what the script hits.
ANNEX_XLSX_URL = (
    "https://taxation-customs.ec.europa.eu/document/download/"
    "1c05d211-80cb-4aaa-8ef0-e08005a95d7e_en"
    "?filename=DVs%20as%20adopted_v20260204%20.xlsx"
)

SEED_DIR = project_root() / "dbt" / "seeds"

# The annex's 119 countries, resolved to ISO3 once, at transcription time. The
# alternative is a fuzzy join at query time, which is the same guesswork with no
# diff to review — and the country list is part of the legal instrument, so
# pinning all of it means an amendment that adds a country fails here with its
# name rather than writing a null ISO3 into the seed.
#
# Nineteen of these needed a human. Excel truncates a sheet name at 31 characters
# and forbids some punctuation, so a few are the regulation's own name mangled
# ("Democratic Republic of the Cong", "Myanmar_Burma"); the rest are the ordinary
# gap between two publishers' country names — the World Bank calls it "Egypt,
# Arab Rep." and the Commission calls it "Egypt". The other 100 matched
# `stg_country` exactly.
SHEET_TO_ISO3 = {
    "Albania": "ALB",
    "Algeria": "DZA",
    "Angola": "AGO",
    "Argentina": "ARG",
    "Armenia": "ARM",
    "Australia": "AUS",
    "Azerbaijan": "AZE",
    "Bahrain": "BHR",
    "Bangladesh": "BGD",
    "Belarus": "BLR",
    "Benin": "BEN",
    "Bolivia": "BOL",
    "Bosnia and Herzegovina": "BIH",
    "Brazil": "BRA",
    "Brunei": "BRN",
    "Cambodia": "KHM",
    "Cameroon": "CMR",
    "Canada": "CAN",
    "Chile": "CHL",
    "China": "CHN",
    "Colombia": "COL",
    "Congo": "COG",
    "Costa Rica": "CRI",
    "Cuba": "CUB",
    "Curaçao": "CUW",
    "Côte d'Ivoire": "CIV",
    "Democratic Republic of the Cong": "COD",
    "Dominican Republic": "DOM",
    "Ecuador": "ECU",
    "Egypt": "EGY",
    "El Salvador": "SLV",
    "Equatorial Guinea": "GNQ",
    "Eritrea": "ERI",
    "Eswatini": "SWZ",
    "Ethiopia": "ETH",
    "Gabon": "GAB",
    "Georgia": "GEO",
    "Ghana": "GHA",
    "Guatemala": "GTM",
    "Haiti": "HTI",
    "Honduras": "HND",
    "Hong Kong": "HKG",
    "India": "IND",
    "Indonesia": "IDN",
    "Iran": "IRN",
    "Iraq": "IRQ",
    "Israel": "ISR",
    "Jamaica": "JAM",
    "Japan": "JPN",
    "Jordan": "JOR",
    "Kazakhstan": "KAZ",
    "Kenya": "KEN",
    "Kuwait": "KWT",
    "Kyrgyzstan": "KGZ",
    "Laos": "LAO",
    "Lebanon": "LBN",
    "Libya": "LBY",
    "Madagascar": "MDG",
    "Malaysia": "MYS",
    "Mali": "MLI",
    "Mauritania": "MRT",
    "Mauritius": "MUS",
    "Mexico": "MEX",
    "Moldova": "MDA",
    "Mongolia": "MNG",
    "Montenegro": "MNE",
    "Morocco": "MAR",
    "Mozambique": "MOZ",
    "Myanmar_Burma": "MMR",
    "Namibia": "NAM",
    "Nepal": "NPL",
    "New Zealand": "NZL",
    "Nicaragua": "NIC",
    "Niger": "NER",
    "Nigeria": "NGA",
    "North Korea": "PRK",
    "North Macedonia": "MKD",
    "Oman": "OMN",
    "Pakistan": "PAK",
    "Panama": "PAN",
    "Papua New Guinea": "PNG",
    "Paraguay": "PRY",
    "Peru": "PER",
    "Philippines": "PHL",
    "Qatar": "QAT",
    "Russia": "RUS",
    "Rwanda": "RWA",
    "Saudi Arabia": "SAU",
    "Senegal": "SEN",
    "Serbia": "SRB",
    "Sierra Leone": "SLE",
    "Singapore": "SGP",
    "South Africa": "ZAF",
    "South Korea": "KOR",
    "Sri Lanka": "LKA",
    "Sudan": "SDN",
    "Suriname": "SUR",
    "Syria": "SYR",
    "Taiwan": "TWN",
    "Tajikistan": "TJK",
    "Tanzania": "TZA",
    "Thailand": "THA",
    "Togo": "TGO",
    "Trinidad and Tobago": "TTO",
    "Tunisia": "TUN",
    "Turkmenistan": "TKM",
    "Türkiye": "TUR",
    "Uganda": "UGA",
    "Ukraine": "UKR",
    "United Arab Emirates": "ARE",
    "United Kingdom": "GBR",
    "United States": "USA",
    "Uruguay": "URY",
    "Uzbekistan": "UZB",
    "Venezuela": "VEN",
    "Vietnam": "VNM",
    "Yemen": "YEM",
    "Zambia": "ZMB",
    "Zimbabwe": "ZWE",
}

# The annex's catch-all table. It is not a country and gets no ISO3: it is the
# value an importer uses when the sourcing country is unlisted, or is listed with
# a `–` for that good. `fct_cbam_exposure` resolves both cases against it.
FALLBACK_SHEET = "_Other Countries and Territorie"
FALLBACK_LABEL = "Other countries and territories"

# `–` (en dash) is the annex's "no value". `_` and `N/A` also appear; all three
# mean the same thing and all three route to the fallback table.
#
# `see below` is the fourth and it is not a blank at all — it is prose, and it
# appears on exactly two goods: the 4-digit CN headings 3102 and 3105, whose
# numbers live in the subheading rows underneath them. 870 rows (2,610 cells)
# across the workbook. It reached the seed as a null anyway, because the old
# parser returned None for anything it could not read, so the *outcome* was
# right and the reason was luck: the same catch-all would have turned a footnote
# marker or a changed unit into a null just as quietly, and a null here is not
# an absence — `fct_cbam_exposure` reads it as "use the fallback row" and prices
# it. Listing the token is what makes the null a decision. Matched
# case-insensitively, unlike the symbols, because it is a phrase someone typed.
NO_VALUE = {"", "-", "–", "—", "_", "N/A", "n/a", "None"}
NO_VALUE_PHRASES = {"see below"}

SKIP_SHEETS = {"Overview", "Version History"}


def _text(cell: object) -> str:
    return "" if cell is None else str(cell).strip()


def _number(cell: object) -> float | None:
    """Parse an annex cell, which may be a float, an int, or a comma decimal.

    Rounded to the three decimals the OJ prints, half away from zero. The mark-up
    columns in the Commission's XLSX are live formulas, so they arrive as binary
    floats — 0.35200000000000004 for a value the regulation states as 0,352 — and
    rounding is what recovers the published figure rather than losing it.

    `round()` will not do: it rounds halves to even, and the mark-up is a
    multiplication that lands on an exact half often enough to matter. China's
    7306 40 80 is 3,300 x 1.1 x ... = 7,7165, which the OJ prints as 7,717 and
    `round()` returns as 7.716. Eleven rows across the seven countries spot-checked
    against the OJ text differed in the third decimal before this changed — small,
    but this column is multiplied by a carbon price and shown as money.

    **An unrecognised token raises rather than returning None**, which is the one
    thing this function must not get wrong. `None` is not an error downstream: it
    is the annex's own "no value here" and `fct_cbam_exposure` reads it as "use
    the fallback row". So a cell this parser merely failed to understand — a
    footnote marker, a stray unit, a thousands-separated figure that leaves two
    separators behind — would not surface as a gap. It would surface as a
    plausible euro figure, correctly computed, attributed to a country the
    regulation never assigned it to. `NO_VALUE` is the list of blanks this
    transcription accepts, and anything outside it is a change in the source that
    a person has to look at. The contract of this script is faithful
    transcription; guessing is the failure, not stopping.
    """
    raw = _text(cell)
    if raw in NO_VALUE or raw.casefold() in NO_VALUE_PHRASES:
        return None
    # Comma is the OJ's *decimal* separator (7,717 is seven point seven one
    # seven), so this is a translation and not a strip. A thousands-separated
    # figure would leave two separators behind and land in the raise below,
    # which is the right answer: this annex does not print one, and reading
    # "1,234,567" as either 1.234567 or 1234567 would be a guess.
    try:
        value = Decimal(repr(float(raw.replace(",", "."))))
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(
            f"unparsable annex cell {raw!r} — if this is a new way of writing "
            f"'no value', add it to NO_VALUE; otherwise the source has changed shape"
        ) from exc
    return float(value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def _cn_code(cell: object) -> str:
    """Normalise a CN code to the spaced form the annex prints.

    1,996 of the 12,532 cells arrive as integers because Excel typed them that
    way (28142000 for "2814 20 00"), so the same good reads two different ways
    depending on which country's sheet it is on.
    """
    raw = _text(cell)
    if raw.isdigit():
        digits = raw
    else:
        digits = re.sub(r"\s+", "", raw)
        if not digits.isdigit():
            return raw
    # CN codes are 8 digits (4+2+2); the annex also prints 6- and 4-digit
    # headings for whole subheadings, which stay as they are.
    if len(digits) == 8:
        return f"{digits[:4]} {digits[4:6]} {digits[6:]}"
    if len(digits) == 6:
        return f"{digits[:4]} {digits[4:]}"
    return digits


def _route(cell: object) -> str:
    """The production route indicator, e.g. "(C)" -> "C" and "(C)/(F)" -> "C/F".

    Annex I's footnote maps these to CBAM benchmarks: (C) carbon steel via
    BF/BOF, (E) via scrap/EAF, (K) primary aluminium, (L) secondary, and so on.
    They matter more than they look — the route, not the country's grid, is what
    separates a 0,13 tCO2e/t semi-finished steel from an 8,21.

    Stripping the outer parentheses is not enough: it leaves "C)/(F" for the
    combined routes, which then reads as two different codes in a group-by.
    """
    return "/".join(re.findall(r"\(([A-Z])\)", _text(cell)))


def _slug(text: str, limit: int = 40) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:limit].rstrip("-")


def good_key(cn_code: str, description: str) -> str:
    """A stable, readable key for a (CN code, description) pair.

    A CN code is *not* unique within a country — 2523 10 00 is both white clinker
    and grey clinker, and their default values differ by more than a factor of
    two — so the goods dimension is keyed on the pair. A slug rather than a
    surrogate integer because the seed is regenerated wholesale from each
    amendment, and a renumbered surrogate would rewrite every row of the diff
    when a single CN code is inserted.
    """
    digits = re.sub(r"\s+", "", cn_code)
    return f"{digits}-{_slug(description)}"


def parse_annex(xlsx_path: Path) -> tuple[list[dict], list[dict]]:
    """Return (goods dimension rows, default-value rows) from the annex workbook."""
    try:
        import openpyxl
    except ModuleNotFoundError:  # pragma: no cover - dev-only dependency
        sys.exit("openpyxl is required: uv run --with openpyxl python -m scripts.build_cbam_seeds")

    workbook = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    goods: dict[str, dict] = {}
    values: list[dict] = []

    for sheet_name in workbook.sheetnames:
        if sheet_name in SKIP_SHEETS:
            continue
        is_fallback = sheet_name == FALLBACK_SHEET
        if is_fallback:
            country, iso3 = FALLBACK_LABEL, ""
        else:
            country = sheet_name
            iso3 = SHEET_TO_ISO3.get(sheet_name, "")

        product_group = ""
        for row in workbook[sheet_name].iter_rows(min_row=3, values_only=True):
            code, description = _text(row[0]), _text(row[1])
            if not code:
                continue
            if not description:
                # A group banner ("Cement", "Iron and steel"), which also carries
                # the mark-up labels for the columns to its right.
                product_group = code
                continue

            cn_code = _cn_code(row[0])
            key = good_key(cn_code, description)
            goods.setdefault(
                key,
                {
                    "good_key": key,
                    "product_group": product_group,
                    "cn_code": cn_code,
                    "goods_description": description,
                },
            )

            total = _number(row[4])
            # Read the mark-up columns only when there is a total to mark up.
            # Albania's white Portland cement has no total and three numbers in
            # these columns anyway; taking them would publish a euro figure off a
            # row the annex's own fallback rule says to ignore.
            markups = (
                (_number(row[5]), _number(row[6]), _number(row[7]))
                if total is not None
                else (None, None, None)
            )

            values.append(
                {
                    "country_or_territory": country,
                    "country_iso3": iso3,
                    "good_key": key,
                    "default_direct_t_co2e_per_t": _number(row[2]),
                    "default_indirect_t_co2e_per_t": _number(row[3]),
                    "default_total_t_co2e_per_t": total,
                    "default_2026_t_co2e_per_t": markups[0],
                    "default_2027_t_co2e_per_t": markups[1],
                    "default_2028_t_co2e_per_t": markups[2],
                    "production_route_code": _route(row[8]),
                }
            )

    return sorted(
        goods.values(), key=lambda g: (g["product_group"], g["cn_code"], g["good_key"])
    ), values


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row[k] is None else row[k]) for k in fieldnames})
    print(f"wrote {path.relative_to(project_root())}: {len(rows):,} rows")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", type=Path, help="local copy of the annex workbook")
    args = parser.parse_args()

    xlsx_path = args.xlsx
    if xlsx_path is None:
        xlsx_path = project_root() / "data" / "cbam_annex_i.xlsx"
        xlsx_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {ANNEX_XLSX_URL}")
        with urlopen(ANNEX_XLSX_URL) as response:
            xlsx_path.write_bytes(response.read())

    goods, values = parse_annex(xlsx_path)

    unmapped = sorted(
        {v["country_or_territory"] for v in values if not v["country_iso3"]} - {FALLBACK_LABEL}
    )
    if unmapped:
        sys.exit(f"no ISO3 for {len(unmapped)} territories, add them to SHEET_TO_ISO3: {unmapped}")

    _write_csv(
        SEED_DIR / "cbam_goods.csv",
        goods,
        ["good_key", "product_group", "cn_code", "goods_description"],
    )
    _write_csv(
        SEED_DIR / "cbam_default_values.csv",
        values,
        [
            "country_or_territory",
            "country_iso3",
            "good_key",
            "default_direct_t_co2e_per_t",
            "default_indirect_t_co2e_per_t",
            "default_total_t_co2e_per_t",
            "default_2026_t_co2e_per_t",
            "default_2027_t_co2e_per_t",
            "default_2028_t_co2e_per_t",
            "production_route_code",
        ],
    )


if __name__ == "__main__":
    main()
