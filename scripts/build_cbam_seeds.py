"""Transcribe Annex I of Implementing Regulation (EU) 2025/2621 into two dbt seeds.

The CBAM default values are regulatory reference data, versioned by amendment
rather than by scrape, so they are seeds rather than a dlt resource; this script
makes the next amendment a re-run rather than a re-transcription. The
Commission's XLSX is "for information purposes only" — the regulation is binding.

    uv run python -m scripts.build_cbam_seeds            # download, then write
    uv run python -m scripts.build_cbam_seeds --xlsx X   # from a local copy

Two seeds, because the 12,540 value rows share 283 goods whose long
descriptions are 1.6 MB repeated per row and 38 kB as a dimension. The mark-up
schedule is a third, hand-written seed (`cbam_markup_schedule`): the annex no
longer publishes it.

The seeds hold the annex as corrected by Implementing Regulation (EU) 2026/1740
(mark-up columns dropped, 10-digit TARIC codes added, countries renamed); the
`compliance-models` skill records what it changed.

The script transcribes faithfully — the annex is the legal instrument — and
stops on anything it does not recognise: an unmapped country
(`SHEET_TO_ISO3`), a moved column (`_check_layout`), an unparsable cell
(`_number`). `dbt/seeds/_seeds.yml` and
`dbt/models/marts/compliance/_compliance.yml` test the result.
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

# The Commission's informational XLSX; the binding text is the OJ.
ANNEX_XLSX_URL = (
    "https://taxation-customs.ec.europa.eu/document/download/"
    "1c05d211-80cb-4aaa-8ef0-e08005a95d7e_en"
    "?filename=DVs%20as%20adopted_v20260204%20.xlsx"
)

SEED_DIR = project_root() / "dbt" / "seeds"

# The annex's countries, resolved to ISO3 at transcription time so the mapping is
# a reviewable diff rather than a fuzzy join. An unmapped sheet stops the script,
# naming it, instead of writing a blank ISO3 the mart's join would drop. In sheet
# order, to read against the workbook. Some needed a human: Excel truncates sheet
# names at 31 characters ("North Korea (Democratic People’"), and publishers
# differ ("Egypt" here, "Egypt, Arab Rep." at the World Bank).
SHEET_TO_ISO3 = {
    "Albania": "ALB",
    "Algeria": "DZA",
    "Angola": "AGO",
    "Argentina": "ARG",
    "Armenia": "ARM",
    "Australia": "AUS",
    "Azerbaijan": "AZE",
    "Bangladesh": "BGD",
    "Bahrain": "BHR",
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
    "Congo, Democratic Republic of": "COD",
    "Costa Rica": "CRI",
    "Cuba": "CUB",
    "Curaçao": "CUW",
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
    "Iran, Islamic Republic of": "IRN",
    "Iraq": "IRQ",
    "Israel": "ISR",
    "Ivory Coast": "CIV",
    "Jamaica": "JAM",
    "Japan": "JPN",
    "Jordan": "JOR",
    "Kazakhstan": "KAZ",
    "Kenya": "KEN",
    "Korea, Republic of (South Korea": "KOR",
    "Kuwait": "KWT",
    "Kyrgyzstan": "KGZ",
    "Laos": "LAO",
    "Lebanon": "LBN",
    "Liberia": "LBR",
    "Libya": "LBY",
    "Madagascar": "MDG",
    "Malaysia": "MYS",
    "Mali": "MLI",
    "Mauritania": "MRT",
    "Mauritius": "MUS",
    "Mexico": "MEX",
    "Moldova, Republic of": "MDA",
    "Mongolia": "MNG",
    "Montenegro": "MNE",
    "Morocco": "MAR",
    "Mozambique": "MOZ",
    "Myanmar": "MMR",
    "Namibia": "NAM",
    "Nepal": "NPL",
    "New Caledonia and dependencies": "NCL",
    "New Zealand": "NZL",
    "Nicaragua": "NIC",
    "Niger": "NER",
    "Nigeria": "NGA",
    "North Korea (Democratic People’": "PRK",
    "North Macedonia": "MKD",
    "Oman": "OMN",
    "Pakistan": "PAK",
    "Panama": "PAN",
    "Papua New Guinea": "PNG",
    "Paraguay": "PRY",
    "Peru": "PER",
    "Philippines": "PHL",
    "Qatar": "QAT",
    "Russian Federation": "RUS",
    "Rwanda": "RWA",
    "Saudi Arabia": "SAU",
    "Senegal": "SEN",
    "Serbia": "SRB",
    "Sierra Leone": "SLE",
    "Singapore": "SGP",
    "South Africa": "ZAF",
    "Sri Lanka": "LKA",
    "Sudan": "SDN",
    "Suriname": "SUR",
    "Syria": "SYR",
    "Taiwan": "TWN",
    "Tajikistan": "TJK",
    "Tanzania, United Republic of": "TZA",
    "Thailand": "THA",
    "Togo": "TGO",
    "Trinidad and Tobago": "TTO",
    "Tunisia": "TUN",
    "Türkiye": "TUR",
    "Turkmenistan": "TKM",
    "Uganda": "UGA",
    "Ukraine": "UKR",
    "United Arab Emirates": "ARE",
    "United Kingdom": "GBR",
    "United States": "USA",
    "Uruguay": "URY",
    "Uzbekistan": "UZB",
    "Venezuela": "VEN",
    "Viet Nam": "VNM",
    "Yemen": "YEM",
    "Zambia": "ZMB",
    "Zimbabwe": "ZWE",
}

# Column positions on a country sheet. They have moved once (the 2026/1740
# correction dropped three mark-up columns, moving `route` from 8 to 5), so
# `_check_layout` and the row-width check refuse any other layout.
COLUMNS = {"cn_code": 0, "description": 1, "direct": 2, "indirect": 3, "total": 4, "route": 5}

# What each of those columns must say it is, checked once per sheet by
# `_check_layout`. Lowercased substrings of the Commission's own headings, so the
# units and parentheses around them can be reworded without breaking the check,
# but a column that *moves* cannot pass.
HEADER_KEYWORDS = {
    "cn_code": "cn code",
    "description": "description",
    "direct": "direct emissions",
    "indirect": "indirect emissions",
    "total": "total emissions",
    "route": "production route",
}

# The annex's catch-all table. It is not a country and gets no ISO3: it is the
# value an importer uses when the sourcing country is unlisted, or is listed with
# a `–` for that good. `fct_cbam_exposure` resolves both cases against it.
FALLBACK_SHEET = "_Other Countries and Territorie"
FALLBACK_LABEL = "Other countries and territories"

# The annex's ways of writing "no value", which `fct_cbam_exposure` prices from
# the fallback table. `see below` is prose on the 4-digit headings 3102 and 3105,
# whose values sit in the subheading rows; listed explicitly so its null is a
# decision, and matched case-insensitively.
NO_VALUE = {"", "-", "–", "—", "_", "N/A", "n/a", "None"}
NO_VALUE_PHRASES = {"see below"}

# Annex IV (the highest default per good, with no country) is not transcribed:
# when a declarant must use it is set by the regulation's articles, which were
# not confirmed from a primary source. (Annexes II and III are excluded on
# licence — see `publish/export_warehouse.ATTRIBUTION`.)
SKIP_SHEETS = {"Overview", "Version History", "Annex IV"}


def _text(cell: object) -> str:
    return "" if cell is None else str(cell).strip()


def _number(cell: object) -> float | None:
    """Parse an annex cell, which may be a float, an int, or a comma decimal.

    Rounded to the three decimals the OJ prints, half away from zero: formula
    cells arrive as binary floats (0.35200000000000004 for 0,352), and `round()`
    rounds halves to even, which differs from the OJ in the third decimal.

    **An unrecognised token raises rather than returning None.** `None` is the
    annex's "no value", which `fct_cbam_exposure` prices from the fallback row —
    so a misread cell would become a plausible euro figure attributed to the
    wrong source. `NO_VALUE` lists the accepted blanks; anything else is a change
    in the source for a person to look at.
    """
    raw = _text(cell)
    if raw in NO_VALUE or raw.casefold() in NO_VALUE_PHRASES:
        return None
    # The comma is the OJ's decimal separator. A thousands-separated figure
    # would leave two separators and raise below — the annex prints none.
    try:
        value = Decimal(repr(float(raw.replace(",", "."))))
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(
            f"unparsable annex cell {raw!r} — if this is a new way of writing "
            f"'no value', add it to NO_VALUE; otherwise the source has changed shape"
        ) from exc
    return float(value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def _cn_code(cell: object) -> str:
    """Normalise a CN or TARIC code to the spaced form the annex prints.

    Excel types many cells as integers (28142000 for "2814 20 00"), so the same
    good would read two ways across sheets. 10 digits is a TARIC code (new with
    2026/1740), 8 a CN code, 4 and 6 headings.
    """
    raw = _text(cell)
    if raw.isdigit():
        digits = raw
    else:
        digits = re.sub(r"\s+", "", raw)
        if not digits.isdigit():
            return raw
    # The annex prints 4- and 6-digit headings for whole subheadings alongside
    # the 8-digit CN codes and the 10-digit TARIC ones; all four stay as they are.
    if len(digits) == 10:
        return f"{digits[:4]} {digits[4:6]} {digits[6:8]} {digits[8:]}"
    if len(digits) == 8:
        return f"{digits[:4]} {digits[4:6]} {digits[6:]}"
    if len(digits) == 6:
        return f"{digits[:4]} {digits[4:]}"
    return digits


def _route(cell: object) -> str:
    """The production route indicator, e.g. "(C)" -> "C" and "(C)/(F)" -> "C/F".

    Annex I's footnote maps these to CBAM benchmarks — (C) carbon steel via
    BF/BOF, (E) via scrap/EAF, (K) primary aluminium, (L) secondary — and the
    route, not the country, is what separates a 0,13 tCO2e/t steel from an 8,21.
    Stripping only the outer parentheses would leave "C)/(F".
    """
    return "/".join(re.findall(r"\(([A-Z])\)", _text(cell)))


def _slug(text: str, limit: int = 40) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:limit].rstrip("-")


def good_key(cn_code: str, description: str) -> str:
    """A stable, readable key for a (CN code, description) pair.

    A code alone need not identify a good — headings are coarse, and before
    TARIC codes white and grey clinker shared `2523 10 00` at values more than
    2x apart — so the key pairs it with the description. A slug rather than a
    surrogate integer, so regenerating the seed does not renumber every row.
    """
    digits = re.sub(r"\s+", "", cn_code)
    return f"{digits}-{_slug(description)}"


def _check_layout(sheet, sheet_name: str) -> None:
    """Fail unless the sheet's header row says what `COLUMNS` assumes it says.

    The row-width check alone would pass a layout whose columns moved at the
    same width, with every field parsing from the wrong cell. So each position is
    checked against the headings in row 2, by substring: units and parentheses
    have been reworded before, the identifying words have not.
    """
    header = next(sheet.iter_rows(min_row=2, max_row=2, values_only=True), ())
    if len(header) != len(COLUMNS):
        raise ValueError(
            f"{sheet_name!r} header has {len(header)} columns, expected "
            f"{len(COLUMNS)} — the annex layout has changed; see COLUMNS"
        )
    for name, index in COLUMNS.items():
        expected = HEADER_KEYWORDS[name]
        found = _text(header[index]).casefold()
        if expected not in found:
            raise ValueError(
                f"{sheet_name!r} column {index} reads {_text(header[index])!r}, "
                f"expected it to mention {expected!r} — the annex columns have "
                f"moved and COLUMNS is now pointing at the wrong cell"
            )


def parse_annex(xlsx_path: Path) -> tuple[list[dict], list[dict]]:
    """Return (goods dimension rows, default-value rows) from the annex workbook."""
    try:
        import openpyxl  # ty: ignore[unresolved-import]  # optional; see below
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

        _check_layout(workbook[sheet_name], sheet_name)

        product_group = ""
        for row in workbook[sheet_name].iter_rows(min_row=3, values_only=True):
            code = _text(row[0]) if row else ""
            if not code:
                continue
            # Exactly: a wider row would still parse, one position out.
            if len(row) != len(COLUMNS):
                raise ValueError(
                    f"{sheet_name!r} row {code!r} has {len(row)} columns, expected "
                    f"{len(COLUMNS)} — the annex layout has changed"
                )
            description = _text(row[1])
            if not description:
                # A group banner ("Cement", "Iron and steel").
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

            values.append(
                {
                    "country_or_territory": country,
                    "country_iso3": iso3,
                    "good_key": key,
                    "default_direct_t_co2e_per_t": _number(row[COLUMNS["direct"]]),
                    "default_indirect_t_co2e_per_t": _number(row[COLUMNS["indirect"]]),
                    "default_total_t_co2e_per_t": _number(row[COLUMNS["total"]]),
                    "production_route_code": _route(row[COLUMNS["route"]]),
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
            "production_route_code",
        ],
    )


if __name__ == "__main__":
    main()
