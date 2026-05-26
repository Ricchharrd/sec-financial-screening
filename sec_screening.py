from __future__ import annotations

from dataclasses import dataclass, field

from sec_edgar_client import CompanyMatch, fetch_company_facts


ANNUAL_FORMS = {"10-K", "20-F", "40-F"}
GRADES = ["AAA", "AA", "A", "BB", "B", "CC", "C", "D"]
GRADE_POINTS = {
    "AAA": 100,
    "AA": 95,
    "A": 90,
    "BB": 80,
    "B": 70,
    "CC": 60,
    "C": 50,
    "D": 40,
}
RATING_WEIGHTS = {
    "revenue": 15.0,
    "operating_income": 10.0,
    "interest_coverage": 7.5,
    "financial_debt_to_operating_income": 7.5,
    "operating_cf_to_financial_debt": 15.0,
    "debt_ratio": 15.0,
    "receivable_turnover_days": 15.0,
    "current_ratio": 15.0,
}

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
    "interest_expense": [
        ("us-gaap", "InterestExpenseNonOperating"),
        ("us-gaap", "InterestExpense"),
        ("us-gaap", "InterestExpenseDebt"),
        ("us-gaap", "InterestAndDebtExpense"),
        ("us-gaap", "FinanceLeaseInterestExpense"),
        ("us-gaap", "InterestExpenseBorrowings"),
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
    "accounts_receivable": [
        ("us-gaap", "AccountsReceivableNetCurrent"),
        ("us-gaap", "AccountsReceivableNet"),
        ("us-gaap", "ReceivablesNetCurrent"),
    ],
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
    ],
}

FINANCIAL_DEBT_COMPONENTS = {
    "short_term_borrowings": [
        ("us-gaap", "ShortTermBorrowings"),
        ("us-gaap", "ShortTermDebt"),
        ("us-gaap", "CommercialPaper"),
    ],
    "current_debt": [
        ("us-gaap", "LongTermDebtCurrent"),
        ("us-gaap", "CurrentPortionOfLongTermDebt"),
        ("us-gaap", "LongTermDebtAndFinanceLeaseObligationsCurrent"),
    ],
    "noncurrent_debt": [
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"),
    ],
}
FINANCIAL_DEBT_FALLBACKS = [
    ("us-gaap", "LongTermDebtAndFinanceLeaseObligations"),
    ("us-gaap", "LongTermDebt"),
]


@dataclass
class ScreeningResult:
    company_name: str
    ticker: str
    cik: str
    fiscal_year: int
    fiscal_period: str
    form: str
    filed: str
    period_start: str | None
    period_end: str | None
    metrics: dict
    notes: dict
    red_flags: list[str]
    rating: dict = field(default_factory=dict)


@dataclass
class ScreeningError:
    input_query: str
    company_name: str
    ticker: str
    cik: str
    fiscal_year: int | None
    error_message: str


def clean_abs(value):
    if value is None:
        return None
    return abs(value)


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


def annual_years_available(company_facts: dict) -> list[int]:
    years = set()
    for taxonomy_map in (company_facts.get("facts") or {}).values():
        for concept_map in taxonomy_map.values():
            for unit_rows in (concept_map.get("units") or {}).values():
                for row in unit_rows:
                    form = str(row.get("form") or "")
                    fy = row.get("fy")
                    fp = str(row.get("fp") or "")
                    if form in ANNUAL_FORMS and fy and (not fp or fp == "FY"):
                        years.add(int(fy))
    return sorted(years)


def pick_annual_fact(company_facts: dict, taxonomy: str, concept: str, fiscal_year: int) -> dict | None:
    facts = ((company_facts.get("facts") or {}).get(taxonomy) or {}).get(concept) or {}
    units = facts.get("units") or {}
    candidates: list[tuple[tuple, dict]] = []

    for unit_name, rows in units.items():
        for row in rows:
            form = str(row.get("form") or "")
            fy = row.get("fy")
            fp = str(row.get("fp") or "")
            frame = str(row.get("frame") or "")
            if form not in ANNUAL_FORMS:
                continue
            if not fy or int(fy) != int(fiscal_year):
                continue
            if fp and fp != "FY":
                continue
            end = str(row.get("end") or "")
            filed = str(row.get("filed") or "")
            score = (end, filed, -_unit_priority(unit_name), frame)
            enriched = dict(row)
            enriched["unit"] = unit_name
            enriched["concept"] = f"{taxonomy}:{concept}"
            candidates.append((score, enriched))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def pick_first_annual_fact(company_facts: dict, concept_options: list[tuple[str, str]], fiscal_year: int) -> dict | None:
    for taxonomy, concept in concept_options:
        matched_fact = pick_annual_fact(company_facts, taxonomy, concept, fiscal_year)
        if matched_fact:
            return matched_fact
    return None


def extract_financial_debt(company_facts: dict, fiscal_year: int) -> tuple[float | None, str]:
    total = 0.0
    matched = []
    for component_name, concept_options in FINANCIAL_DEBT_COMPONENTS.items():
        matched_fact = pick_first_annual_fact(company_facts, concept_options, fiscal_year)
        if matched_fact is None:
            continue
        total += matched_fact.get("val") or 0
        matched.append(f"{component_name}={matched_fact.get('concept')}")

    if matched:
        return total, "Summed debt components: " + " | ".join(matched)

    fallback = pick_first_annual_fact(company_facts, FINANCIAL_DEBT_FALLBACKS, fiscal_year)
    if fallback:
        return fallback.get("val"), f"Used debt fallback concept {fallback.get('concept')}."
    return None, f"No standard annual SEC financial debt fact matched for FY{fiscal_year}."


def extract_metrics_for_year(company_facts: dict, fiscal_year: int) -> tuple[dict, dict]:
    metrics = {}
    notes = {}
    for metric_key, concept_options in CONCEPTS.items():
        matched_fact = pick_first_annual_fact(company_facts, concept_options, fiscal_year)
        if matched_fact is None:
            metrics[metric_key] = None
            notes[metric_key] = f"No standard annual SEC fact matched for FY{fiscal_year}."
        else:
            metrics[metric_key] = matched_fact.get("val")
            notes[metric_key] = f"Matched {matched_fact.get('concept')} ({matched_fact.get('form')}, FY{matched_fact.get('fy')})."

    financial_debt, debt_note = extract_financial_debt(company_facts, fiscal_year)
    metrics["financial_debt"] = financial_debt
    notes["financial_debt"] = debt_note

    metrics["interest_expense_abs"] = clean_abs(metrics.get("interest_expense"))
    metrics["debt_ratio"] = safe_div(metrics.get("total_liabilities"), metrics.get("total_equity"))
    metrics["current_ratio"] = safe_div(metrics.get("current_assets"), metrics.get("current_liabilities"))
    metrics["operating_margin"] = safe_div(metrics.get("operating_income"), metrics.get("revenue"))
    metrics["net_margin"] = safe_div(metrics.get("net_income"), metrics.get("revenue"))
    metrics["roa"] = safe_div(metrics.get("net_income"), metrics.get("total_assets"))
    metrics["interest_coverage"] = safe_div(metrics.get("operating_income"), metrics.get("interest_expense_abs"))
    metrics["financial_debt_to_operating_income"] = safe_div(metrics.get("financial_debt"), metrics.get("operating_income"))
    metrics["operating_cf_to_financial_debt"] = safe_div(metrics.get("operating_cash_flow"), metrics.get("financial_debt"))
    metrics["working_capital"] = (
        None
        if metrics.get("current_assets") is None or metrics.get("current_liabilities") is None
        else metrics["current_assets"] - metrics["current_liabilities"]
    )
    return metrics, notes


def infer_filing_meta_for_year(company_facts: dict, fiscal_year: int) -> dict:
    latest = None
    starts = []
    ends = []
    for taxonomy_map in (company_facts.get("facts") or {}).values():
        for concept_map in taxonomy_map.values():
            for unit_rows in (concept_map.get("units") or {}).values():
                for row in unit_rows:
                    form = str(row.get("form") or "")
                    fy = row.get("fy")
                    fp = str(row.get("fp") or "")
                    if form not in ANNUAL_FORMS or not fy or int(fy) != int(fiscal_year):
                        continue
                    if fp and fp != "FY":
                        continue
                    if row.get("start"):
                        starts.append(str(row.get("start")))
                    if row.get("end"):
                        ends.append(str(row.get("end")))
                    candidate = {
                        "fiscal_year": int(fy),
                        "fiscal_period": fp or "FY",
                        "form": form,
                        "filed": str(row.get("filed") or ""),
                        "period_start": str(row.get("start") or ""),
                        "period_end": str(row.get("end") or ""),
                    }
                    if latest is None or candidate["filed"] > latest["filed"]:
                        latest = candidate
    if latest:
        latest["period_start"] = min(starts) if starts else latest.get("period_start") or None
        latest["period_end"] = max(ends) if ends else latest.get("period_end") or None
        return latest
    return {"fiscal_year": fiscal_year, "fiscal_period": "FY", "form": "-", "filed": "-", "period_start": None, "period_end": None}


def final_grade(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 90:
        return "A"
    if score >= 80:
        return "B1"
    if score >= 70:
        return "B2"
    if score >= 50:
        return "C"
    return "D"


def grade_higher_better(value: float | None, thresholds: list[tuple[str, float]]) -> str | None:
    if value is None:
        return None
    for grade, threshold in thresholds:
        if value >= threshold:
            return grade
    return "D"


def grade_lower_better(value: float | None, thresholds: list[tuple[str, float]]) -> str | None:
    if value is None:
        return None
    for grade, threshold in thresholds:
        if value <= threshold:
            return grade
    return "D"


def grade_debt_ratio(value: float | None) -> str | None:
    if value is None:
        return None
    if value <= 1.0:
        return "AAA"
    if value <= 2.0:
        return "AA"
    if value <= 3.0:
        return "BB"
    if value <= 4.0:
        return "CC"
    return "D"


def grade_current_ratio(value: float | None) -> str | None:
    if value is None:
        return None
    if value >= 1.0:
        return "AA"
    if value >= 0.9:
        return "BB"
    if value >= 0.8:
        return "CC"
    return "D"


def grade_financial_debt_to_operating_income(value: float | None, operating_income_krw: float | None) -> str | None:
    if operating_income_krw is not None and operating_income_krw <= 0:
        return "D"
    return grade_lower_better(
        value,
        [
            ("AAA", 0.25),
            ("AA", 0.75),
            ("A", 1.50),
            ("BB", 2.75),
            ("B", 4.50),
            ("CC", 6.50),
            ("C", 9.00),
        ],
    )


def translate_usd(value: float | None, rate: float | None) -> float | None:
    if value is None or rate in (None, 0):
        return None
    return value * rate


def millions_krw(value_krw: float | None) -> float | None:
    if value_krw is None:
        return None
    return value_krw / 1_000_000


def component(metric_key: str, label: str, value, grade: str | None, weight: float, note: str = "") -> dict:
    effective_grade = grade or "D"
    return {
        "metric_key": metric_key,
        "label": label,
        "value": value,
        "grade": effective_grade,
        "weight": weight,
        "points": GRADE_POINTS[effective_grade],
        "weighted_points": GRADE_POINTS[effective_grade] * weight / 100,
        "note": note or ("Missing data conservatively scored as D." if grade is None else ""),
    }


def build_company_rating(
    result: ScreeningResult,
    prior_result: ScreeningResult | None,
    exchange_rates: dict,
) -> dict:
    rates = exchange_rates.get((result.cik, result.fiscal_year), exchange_rates.get(result.fiscal_year, {}))
    prior_rates = (
        exchange_rates.get((prior_result.cik, prior_result.fiscal_year), exchange_rates.get(prior_result.fiscal_year, {}))
        if prior_result
        else {}
    )
    closing_rate = rates.get("closing")
    average_rate = rates.get("average")
    prior_closing_rate = prior_rates.get("closing")

    revenue_krw = translate_usd(result.metrics.get("revenue"), average_rate)
    operating_income_krw = translate_usd(result.metrics.get("operating_income"), average_rate)
    interest_expense_krw = translate_usd(result.metrics.get("interest_expense_abs"), average_rate)
    operating_cf_krw = translate_usd(result.metrics.get("operating_cash_flow"), average_rate)
    financial_debt_krw = translate_usd(result.metrics.get("financial_debt"), closing_rate)
    accounts_receivable_krw = translate_usd(result.metrics.get("accounts_receivable"), closing_rate)

    prior_accounts_receivable_krw = None
    if prior_result is not None:
        prior_accounts_receivable_krw = translate_usd(prior_result.metrics.get("accounts_receivable"), prior_closing_rate)

    average_receivable_krw = accounts_receivable_krw
    receivable_note = "Current year accounts receivable used because prior-year SEC receivable was unavailable."
    if accounts_receivable_krw is not None and prior_accounts_receivable_krw is not None:
        average_receivable_krw = (accounts_receivable_krw + prior_accounts_receivable_krw) / 2
        receivable_note = "Average of current and prior year accounts receivable."

    receivable_turnover_days = None
    if average_receivable_krw is not None and revenue_krw not in (None, 0):
        receivable_turnover_days = average_receivable_krw / revenue_krw * 365

    revenue_mil = millions_krw(revenue_krw)
    operating_income_mil = millions_krw(operating_income_krw)
    financial_debt_to_op = safe_div(financial_debt_krw, operating_income_krw)
    ocf_to_debt = safe_div(operating_cf_krw, financial_debt_krw)
    interest_coverage = safe_div(operating_income_krw, interest_expense_krw)

    components = [
        component(
            "revenue",
            "Revenue",
            revenue_mil,
            grade_higher_better(
                revenue_mil,
                [("AAA", 40000), ("AA", 15000), ("A", 12000), ("BB", 7000), ("B", 3500), ("CC", 1000), ("C", 250)],
            ),
            RATING_WEIGHTS["revenue"],
            "KRW million translated at average USD/KRW rate.",
        ),
        component(
            "operating_income",
            "Operating Income",
            operating_income_mil,
            grade_higher_better(
                operating_income_mil,
                [("AAA", 4000), ("AA", 2000), ("A", 1500), ("BB", 750), ("B", 250), ("CC", 125), ("C", 60)],
            ),
            RATING_WEIGHTS["operating_income"],
            "KRW million translated at average USD/KRW rate.",
        ),
        component(
            "interest_coverage",
            "Interest Coverage",
            interest_coverage,
            grade_higher_better(
                interest_coverage,
                [("AAA", 20), ("AA", 15), ("A", 10), ("BB", 5), ("B", 2.25), ("CC", 1), ("C", 0.5)],
            ),
            RATING_WEIGHTS["interest_coverage"],
        ),
        component(
            "financial_debt_to_operating_income",
            "Financial Debt / Operating Income",
            financial_debt_to_op,
            grade_financial_debt_to_operating_income(financial_debt_to_op, operating_income_krw),
            RATING_WEIGHTS["financial_debt_to_operating_income"],
            "Financial debt translated at closing rate; operating income at average rate.",
        ),
        component(
            "operating_cf_to_financial_debt",
            "Operating CF / Financial Debt",
            ocf_to_debt,
            grade_higher_better(ocf_to_debt, [("AAA", 1.0), ("AA", 0.8), ("A", 0.55), ("BB", 0.35), ("B", 0.2), ("CC", 0.1), ("C", 0.05)]),
            RATING_WEIGHTS["operating_cf_to_financial_debt"],
            "Operating cash flow translated at average rate; financial debt at closing rate.",
        ),
        component(
            "debt_ratio",
            "Liabilities / Equity",
            result.metrics.get("debt_ratio"),
            grade_debt_ratio(result.metrics.get("debt_ratio")),
            RATING_WEIGHTS["debt_ratio"],
            "Duplicate threshold groups use the lower grade, per user instruction.",
        ),
        component(
            "receivable_turnover_days",
            "Receivable Turnover Days",
            receivable_turnover_days,
            grade_lower_better(
                receivable_turnover_days,
                [("AAA", 30), ("AA", 40), ("A", 50), ("BB", 60), ("B", 70), ("CC", 80), ("C", 90)],
            ),
            RATING_WEIGHTS["receivable_turnover_days"],
            receivable_note,
        ),
        component(
            "current_ratio",
            "Current Ratio",
            result.metrics.get("current_ratio"),
            grade_current_ratio(result.metrics.get("current_ratio")),
            RATING_WEIGHTS["current_ratio"],
            "Duplicate threshold groups use the lower grade, per user instruction.",
        ),
    ]
    weighted_score = sum(item["weighted_points"] for item in components)
    return {
        "exchange_rates": {
            "closing": closing_rate,
            "average": average_rate,
            "closing_date": rates.get("closing_date"),
            "average_start": rates.get("average_start"),
            "average_end": rates.get("average_end"),
            "source": rates.get("source"),
        },
        "translated_metrics": {
            "revenue_mil_krw": revenue_mil,
            "operating_income_mil_krw": operating_income_mil,
            "financial_debt_mil_krw": millions_krw(financial_debt_krw),
            "operating_cf_mil_krw": millions_krw(operating_cf_krw),
            "accounts_receivable_mil_krw": millions_krw(accounts_receivable_krw),
            "receivable_turnover_days": receivable_turnover_days,
        },
        "components": components,
        "weighted_score": weighted_score,
        "final_grade": final_grade(weighted_score),
        "point_scale_note": "Component grade points are configurable: AAA=100, AA=95, A=90, BB=80, B=70, CC=60, C=50, D=40.",
    }


def apply_company_ratings(results: list[ScreeningResult], exchange_rates: dict[int, dict[str, float]]) -> None:
    by_company: dict[str, list[ScreeningResult]] = {}
    for result in results:
        by_company.setdefault(result.cik, []).append(result)

    for company_results in by_company.values():
        company_results.sort(key=lambda item: item.fiscal_year)
        for index, result in enumerate(company_results):
            prior_result = company_results[index - 1] if index > 0 else None
            result.rating = build_company_rating(result, prior_result, exchange_rates)


def build_red_flags(metrics: dict) -> list[str]:
    flags = []
    if metrics.get("debt_ratio") is not None and metrics["debt_ratio"] >= 2:
        flags.append("Preliminary red flag: liabilities / equity is 200% or higher.")
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


def screen_company_year(match: CompanyMatch, company_facts: dict, fiscal_year: int) -> ScreeningResult:
    metrics, notes = extract_metrics_for_year(company_facts, fiscal_year)
    filing_meta = infer_filing_meta_for_year(company_facts, fiscal_year)
    return ScreeningResult(
        company_name=match.company_name,
        ticker=match.ticker,
        cik=match.cik,
        fiscal_year=fiscal_year,
        fiscal_period=filing_meta["fiscal_period"],
        form=filing_meta["form"],
        filed=filing_meta["filed"],
        period_start=filing_meta.get("period_start"),
        period_end=filing_meta.get("period_end"),
        metrics=metrics,
        notes=notes,
        red_flags=build_red_flags(metrics),
    )


def select_target_years(company_facts: dict, start_year: int, end_year: int) -> list[int]:
    if start_year > end_year:
        raise ValueError("Start year cannot be later than end year.")
    available = annual_years_available(company_facts)
    return [year for year in available if start_year <= year <= end_year]


def screen_company(match: CompanyMatch) -> ScreeningResult:
    company_facts = fetch_company_facts(match.cik)
    available = annual_years_available(company_facts)
    if not available:
        raise ValueError("No annual SEC facts found.")
    return screen_company_year(match, company_facts, max(available))


def screen_companies(
    matches: list[CompanyMatch],
    start_year: int,
    end_year: int,
    exchange_rates: dict[int, dict[str, float]] | None = None,
) -> tuple[list[ScreeningResult], list[ScreeningError]]:
    results: list[ScreeningResult] = []
    errors: list[ScreeningError] = []
    for match in matches:
        try:
            company_facts = fetch_company_facts(match.cik)
            target_years = select_target_years(company_facts, start_year, end_year)
            if not target_years:
                errors.append(
                    ScreeningError(
                        input_query=match.ticker or match.company_name,
                        company_name=match.company_name,
                        ticker=match.ticker,
                        cik=match.cik,
                        fiscal_year=None,
                        error_message=f"No annual SEC facts found between {start_year} and {end_year}.",
                    )
                )
                continue
            for fiscal_year in target_years:
                try:
                    results.append(screen_company_year(match, company_facts, fiscal_year))
                except Exception as exc:
                    errors.append(
                        ScreeningError(
                            input_query=match.ticker or match.company_name,
                            company_name=match.company_name,
                            ticker=match.ticker,
                            cik=match.cik,
                            fiscal_year=fiscal_year,
                            error_message=str(exc),
                        )
                    )
        except Exception as exc:
            errors.append(
                ScreeningError(
                    input_query=match.ticker or match.company_name,
                    company_name=match.company_name,
                    ticker=match.ticker,
                    cik=match.cik,
                    fiscal_year=None,
                    error_message=str(exc),
                )
            )
    results.sort(key=lambda item: (item.company_name, item.fiscal_year))
    if exchange_rates:
        apply_company_ratings(results, exchange_rates)
    return results, errors


def result_to_summary_row(result: ScreeningResult) -> dict:
    return {
        "Company": result.company_name,
        "Ticker": result.ticker,
        "CIK": result.cik,
        "Fiscal Year": result.fiscal_year,
        "Form": result.form,
        "Filed": result.filed,
        "Period Start": result.period_start,
        "Period End": result.period_end,
        "Revenue": result.metrics.get("revenue"),
        "Operating Income": result.metrics.get("operating_income"),
        "Net Income": result.metrics.get("net_income"),
        "Total Assets": result.metrics.get("total_assets"),
        "Total Liabilities": result.metrics.get("total_liabilities"),
        "Total Equity": result.metrics.get("total_equity"),
        "Financial Debt": result.metrics.get("financial_debt"),
        "Interest Expense": result.metrics.get("interest_expense_abs"),
        "Accounts Receivable": result.metrics.get("accounts_receivable"),
        "Liabilities / Equity": result.metrics.get("debt_ratio"),
        "Current Ratio": result.metrics.get("current_ratio"),
        "Operating Margin": result.metrics.get("operating_margin"),
        "Net Margin": result.metrics.get("net_margin"),
        "ROA": result.metrics.get("roa"),
        "Interest Coverage": result.metrics.get("interest_coverage"),
        "Financial Debt / Operating Income": result.metrics.get("financial_debt_to_operating_income"),
        "Operating CF / Financial Debt": result.metrics.get("operating_cf_to_financial_debt"),
        "Operating Cash Flow": result.metrics.get("operating_cash_flow"),
        "Working Capital": result.metrics.get("working_capital"),
        "Internal Score": result.rating.get("weighted_score") if result.rating else None,
        "Internal Grade": result.rating.get("final_grade") if result.rating else None,
        "Closing USD/KRW": result.rating.get("exchange_rates", {}).get("closing") if result.rating else None,
        "Average USD/KRW": result.rating.get("exchange_rates", {}).get("average") if result.rating else None,
        "Red Flag Count": len(result.red_flags),
    }


def result_to_rating_rows(result: ScreeningResult) -> list[dict]:
    if not result.rating:
        return []
    rows = []
    for item in result.rating.get("components", []):
        rows.append(
            {
                "Company": result.company_name,
                "Ticker": result.ticker,
                "Fiscal Year": result.fiscal_year,
                "Metric": item["label"],
                "Value": item["value"],
                "Grade": item["grade"],
                "Weight": item["weight"],
                "Points": item["points"],
                "Weighted Points": item["weighted_points"],
                "Note": item["note"],
            }
        )
    return rows


def result_to_note_rows(result: ScreeningResult) -> list[dict]:
    rows = [
        {
            "Company": result.company_name,
            "Ticker": result.ticker,
            "Fiscal Year": result.fiscal_year,
            "Metric": key,
            "Note": note,
        }
        for key, note in result.notes.items()
    ]
    if result.rating:
        rows.append(
            {
                "Company": result.company_name,
                "Ticker": result.ticker,
                "Fiscal Year": result.fiscal_year,
                "Metric": "internal_rating",
                "Note": result.rating.get("point_scale_note", ""),
            }
        )
    return rows


def result_to_flag_rows(result: ScreeningResult) -> list[dict]:
    return [
        {
            "Company": result.company_name,
            "Ticker": result.ticker,
            "Fiscal Year": result.fiscal_year,
            "Seq": index,
            "Flag": flag,
        }
        for index, flag in enumerate(result.red_flags, start=1)
    ]


def error_to_row(error: ScreeningError) -> dict:
    return {
        "Input": error.input_query,
        "Company": error.company_name,
        "Ticker": error.ticker,
        "CIK": error.cik,
        "Fiscal Year": error.fiscal_year if error.fiscal_year is not None else "-",
        "Error": error.error_message,
    }
