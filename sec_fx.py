from __future__ import annotations

import csv
from datetime import date, datetime
from functools import lru_cache
from io import StringIO
import urllib.request


FRED_DEXKOUS_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXKOUS"
FX_SOURCE_LABEL = "FRED DEXKOUS, KRW per 1 USD"


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


@lru_cache(maxsize=1)
def load_usd_krw_rates() -> tuple[tuple[date, float], ...]:
    request = urllib.request.Request(
        FRED_DEXKOUS_CSV_URL,
        headers={"User-Agent": "SEC Financial Screening FX loader"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8")

    rows: list[tuple[date, float]] = []
    for row in csv.DictReader(StringIO(text)):
        raw_date = row.get("observation_date")
        raw_value = row.get("DEXKOUS")
        if not raw_date or not raw_value or raw_value == ".":
            continue
        rows.append((parse_date(raw_date), float(raw_value)))
    return tuple(item for item in rows if item[0] is not None)


def rate_on_or_before(target_date: date, rates: tuple[tuple[date, float], ...]) -> tuple[date, float]:
    candidates = [item for item in rates if item[0] <= target_date]
    if not candidates:
        raise ValueError(f"No USD/KRW rate available on or before {target_date}.")
    return candidates[-1]


def average_rate(start_date: date, end_date: date, rates: tuple[tuple[date, float], ...]) -> float:
    values = [value for rate_date, value in rates if start_date <= rate_date <= end_date]
    if not values:
        _closest_date, closest_rate = rate_on_or_before(end_date, rates)
        return closest_rate
    return sum(values) / len(values)


def fiscal_period_from_result(result) -> tuple[date, date]:
    end_date = parse_date(getattr(result, "period_end", None))
    start_date = parse_date(getattr(result, "period_start", None))
    if end_date is None:
        end_date = date(int(result.fiscal_year), 12, 31)
    if start_date is None:
        start_date = date(int(result.fiscal_year), 1, 1)
    return start_date, end_date


def build_exchange_rates_for_results(results) -> dict[tuple[str, int], dict[str, float | str]]:
    rates = load_usd_krw_rates()
    exchange_rates = {}
    for result in results:
        start_date, end_date = fiscal_period_from_result(result)
        closing_date, closing = rate_on_or_before(end_date, rates)
        average = average_rate(start_date, end_date, rates)
        exchange_rates[(result.cik, int(result.fiscal_year))] = {
            "closing": closing,
            "average": average,
            "closing_date": closing_date.isoformat(),
            "average_start": start_date.isoformat(),
            "average_end": end_date.isoformat(),
            "source": FX_SOURCE_LABEL,
        }
    return exchange_rates


def exchange_rate_rows(exchange_rates: dict[tuple[str, int], dict[str, float | str]]) -> list[dict]:
    rows = []
    for (cik, fiscal_year), rate in sorted(exchange_rates.items(), key=lambda item: (item[0][0], item[0][1])):
        rows.append(
            {
                "CIK": cik,
                "Fiscal Year": fiscal_year,
                "Closing USD/KRW": rate.get("closing"),
                "Closing Rate Date": rate.get("closing_date"),
                "Average USD/KRW": rate.get("average"),
                "Average Period": f"{rate.get('average_start')} ~ {rate.get('average_end')}",
                "Source": rate.get("source"),
            }
        )
    return rows
