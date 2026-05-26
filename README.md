# SEC Financial Screening MVP

This folder contains a separate U.S. SEC / EDGAR-based screening tool.

## What it does

- Looks up multiple companies by ticker or company name
- Resolves each company to an SEC CIK
- Lets the user choose the matched company when there are multiple candidates
- Pulls public company facts from SEC EDGAR `submissions` and `companyfacts`
- Calculates a small set of preliminary screening metrics across a selected year range
- Applies the internal financial capability rating grid using built-in USD/KRW rates
- Shows the result in a Streamlit web UI
- Generates an Excel workbook download from the web app
- Displays multi-year trend charts so several companies can be compared side by side

## MVP scope

This version is intentionally narrow:

- Focus on annual screening
- Prefer standard `us-gaap` concepts from SEC `companyfacts`
- Use annual facts for each company across the requested fiscal-year range
- Translate balance sheet items with closing USD/KRW rates
- Translate income statement and cash flow items with average USD/KRW rates
- Show preliminary red flags and internal rating details

## FX rates

The Streamlit app does not call FRED at runtime. It uses a hardcoded USD/KRW table for FY2020-FY2025, based on FRED annual average `AEXKOUS` and year-end or last-observed daily `DEXKOUS` rates.

## Internal rating note

The current score conversion is configurable:

- `AAA=100`
- `AA=95`
- `A=90`
- `BB=80`
- `B=70`
- `CC=60`
- `C=50`
- `D=40`

Some company threshold rows have duplicated bands. Following the current working assumption, duplicated bands use the lower grade. For example, a 300% liabilities/equity band is scored as `BB`.

## Important note

This is not a credit rating tool. It is a preliminary financial screening tool and still requires human review.

## SEC API key

The SEC EDGAR data APIs do not require an API key.

However, SEC guidance asks automated tools to:

- declare a descriptive `User-Agent`
- keep request volume moderate

You should set a contact identity for requests. In this app, that is done with:

- `SEC_USER_AGENT`

Example:

```powershell
$env:SEC_USER_AGENT = "MyCompany FinancialScreening your-email@company.com"
streamlit run streamlit_app.py
```

## Run locally

```powershell
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Run as desktop app

```powershell
pip install -r requirements.txt
py sec_screening_dialog.py
```

## Build Windows exe

This project includes a native desktop entry point and a PyInstaller build script.

Files:

- `sec_screening_dialog.py`
- `build_windows_exe.bat`
- `SECFinancialScreening.spec`

Build:

```powershell
build_windows_exe.bat
```

After the build finishes, the exe will be created at:

```text
dist\SECFinancialScreening.exe
```

## Suggested next steps after MVP

- add 10-Q support
- add Excel export
- add peer comparison tables
- add more robust concept fallbacks for interest expense and debt
- add cached company lookup and request throttling
