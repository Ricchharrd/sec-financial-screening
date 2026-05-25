from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from sec_edgar_client import CompanyMatch, search_companies
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


def percent_or_none(value):
    if value is None:
        return None
    return value * 100


def candidate_option_label(match: CompanyMatch) -> str:
    industry = match.sic_description or "-"
    return f"{match.company_name} | {match.ticker} | CIK {match.cik} | {industry}"


def render_candidate_preview(query_matches: list[tuple[str, list[CompanyMatch]]]):
    selected_matches = []
    preview_rows = []
    for index, (query, matches) in enumerate(query_matches, start=1):
        st.markdown(f"**{index}. Input:** `{query}`")
        if not matches:
            st.warning("No SEC match found for this input.")
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

        options = [candidate_option_label(match) for match in matches]
        selected_label = st.selectbox(
            f"Choose the company for {query}",
            options=options,
            index=0,
            key=f"company_match_{index}_{query}",
            label_visibility="collapsed",
        )
        selected_match = matches[options.index(selected_label)]
        selected_matches.append(selected_match)
        preview_rows.append(
            {
                "Input": query,
                "Selected Company": selected_match.company_name,
                "Ticker": selected_match.ticker,
                "CIK": selected_match.cik,
                "Industry": selected_match.sic_description or "-",
                "Note": "User-selected SEC match",
            }
        )
        with st.expander(f"Show candidate details for {query}", expanded=False):
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
    if preview_rows:
        st.subheader("Selection summary")
        st.dataframe(preview_rows, width="stretch", hide_index=True)
    return selected_matches


def build_dashboard_frames(results) -> tuple[pd.DataFrame, pd.DataFrame]:
    metric_rows = []
    flag_rows = []
    for result in sorted(results, key=lambda item: (item.company_name, item.fiscal_year)):
        severe_count = sum(
            1
            for flag in (result.red_flags or [])
            if any(token in flag for token in ("200%", "below 1.0x", "negative"))
        )
        metric_rows.append(
            {
                "Company": result.company_name,
                "Ticker": result.ticker,
                "Fiscal Year": result.fiscal_year,
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
                "Fiscal Year": result.fiscal_year,
                "Red Flag Count": len(result.red_flags or []),
                "Severe Red Flag Count": severe_count,
            }
        )
    return pd.DataFrame(metric_rows), pd.DataFrame(flag_rows)


def line_chart(metric_df: pd.DataFrame, column: str, title: str, y_title: str, color: str):
    chart_df = metric_df.dropna(subset=[column])
    if chart_df.empty:
        st.info(f"No data available for {title.lower()}.")
        return
    chart = alt.Chart(chart_df).mark_line(point=True).encode(
        x=alt.X("Fiscal Year:O", title="Fiscal Year"),
        y=alt.Y(f"{column}:Q", title=y_title),
        color=alt.Color("Company:N", title="Company"),
        tooltip=["Company", "Ticker", "Fiscal Year", column],
    ).properties(height=320, title=title)
    st.altair_chart(chart, use_container_width=True)


def render_dashboard(results):
    metric_df, flag_df = build_dashboard_frames(results)
    if metric_df.empty:
        st.info("No successful screening results are available for charts.")
        return

    st.subheader("Dashboard")
    tab1, tab2, tab3 = st.tabs(["Trend charts", "Red flag trends", "Raw tables"])

    with tab1:
        c1, c2 = st.columns(2)
        with c1:
            line_chart(metric_df, "Revenue ($m)", "Revenue trend", "Revenue ($m)", "#1f77b4")
        with c2:
            line_chart(metric_df, "Operating Margin (%)", "Operating margin trend", "Operating Margin (%)", "#2e8b57")

        c3, c4 = st.columns(2)
        with c3:
            line_chart(metric_df, "Debt Ratio (%)", "Debt ratio trend", "Debt Ratio (%)", "#d35400")
        with c4:
            line_chart(metric_df, "Operating Cash Flow ($m)", "Operating cash flow trend", "Operating Cash Flow ($m)", "#6c5ce7")

    with tab2:
        if flag_df.empty:
            st.info("No red flag chart data is available.")
        else:
            red_chart = alt.Chart(flag_df).mark_line(point=True).encode(
                x=alt.X("Fiscal Year:O", title="Fiscal Year"),
                y=alt.Y("Red Flag Count:Q", title="Red Flag Count"),
                color=alt.Color("Company:N", title="Company"),
                tooltip=["Company", "Ticker", "Fiscal Year", "Red Flag Count", "Severe Red Flag Count"],
            ).properties(height=320, title="Red flag count trend")
            st.altair_chart(red_chart, use_container_width=True)

            severe_chart = alt.Chart(flag_df).mark_line(point=True, strokeDash=[4, 2]).encode(
                x=alt.X("Fiscal Year:O", title="Fiscal Year"),
                y=alt.Y("Severe Red Flag Count:Q", title="Severe Red Flag Count"),
                color=alt.Color("Company:N", title="Company"),
                tooltip=["Company", "Ticker", "Fiscal Year", "Severe Red Flag Count"],
            ).properties(height=320, title="Severe red flag trend")
            st.altair_chart(severe_chart, use_container_width=True)

    with tab3:
        st.dataframe(metric_df, width="stretch", hide_index=True)
        st.dataframe(flag_df, width="stretch", hide_index=True)


def render_results(results, errors, workbook_binary: bytes | None):
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

    st.subheader("Company-year summary")
    if summary_df.empty:
        st.info("No successful company-year result is available.")
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

    if workbook_binary is not None:
        st.download_button(
            label="Download Excel workbook",
            data=workbook_binary,
            file_name="sec_financial_screening.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    else:
        st.warning("Excel download is unavailable because `openpyxl` is not installed in the current environment.")


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon=":office_building:", layout="wide")
    st.title(APP_TITLE)
    st.caption("U.S. public company preliminary financial screening tool based on SEC annual filing facts. Not a rating opinion. Human review required.")

    current_year = 2026
    with st.sidebar:
        st.header("Input")
        default_queries = "MSFT\nAAPL\nNVDA\nCAT"
        query_text = st.text_area("Tickers or company names", value=default_queries, height=180, help="Enter multiple tickers or company names separated by commas or line breaks.")
        start_year = st.number_input("Start year", min_value=2000, max_value=current_year, value=current_year - 2, step=1)
        end_year = st.number_input("End year", min_value=2000, max_value=current_year, value=current_year, step=1)
        preview_button = st.button("Preview SEC matches", width="stretch")
        run_button = st.button("Run screening", type="primary", width="stretch")

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
        elif int(start_year) > int(end_year):
            st.session_state["error"] = "Start year cannot be later than end year."
        else:
            selected_matches = st.session_state.get("selected_matches") or []
            if not selected_matches:
                st.session_state["error"] = "Run preview first so the app can resolve SEC company matches."
            else:
                try:
                    with st.status("Running SEC screening...", expanded=True) as status:
                        status.write("1. SEC company matches resolved")
                        status.write("2. Downloading SEC company facts")
                        results, errors = screen_companies(selected_matches, int(start_year), int(end_year))
                        status.write("3. Building multi-year metrics and red flags")
                        summary_rows = [result_to_summary_row(result) for result in results]
                        flag_rows = [row for result in results for row in result_to_flag_rows(result)]
                        note_rows = [row for result in results for row in result_to_note_rows(result)]
                        error_rows = [error_to_row(error) for error in errors]
                        binary = None
                        try:
                            binary = workbook_bytes(summary_rows, flag_rows, note_rows, error_rows)
                            status.write("4. Excel workbook generated")
                        except Exception as exc:
                            status.write(f"4. Excel workbook skipped: {exc}")
                        status.update(label="Screening complete", state="complete")
                    st.session_state["results"] = results
                    st.session_state["errors"] = errors
                    st.session_state["workbook_binary"] = binary
                except Exception as exc:
                    st.session_state["error"] = str(exc)

    if st.session_state.get("error"):
        st.error(st.session_state["error"])

    if st.session_state.get("results") is not None:
        render_dashboard(st.session_state["results"])
        render_results(
            st.session_state["results"],
            st.session_state.get("errors") or [],
            st.session_state.get("workbook_binary"),
        )

    with st.expander("About this tool", expanded=False):
        st.markdown(
            """
            - This tool uses public SEC `companyfacts` data only.
            - It focuses on annual facts from `10-K`, `20-F`, and `40-F`.
            - You can choose the fiscal-year range to screen.
            - Multiple companies are shown as time-series so you can compare their trends.
            - This is a preliminary financial screening workflow, not a formal credit opinion.
            """
        )


if __name__ == "__main__":
    main()
