"""Utilities for exporting comprehensive DART financial statements to Excel.

This script downloads every available account in the Korean FSS DART API for a
single company over a five year quarterly window and pivots the data into an
Excel workbook.  The resulting file places accounts on the rows and
periods (year-quarter) on the columns.

The implementation intentionally avoids any external project specific
dependencies so it can be reused as a standalone utility.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import sys
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Tuple

import xml.etree.ElementTree as ET

import pandas as pd
import requests


DART_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_SINGLE_ACCOUNT_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"

# Mapping between quarter labels and DART report codes. Q4 uses the annual report.
DEFAULT_REPRT_CODES = OrderedDict([
    ("Q1", "11013"),
    ("Q2", "11012"),
    ("Q3", "11014"),
    ("Q4", "11011"),
])


@dataclass(frozen=True)
class AccountKey:
    """Identifies a single account record across statements."""

    account_id: str
    account_name: str
    statement_division: str

    def as_metadata(self) -> Dict[str, str]:
        return {
            "Account ID": self.account_id,
            "Account Name": self.account_name,
            "Statement": self.statement_division,
        }


def parse_amount(raw: Optional[str]) -> Optional[Decimal]:
    """Convert a numeric string from the API into a Decimal.

    Empty strings or placeholder characters are converted to ``None`` to make it
    easier to leave a cell blank in the spreadsheet when data is missing.
    """

    if raw is None:
        return None
    raw = raw.strip()
    if not raw or raw in {"-", "--"}:
        return None
    # Remove thousands separators, then parse using Decimal for precision.
    normalized = raw.replace(",", "")
    if normalized.startswith("(") and normalized.endswith(")"):
        # Convert values like "(1,234)" into "-1234".
        normalized = f"-{normalized[1:-1]}"
    try:
        return Decimal(normalized)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unable to parse amount: {raw!r}") from exc


def download_corp_code_xml(api_key: str) -> str:
    """Download and return the raw corp code XML as a string."""

    response = requests.get(DART_CORP_CODE_URL, params={"crtfc_key": api_key}, timeout=30)
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        # The archive typically contains a single file named CORPCODE.xml, but we
        # read the first entry defensively.
        name = zf.namelist()[0]
        with zf.open(name) as xml_file:
            return xml_file.read().decode("utf-8")


def build_corp_code_index(xml_text: str) -> Mapping[str, str]:
    """Parse the corp code xml into a mapping of lower-cased names to codes."""

    root = ET.fromstring(xml_text)
    index: Dict[str, str] = {}
    for entry in root.findall("list"):
        name = entry.findtext("corp_name", default="").strip()
        code = entry.findtext("corp_code", default="").strip()
        if not name or not code:
            continue
        index[name.lower()] = code
    if not index:
        raise RuntimeError("Parsed corp code index is empty; the XML may be malformed.")
    return index


def resolve_corp_code(api_key: str, corp_name: str) -> str:
    """Resolve the DART corp code for a given company name."""

    xml_text = download_corp_code_xml(api_key)
    index = build_corp_code_index(xml_text)

    code = index.get(corp_name.lower())
    if code:
        return code

    # Attempt fuzzy lookup using startswith to provide a helpful error message.
    suggestions = [name for name in index.keys() if name.startswith(corp_name.lower())]
    if suggestions:
        suggestion_text = ", ".join(sorted(suggestions)[:5])
        raise KeyError(
            f"Company name '{corp_name}' was not found. Did you mean one of: {suggestion_text}?"
        )
    raise KeyError(f"Company name '{corp_name}' was not found in the DART corp code list.")


def fetch_financial_statement(
    api_key: str,
    corp_code: str,
    year: int,
    reprt_code: str,
    fs_div: Optional[str],
) -> List[Mapping[str, str]]:
    """Fetch the financial statement records for a given period."""

    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
    }
    if fs_div:
        params["fs_div"] = fs_div

    response = requests.get(DART_SINGLE_ACCOUNT_URL, params=params, timeout=30)
    response.raise_for_status()

    payload = response.json()
    status = payload.get("status")
    if status != "000":
        message = payload.get("message", "Unknown error")
        raise RuntimeError(
            f"DART API error for {year} report code {reprt_code}: {status} - {message}"
        )

    records = payload.get("list") or []
    if not isinstance(records, list):
        raise TypeError("Unexpected payload structure: 'list' field is not an array")
    return records


def collect_financial_data(
    api_key: str,
    corp_code: str,
    years: Iterable[int],
    reprt_codes: Mapping[str, str],
    fs_div: Optional[str],
) -> Tuple[pd.DataFrame, List[str]]:
    """Collect every available account into a tidy pandas DataFrame."""

    data: MutableMapping[AccountKey, Dict[str, Optional[Decimal]]] = OrderedDict()
    period_columns: List[str] = []

    seen_columns = set()

    for year in years:
        for quarter_label, reprt_code in reprt_codes.items():
            column = f"{year}_{quarter_label}"
            if column not in seen_columns:
                period_columns.append(column)
                seen_columns.add(column)
            records = fetch_financial_statement(api_key, corp_code, year, reprt_code, fs_div)

            for record in records:
                key = AccountKey(
                    account_id=(record.get("account_id") or "").strip(),
                    account_name=(record.get("account_nm") or "").strip(),
                    statement_division=(record.get("sj_nm") or record.get("sj_div") or "").strip(),
                )
                if not key.account_name:
                    # Skip records without a name; they are unusable in the final table.
                    continue

                amount = parse_amount(record.get("thstrm_amount"))
                data.setdefault(key, {})[column] = amount

    # Compose DataFrame with metadata columns first.
    rows = []
    for key, values in data.items():
        row = key.as_metadata()
        row.update(values)
        rows.append(row)

    df = pd.DataFrame(rows)
    metadata_cols = ["Account ID", "Account Name", "Statement"]
    columns = metadata_cols + period_columns
    df = df.reindex(columns=columns)

    # Sort by statement then account name for readability.
    if not df.empty:
        df.sort_values(by=["Statement", "Account Name"], inplace=True)
        df.reset_index(drop=True, inplace=True)

    return df, period_columns


def _write_dataframe_to_excel(df: pd.DataFrame, writer: pd.ExcelWriter) -> None:
    """Write the financial DataFrame into an Excel worksheet."""

    df.to_excel(writer, index=False, sheet_name="Financials")


def write_excel(df: pd.DataFrame, output_path: str) -> None:
    """Persist the DataFrame into an Excel workbook."""

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        _write_dataframe_to_excel(df, writer)


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Serialize the DataFrame to an Excel workbook in-memory."""

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        _write_dataframe_to_excel(df, writer)
    buffer.seek(0)
    return buffer.read()


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("api_key", help="Registered DART API certification key")
    parser.add_argument("corp_name", help="Exact company name as registered in DART")
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        help=(
            "Explicit list of fiscal years to download. "
            "Defaults to the latest five fiscal years based on the current year."
        ),
    )
    parser.add_argument(
        "--latest",
        type=int,
        default=5,
        help=(
            "Number of most recent fiscal years to include when --years is not provided. "
            "Defaults to 5."
        ),
    )
    parser.add_argument(
        "--fs-div",
        dest="fs_div",
        choices=["CFS", "OFS"],
        default="CFS",
        help=(
            "Financial statement division. Use CFS for consolidated statements and OFS "
            "for separate statements. Default is CFS."
        ),
    )
    parser.add_argument(
        "--output",
        default="dart_financials.xlsx",
        help="Destination Excel file path. Defaults to dart_financials.xlsx",
    )
    return parser.parse_args(argv)


def determine_years(args: argparse.Namespace) -> List[int]:
    if args.years:
        return sorted(set(args.years))

    current_year = _dt.date.today().year
    years = {current_year - offset for offset in range(args.latest)}
    return sorted(years)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)

    years = determine_years(args)
    corp_code = resolve_corp_code(args.api_key, args.corp_name)
    df, period_columns = collect_financial_data(
        api_key=args.api_key,
        corp_code=corp_code,
        years=years,
        reprt_codes=DEFAULT_REPRT_CODES,
        fs_div=args.fs_div,
    )

    if df.empty:
        raise RuntimeError("No financial data was returned by the DART API.")

    write_excel(df, args.output)

    print(
        "Export completed successfully. Rows: {rows}, columns: {cols}. Saved to: {path}".format(
            rows=len(df), cols=len(period_columns), path=args.output
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
