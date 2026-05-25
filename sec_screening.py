from __future__ import annotations

from dataclasses import dataclass

from sec_edgar_client import CompanyMatch, fetch_company_facts


CONCEPTS = {
    "revenue": [
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ],
    "net_income": [
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
    ],
    "operating_income": [
        ("us-gaap", "OperatingIncomeLoss"),
    ],
    "total_assets": [
        ("us-gaap", "Assets"),
    ],
    "total_liabilities": [
        ("us-gaap", "Liabilities"),
    ],
    "total_equity": [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    ],
    "current_assets": [
        ("us-gaap", "AssetsCurrent"),
    ],
    "current_liabilities": [
        ("us-gaap", "LiabilitiesCurrent"),
    ],
    "cash": [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    ],
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
    ],
}


@dataclass
class ScreeningResult:
    company_name: str
    ticker: str
    cik: str
    fiscal_year: int
    fiscal_period: str
    form: str
    filed: str
    metrics: dict
    notes: dict
    red_flags: list[str]


@dataclass
class ScreeningError:
    input_query: str
    company_name: str
    ticker: str
    cik: str
    error_message: str


def safe_div(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _unit_priority(unit_name: str) -> int:
    if unit_name == "USD":
        return 0
    if unit_name == "pure":
        return 1
    return 9


def pick_latest_annual_fact(company_facts: dict, taxonomy: str, concept: str) -> dict | None:
    facts = ((company_facts.get("facts") or {}).get(taxonomy) or {}).get(concept) or {}
    units = facts.get("units") or {}
    candidates: list[tuple[tuple, dict]] = []

    for unit_name, rows in units.items():
        for row in rows:
            form = str(row.get("form") or "")
            fy = row.get("fy")
            fp = str(row.get("fp") or "")
            frame = str(row.get("frame") or "")
            if form not in {"10-K", "20-F", "40-F"}:
                continue
            if fp and fp != "FY":
                continue
            if not fy:
                continue
            end = str(row.get("end") or "")
            filed = str(row.get("filed") or "")
            score = (int(fy), end, filed, -_unit_priority(unit_name), frame)
            enriched = dict(row)
            enriched["unit"] = unit_name
            candidates.append((score, enriched))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def extract_latest_metrics(company_facts: dict) -> tuple[dict, dict]:
    metrics = {}
    notes = {}
    for metric_key, concept_options in CONCEPTS.items():
        matched_fact = None
        matched_concept = None
        for taxonomy, concept in concept_options:
            matched_fact = pick_latest_annual_fact(company_facts, taxonomy, concept)
            if matched_fact:
                matched_concept = f"{taxonomy}:{concept}"
                break
        if matched_fact is None:
            metrics[metric_key] = None
            notes[metric_key] = "No standard annual SEC fact matched."
        else:
            metrics[metric_key] = matched_fact.get("val")
            notes[metric_key] = f"Matched {matched_concept} ({matched_fact.get('form')}, FY{matched_fact.get('fy')})."

    metrics["debt_ratio"] = safe_div(metrics.get("total_liabilities"), metrics.get("total_equity"))
    metrics["current_ratio"] = safe_div(metrics.get("current_assets"), metrics.get("current_liabilities"))
    metrics["operating_margin"] = safe_div(metrics.get("operating_income"), metrics.get("revenue"))
    metrics["net_margin"] = safe_div(metrics.get("net_income"), metrics.get("revenue"))
    metrics["roa"] = safe_div(metrics.get("net_income"), metrics.get("total_assets"))
    metrics["working_capital"] = (
        None
        if metrics.get("current_assets") is None or metrics.get("current_liabilities") is None
        else metrics["current_assets"] - metrics["current_liabilities"]
    )

    return metrics, notes


def infer_latest_filing_meta(company_facts: dict) -> dict:
    latest = None
    for taxonomy_map in (company_facts.get("facts") or {}).values():
        for concept_map in taxonomy_map.values():
            for unit_rows in (concept_map.get("units") or {}).values():
                for row in unit_rows:
                    form = str(row.get("form") or "")
                    fy = row.get("fy")
                    fp = str(row.get("fp") or "")
                    if form not in {"10-K", "20-F", "40-F"} or not fy:
                        continue
                    candidate = {
                        "fiscal_year": int(fy),
                        "fiscal_period": fp or "FY",
                        "form": form,
                        "filed": str(row.get("filed") or ""),
                    }
                    if latest is None or (
                        candidate["fiscal_year"],
                        candidate["filed"],
                    ) > (
                        latest["fiscal_year"],
                        latest["filed"],
                    ):
                        latest = candidate
    return latest or {"fiscal_year": 0, "fiscal_period": "FY", "form": "-", "filed": "-"}


def build_red_flags(metrics: dict) -> list[str]:
    flags = []
    if metrics.get("debt_ratio") is not None and metrics["debt_ratio"] >= 2:
        flags.append("Preliminary red flag: debt ratio is 200% or higher.")
    if metrics.get("current_ratio") is not None and metrics["current_ratio"] < 1:
        flags.append("Preliminary red flag: current ratio is below 1.0x.")
    if metrics.get("operating_cash_flow") is not None and metrics["operating_cash_flow"] < 0:
        flags.append("Preliminary red flag: operating cash flow is negative.")
    if metrics.get("net_margin") is not None and metrics["net_margin"] < 0:
        flags.append("Preliminary red flag: net margin is negative.")
    if metrics.get("working_capital") is not None and metrics["working_capital"] < 0:
        flags.append("Preliminary red flag: working capital is negative.")
    if not flags:
        flags.append("No automatic red flag triggered. Human review is still required.")
    return flags


def screen_company(match: CompanyMatch) -> ScreeningResult:
    company_facts = fetch_company_facts(match.cik)
    metrics, notes = extract_latest_metrics(company_facts)
    filing_meta = infer_latest_filing_meta(company_facts)
    return ScreeningResult(
        company_name=match.company_name,
        ticker=match.ticker,
        cik=match.cik,
        fiscal_year=filing_meta["fiscal_year"],
        fiscal_period=filing_meta["fiscal_period"],
        form=filing_meta["form"],
        filed=filing_meta["filed"],
        metrics=metrics,
        notes=notes,
        red_flags=build_red_flags(metrics),
    )


def screen_companies(matches: list[CompanyMatch]) -> tuple[list[ScreeningResult], list[ScreeningError]]:
    results: list[ScreeningResult] = []
    errors: list[ScreeningError] = []
    for match in matches:
        try:
            results.append(screen_company(match))
        except Exception as exc:
            errors.append(
                ScreeningError(
                    input_query=match.ticker or match.company_name,
                    company_name=match.company_name,
                    ticker=match.ticker,
                    cik=match.cik,
                    error_message=str(exc),
                )
            )
    return results, errors


def result_to_summary_row(result: ScreeningResult) -> dict:
    return {
        "Company": result.company_name,
        "Ticker": result.ticker,
        "CIK": result.cik,
        "Latest FY": result.fiscal_year,
        "Form": result.form,
        "Filed": result.filed,
        "Revenue": result.metrics.get("revenue"),
        "Operating Income": result.metrics.get("operating_income"),
        "Net Income": result.metrics.get("net_income"),
        "Total Assets": result.metrics.get("total_assets"),
        "Total Liabilities": result.metrics.get("total_liabilities"),
        "Total Equity": result.metrics.get("total_equity"),
        "Debt Ratio": result.metrics.get("debt_ratio"),
        "Current Ratio": result.metrics.get("current_ratio"),
        "Operating Margin": result.metrics.get("operating_margin"),
        "Net Margin": result.metrics.get("net_margin"),
        "ROA": result.metrics.get("roa"),
        "Operating Cash Flow": result.metrics.get("operating_cash_flow"),
        "Working Capital": result.metrics.get("working_capital"),
        "Red Flag Count": len(result.red_flags),
    }


def result_to_note_rows(result: ScreeningResult) -> list[dict]:
    rows = []
    for key, note in result.notes.items():
        rows.append(
            {
                "Company": result.company_name,
                "Ticker": result.ticker,
                "Metric": key,
                "Note": note,
            }
        )
    return rows


def result_to_flag_rows(result: ScreeningResult) -> list[dict]:
    rows = []
    for index, flag in enumerate(result.red_flags, start=1):
        rows.append(
            {
                "Company": result.company_name,
                "Ticker": result.ticker,
                "Seq": index,
                "Flag": flag,
            }
        )
    return rows


def error_to_row(error: ScreeningError) -> dict:
    return {
        "Input": error.input_query,
        "Company": error.company_name,
        "Ticker": error.ticker,
        "CIK": error.cik,
        "Error": error.error_message,
    }
