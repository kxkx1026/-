"""Flask application providing a natural-language interface for DART exports."""
from __future__ import annotations

import io
import os
import re
import uuid
from dataclasses import dataclass
from difflib import get_close_matches
from functools import lru_cache
from typing import Dict, Mapping, Optional, Sequence

import pandas as pd
from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from plotly import graph_objs as go
from plotly.offline import plot

from scripts.dart_financials_to_excel import (
    DEFAULT_REPRT_CODES,
    build_corp_code_index,
    collect_financial_data,
    dataframe_to_excel_bytes,
    download_corp_code_xml,
)


@dataclass
class QueryParameters:
    """Interpretation of a natural language request."""

    corp_name: str
    display_name: str
    years: Sequence[int]
    fs_div: Optional[str]


class QueryError(ValueError):
    """Raised when the request cannot be interpreted."""


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dart-secret-key")

RESULT_CACHE: Dict[str, Dict[str, object]] = {}


@lru_cache(maxsize=4)
def get_corp_index(api_key: str) -> Mapping[str, str]:
    xml_text = download_corp_code_xml(api_key)
    return build_corp_code_index(xml_text)


def _normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _guess_fs_div(query: str) -> Optional[str]:
    if re.search("별도|개별", query):
        return "OFS"
    if re.search("연결|consolidated", query, flags=re.IGNORECASE):
        return "CFS"
    return None


def _interpret_years(query: str, default_latest: int = 5) -> Sequence[int]:
    recent_match = re.search(r"최근\s*(\d+)\s*년", query)
    if recent_match:
        latest = int(recent_match.group(1))
        if latest > 0:
            end_year = pd.Timestamp.today().year
            start_year = end_year - (latest - 1)
            return list(range(start_year, end_year + 1))

    years = [int(match) for match in re.findall(r"(20\d{2})", query)]
    if len(years) >= 2:
        start_year = min(years)
        end_year = max(years)
        if end_year >= start_year:
            return list(range(start_year, end_year + 1))
    if len(years) == 1:
        end_year = years[0]
        start_year = end_year - (default_latest - 1)
        return list(range(start_year, end_year + 1))

    end_year = pd.Timestamp.today().year
    start_year = end_year - (default_latest - 1)
    return list(range(start_year, end_year + 1))


def _select_company_name(query: str, index: Mapping[str, str]) -> Optional[str]:
    lowered = query.lower()
    direct_matches = [name for name in index.keys() if name in lowered]
    if direct_matches:
        # Prefer the longest match to avoid partial collisions (e.g. 삼성 vs 삼성전자)
        return max(direct_matches, key=len)

    words = re.findall(r"[\w가-힣]+", lowered)
    candidates = get_close_matches(" ".join(words), index.keys(), n=1, cutoff=0.7)
    if candidates:
        return candidates[0]
    return None


def interpret_query(query: str, index: Mapping[str, str]) -> QueryParameters:
    cleaned = _normalize_query(query)
    if not cleaned:
        raise QueryError("검색어를 입력해주세요.")

    corp_name = _select_company_name(cleaned, index)
    if not corp_name:
        raise QueryError("요청에서 회사를 찾을 수 없습니다. 정확한 회사명을 포함해주세요.")

    years = sorted(set(_interpret_years(cleaned)))
    fs_div = _guess_fs_div(cleaned)

    match = re.search(re.escape(corp_name), query, flags=re.IGNORECASE)
    display_name = match.group(0) if match else corp_name

    return QueryParameters(
        corp_name=corp_name,
        display_name=display_name,
        years=years,
        fs_div=fs_div,
    )


def _format_amount(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return f"{value}"


def _build_visualization(df: pd.DataFrame, period_columns: Sequence[str]) -> Optional[str]:
    if not period_columns or df.empty:
        return None

    numeric_df = df.loc[:, period_columns].apply(pd.to_numeric, errors="coerce")
    if numeric_df.isna().all().all():
        return None

    abs_sums = numeric_df.abs().sum(axis=1)
    top_indices = abs_sums.nlargest(5).index
    data = []
    for idx in top_indices:
        row = numeric_df.loc[idx]
        if row.isna().all():
            continue
        account_name = df.loc[idx, "Account Name"]
        data.append(
            go.Scatter(
                x=list(period_columns),
                y=row.tolist(),
                mode="lines+markers",
                name=account_name,
            )
        )

    if not data:
        return None

    layout = go.Layout(
        title="주요 계정 추이",
        xaxis={"title": "기간"},
        yaxis={"title": "금액", "tickformat": ",.0f"},
        hovermode="x unified",
    )
    return plot({"data": data, "layout": layout}, include_plotlyjs=False, output_type="div")


def _prepare_table(df: pd.DataFrame, period_columns: Sequence[str]) -> pd.DataFrame:
    preview_rows = min(len(df), 50)
    preview_df = df.head(preview_rows).copy()
    for column in period_columns:
        preview_df[column] = preview_df[column].map(_format_amount)
    return preview_df


@app.route("/", methods=["GET", "POST"])
def index() -> str:
    default_api_key = os.environ.get("DART_API_KEY", "")
    if request.method == "POST":
        query_text = request.form.get("query", "")
        api_key = request.form.get("api_key") or default_api_key
        if not api_key:
            flash("DART API 키를 입력해주세요.")
            return redirect(url_for("index"))

        try:
            index_map = get_corp_index(api_key)
            params = interpret_query(query_text, index_map)
            corp_code = index_map[params.corp_name]

            df, period_columns = collect_financial_data(
                api_key=api_key,
                corp_code=corp_code,
                years=params.years,
                reprt_codes=DEFAULT_REPRT_CODES,
                fs_div=params.fs_div,
            )
            if df.empty:
                raise QueryError("DART API에서 재무 데이터를 찾을 수 없습니다.")

            result_id = str(uuid.uuid4())
            excel_bytes = dataframe_to_excel_bytes(df)
            RESULT_CACHE[result_id] = {
                "excel_bytes": excel_bytes,
                "filename": f"{params.display_name}_financials.xlsx",
            }
            if len(RESULT_CACHE) > 20:
                # Drop the oldest cached result to bound memory usage.
                first_key = next(iter(RESULT_CACHE.keys()))
                if first_key != result_id:
                    RESULT_CACHE.pop(first_key, None)

            table_df = _prepare_table(df, period_columns)
            plot_div = _build_visualization(df, period_columns)

            return render_template(
                "results.html",
                corp_name=params.display_name,
                fs_div=params.fs_div or "CFS",
                years=params.years,
                period_columns=period_columns,
                table_html=table_df.to_html(classes="table table-striped table-hover", index=False, border=0),
                plot_div=plot_div,
                download_url=url_for("download", result_id=result_id),
            )
        except QueryError as exc:
            flash(str(exc))
            return redirect(url_for("index"))
        except Exception as exc:  # pragma: no cover - defensive
            flash(f"요청 처리 중 오류가 발생했습니다: {exc}")
            return redirect(url_for("index"))

    return render_template("index.html", default_api_key=default_api_key)


@app.route("/download/<result_id>")
def download(result_id: str) -> Response:
    result = RESULT_CACHE.get(result_id)
    if not result:
        flash("요청하신 파일을 찾을 수 없습니다. 다시 검색해주세요.")
        return redirect(url_for("index"))

    excel_bytes = result["excel_bytes"]
    filename = result["filename"]
    return send_file(
        io.BytesIO(excel_bytes),
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    app.run(debug=True)
