from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from sec_edgar_client import CompanyMatch, get_user_agent, search_companies
from sec_excel_export import workbook_bytes
from sec_screening import error_to_row, result_to_flag_rows, result_to_note_rows, result_to_summary_row, screen_companies


APP_TITLE = "SEC Preliminary Financial Screening"


def parse_input_queries(raw_text: str) -> list[str]:
    queries = []
    for part in raw_text.replace("\r", "\n").replace(",", "\n").split("\n"):
        cleaned = part.strip()
        if cleaned:
            queries.append(cleaned)
    return queries


def render_match_table(matches: list[CompanyMatch]):
    st.dataframe(
        [
            {
                "Company": match.company_name,
                "Ticker": match.ticker,
                "CIK": match.cik,
                "SIC": match.sic or "-",
                "Industry": match.sic_description or "-",
            }
            for match in matches
        ],
        width="stretch",
        hide_index=True,
    )


def format_ratio(value):
    if value is None:
        return "-"
    return f"{value * 100:.1f}%"


def format_multiple(value):
    if value is None:
        return "-"
    return f"{value:.2f}x"


def percent_or_none(value):
    if value is None:
        return None
    return value * 100


def render_candidate_preview(query_matches: list[tuple[str, list[CompanyMatch]]]):
    preview_rows = []
    selected_matches = []
    for query, matches in query_matches:
        if not matches:
            preview_rows.append(
                {
                    "Input": query,
                    "Selected Company": "-",
                    "Ticker": "-",
                    "CIK": "-",
                    "Industry": "-",
                    "Note": "No SEC match found",
                }
            )
            continue
        best = matches[0]
        preview_rows.append(
            {
                "Input": query,
                "Selected Company": best.company_name,
                "Ticker": best.ticker,
                "CIK": best.cik,
                "Industry": best.sic_description or "-",
                "Note": "Top SEC match auto-selected",
            }
        )
        selected_matches.append(best)
    st.dataframe(preview_rows, width="stretch", hide_index=True)
    return selected_matches


def build_dashboard_frames(results) -> tuple[pd.DataFrame, pd.DataFrame]:
    metric_rows = []
    flag_rows = []
    for result in sorted(results, key=lambda item: item.company_name):
        severe_count = sum(
            1
            for flag in (result.red_flags or [])
            if any(token in flag for token in ("200%", "below 1.0x", "negative"))
        )
        metric_rows.append(
            {
                "Company": result.company_name,
                "Ticker": result.ticker,
                "Latest FY": result.fiscal_year,
                "Revenue ($m)": (result.metrics.get("revenue") or 0) / 1_000_000 if result.metrics.get("revenue") is not None else None,
                "Operating Margin (%)": percent_or_none(result.metrics.get("operating_margin")),
                "Debt Ratio (%)": percent_or_none(result.metrics.get("debt_ratio")),
                "Current Ratio (x)": result.metrics.get("current_ratio"),
                "Net Margin (%)": percent_or_none(result.metrics.get("net_margin")),
                "ROA (%)": percent_or_none(result.metrics.get("roa")),
                "Operating Cash Flow ($m)": (result.metrics.get("operating_cash_flow") or 0) / 1_000_000 if result.metrics.get("operating_cash_flow") is not None else None,
            }
        )
        flag_rows.append(
            {
                "Company": result.company_name,
                "Ticker": result.ticker,
                "Latest FY": result.fiscal_year,
                "Red Flag Count": len(result.red_flags or []),
                "Severe Red Flag Count": severe_count,
            }
        )
    return pd.DataFrame(metric_rows), pd.DataFrame(flag_rows)


def render_dashboard(results):
    metric_df, flag_df = build_dashboard_frames(results)
    if metric_df.empty:
        st.info("No successful screening results are available for charts.")
        return

    st.subheader("Dashboard")
    tab1, tab2, tab3 = st.tabs(["Metric charts", "Red flag charts", "Raw tables"])

    with tab1:
        c1, c2 = st.columns(2)
        revenue_chart = alt.Chart(metric_df.dropna(subset=["Revenue ($m)"])).mark_bar().encode(
            x=alt.X("Company:N", title="Company", sort="-y"),
            y=alt.Y("Revenue ($m):Q", title="Revenue ($m)"),
            tooltip=["Company", "Ticker", "Latest FY", "Revenue ($m)"],
            color=alt.value("#1f77b4"),
        ).properties(height=320, title="Latest annual revenue")
        c1.altair_chart(revenue_chart, use_container_width=True)

        debt_chart = alt.Chart(metric_df.dropna(subset=["Debt Ratio (%)"])).mark_bar().encode(
            x=alt.X("Company:N", title="Company", sort="-y"),
            y=alt.Y("Debt Ratio (%):Q", title="Debt Ratio (%)"),
            tooltip=["Company", "Ticker", "Latest FY", "Debt Ratio (%)"],
            color=alt.value("#d35400"),
        ).properties(height=320, title="Debt ratio snapshot")
        c2.altair_chart(debt_chart, use_container_width=True)

        c3, c4 = st.columns(2)
        margin_chart = alt.Chart(metric_df.dropna(subset=["Operating Margin (%)"])).mark_bar().encode(
            x=alt.X("Company:N", title="Company", sort="-y"),
            y=alt.Y("Operating Margin (%):Q", title="Operating Margin (%)"),
            tooltip=["Company", "Ticker", "Latest FY", "Operating Margin (%)"],
            color=alt.value("#2e8b57"),
        ).properties(height=320, title="Operating margin snapshot")
        c3.altair_chart(margin_chart, use_container_width=True)

        cfo_chart = alt.Chart(metric_df.dropna(subset=["Operating Cash Flow ($m)"])).mark_bar().encode(
            x=alt.X("Company:N", title="Company", sort="-y"),
            y=alt.Y("Operating Cash Flow ($m):Q", title="Operating Cash Flow ($m)"),
            tooltip=["Company", "Ticker", "Latest FY", "Operating Cash Flow ($m)"],
            color=alt.value("#6c5ce7"),
        ).properties(height=320, title="Operating cash flow snapshot")
        c4.altair_chart(cfo_chart, use_container_width=True)

    with tab2:
        red_bar = alt.Chart(flag_df).mark_bar().encode(
            x=alt.X("Company:N", title="Company", sort="-y"),
            y=alt.Y("Red Flag Count:Q", title="Red flag count"),
            tooltip=["Company", "Ticker", "Latest FY", "Red Flag Count", "Severe Red Flag Count"],
            color=alt.value("#c0392b"),
        ).properties(height=320, title="Red flag count by company")
        st.altair_chart(red_bar, use_container_width=True)

    with tab3:
        st.dataframe(metric_df, width="stretch", hide_index=True)
        st.dataframe(flag_df, width="stretch", hide_index=True)


def render_results(results, errors, workbook_binary: bytes):
    summary_rows = [result_to_summary_row(result) for result in results]
    flag_rows = [row for result in results for row in result_to_flag_rows(result)]
    note_rows = [row for result in results for row in result_to_note_rows(result)]
    error_rows = [error_to_row(error) for error in errors]
    summary_df = pd.DataFrame(summary_rows)
    flag_df = pd.DataFrame(flag_rows)
    error_df = pd.DataFrame(error_rows)

    st.success("SEC preliminary financial screening is complete. This is not a rating opinion and still requires human review.")

    col1, col2, col3 = st.columns(3)
    col1.metric("Success count", len(results))
    col2.metric("Error count", len(errors))
    col3.metric("Company count", len({result.company_name for result in results}))

    st.subheader("Latest annual summary")
    if summary_df.empty:
        st.info("No successful company result is available.")
    else:
        st.dataframe(summary_df, width="stretch", hide_index=True)

    st.subheader("Preliminary red flags")
    if flag_df.empty:
        st.info("No red flag detail is available.")
    else:
        st.dataframe(flag_df, width="stretch", hide_index=True)

    st.subheader("Errors / missing cases")
    if error_df.empty:
        st.info("No screening error occurred.")
    else:
        st.dataframe(error_df, width="stretch", hide_index=True)

    st.subheader("Metric matching notes")
    if note_rows:
        st.dataframe(pd.DataFrame(note_rows), width="stretch", hide_index=True)

    st.download_button(
        label="Download Excel workbook",
        data=workbook_binary,
        file_name="sec_financial_screening.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon=":office_building:", layout="wide")
    st.title(APP_TITLE)
    st.caption("U.S. public company preliminary financial screening tool based on SEC annual filing facts. Not a rating opinion. Human review required.")

    with st.sidebar:
        st.header("Input")
        default_queries = "MSFT\nAAPL\nNVDA\nCAT"
        query_text = st.text_area("Tickers or company names", value=default_queries, height=180, help="Enter multiple tickers or company names separated by commas or line breaks.")
        preview_button = st.button("Preview SEC matches", width="stretch")
        run_button = st.button("Run screening", type="primary", width="stretch")

    st.info(
        f"SEC API key is not required. Current request identity: `{get_user_agent()}`. "
        "For production, set `SEC_USER_AGENT` to your company name and contact email."
    )

    st.session_state.setdefault("query_matches", None)
    st.session_state.setdefault("selected_matches", None)
    st.session_state.setdefault("results", None)
    st.session_state.setdefault("errors", None)
    st.session_state.setdefault("workbook_binary", None)
    st.session_state.setdefault("error", None)
    queries = parse_input_queries(query_text)

    if preview_button:
        st.session_state["error"] = None
        st.session_state["results"] = None
        st.session_state["errors"] = None
        st.session_state["workbook_binary"] = None
        try:
            if not queries:
                st.session_state["error"] = "Enter at least one ticker or company name."
            else:
                with st.spinner("Searching SEC companies..."):
                    query_matches = [(query, search_companies(query, limit=5)) for query in queries]
                st.session_state["query_matches"] = query_matches
        except Exception as exc:
            st.session_state["error"] = str(exc)

    query_matches = st.session_state.get("query_matches") or []
    st.subheader("Company selection preview")
    if query_matches:
        selected_matches = render_candidate_preview(query_matches)
        st.session_state["selected_matches"] = selected_matches
    else:
        st.info("Preview SEC matches to see which company each input will map to.")

    if run_button:
        st.session_state["error"] = None
        st.session_state["results"] = None
        st.session_state["errors"] = None
        st.session_state["workbook_binary"] = None
        if not queries:
            st.session_state["error"] = "Enter at least one ticker or company name."
        else:
            selected_matches = st.session_state.get("selected_matches") or []
            if not selected_matches:
                st.session_state["error"] = "Run preview first so the app can resolve SEC company matches."
            else:
                try:
                    with st.status("Running SEC screening...", expanded=True) as status:
                        status.write("1. SEC company matches resolved")
                        status.write("2. Downloading SEC company facts")
                        results, errors = screen_companies(selected_matches)
                        status.write("3. Calculating preliminary metrics and red flags")
                        summary_rows = [result_to_summary_row(result) for result in results]
                        flag_rows = [row for result in results for row in result_to_flag_rows(result)]
                        note_rows = [row for result in results for row in result_to_note_rows(result)]
                        error_rows = [error_to_row(error) for error in errors]
                        binary = workbook_bytes(summary_rows, flag_rows, note_rows, error_rows)
                        status.write("4. Excel workbook generated")
                        status.update(label="Screening complete", state="complete")
                    st.session_state["results"] = results
                    st.session_state["errors"] = errors
                    st.session_state["workbook_binary"] = binary
                except Exception as exc:
                    st.session_state["error"] = str(exc)

    if st.session_state.get("error"):
        st.error(st.session_state["error"])

    if st.session_state.get("results") is not None and st.session_state.get("workbook_binary") is not None:
        render_dashboard(st.session_state["results"])
        render_results(
            st.session_state["results"],
            st.session_state.get("errors") or [],
            st.session_state["workbook_binary"],
        )

    with st.expander("About this tool", expanded=False):
        st.markdown(
            """
            - This tool uses public SEC `companyfacts` data only.
            - It currently focuses on annual facts from `10-K`, `20-F`, and `40-F`.
            - Companies do not always report every concept in a perfectly comparable way.
            - This is a preliminary financial screening workflow, not a formal credit opinion.
            - Review the summary, red flags, and metric matching notes together before using the output.
            """
        )


if __name__ == "__main__":
    main()
