from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from sec_edgar_client import CompanyMatch, get_user_agent, search_companies
from sec_excel_export import write_screening_workbook
from sec_screening import result_to_flag_rows, result_to_note_rows, result_to_summary_row, screen_company


APP_TITLE = "SEC Preliminary Financial Screening"


def create_dialog_root():
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("820x680")
    root.minsize(760, 620)
    return root


def choose_output_path(root):
    return filedialog.asksaveasfilename(
        parent=root,
        title="Save screening workbook",
        defaultextension=".xlsx",
        filetypes=[("Excel workbook", "*.xlsx")],
        initialfile="sec_financial_screening.xlsx",
    )


def run_with_wait(root, message, target, *args):
    wait = tk.Toplevel(root)
    wait.title(APP_TITLE)
    wait.geometry("420x140")
    wait.resizable(False, False)
    wait.transient(root)
    wait.grab_set()

    label = tk.Label(wait, text=message, padx=24, pady=20, font=("Malgun Gothic", 10), justify="left")
    label.pack(fill=tk.BOTH, expand=True)

    holder = {}
    result_queue = queue.Queue()

    def worker():
        try:
            result_queue.put(("result", target(*args)))
        except Exception as exc:
            result_queue.put(("error", exc))

    def poll():
        try:
            kind, payload = result_queue.get_nowait()
        except queue.Empty:
            if wait.winfo_exists():
                root.after(120, poll)
            return
        holder[kind] = payload
        if wait.winfo_exists():
            wait.destroy()

    threading.Thread(target=worker, daemon=True).start()
    root.after(120, poll)
    root.wait_window(wait)
    if "error" in holder:
        raise holder["error"]
    return holder["result"]


def choose_company(root, matches: list[CompanyMatch]) -> CompanyMatch | None:
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    window = tk.Toplevel(root)
    window.title("Choose SEC company")
    window.geometry("760x360")
    window.transient(root)
    window.grab_set()

    tk.Label(
        window,
        text="Select the company to screen.",
        font=("Malgun Gothic", 11, "bold"),
        pady=10,
    ).pack(anchor="w", padx=16)

    listbox = tk.Listbox(window, font=("Malgun Gothic", 10))
    listbox.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))
    for match in matches:
        industry = match.sic_description or "-"
        listbox.insert(
            tk.END,
            f"{match.company_name} | {match.ticker} | CIK {match.cik} | {industry}",
        )
    listbox.selection_set(0)

    selected = {"value": None}

    def confirm():
        selection = listbox.curselection()
        if not selection:
            messagebox.showwarning(APP_TITLE, "Select a company.", parent=window)
            return
        selected["value"] = matches[selection[0]]
        window.destroy()

    def cancel():
        window.destroy()

    button_frame = tk.Frame(window)
    button_frame.pack(fill=tk.X, padx=16, pady=(0, 16))
    tk.Button(button_frame, text="Select", width=10, command=confirm).pack(side=tk.RIGHT)
    tk.Button(button_frame, text="Cancel", width=10, command=cancel).pack(side=tk.RIGHT, padx=(0, 8))

    root.wait_window(window)
    return selected["value"]


def format_amount(value):
    if value is None:
        return "-"
    return f"{value:,.0f}"


def format_ratio(value):
    if value is None:
        return "-"
    return f"{value * 100:,.1f}%"


def format_multiple(value):
    if value is None:
        return "-"
    return f"{value:,.2f}x"


def build_preview_text(result) -> str:
    lines = [
        f"Company: {result.company_name}",
        f"Ticker: {result.ticker}",
        f"CIK: {result.cik}",
        f"Latest FY: {result.fiscal_year}",
        f"Form: {result.form}",
        f"Filed: {result.filed}",
        "",
        "Key metrics",
        f"  Revenue: {format_amount(result.metrics.get('revenue'))}",
        f"  Operating income: {format_amount(result.metrics.get('operating_income'))}",
        f"  Net income: {format_amount(result.metrics.get('net_income'))}",
        f"  Total assets: {format_amount(result.metrics.get('total_assets'))}",
        f"  Total liabilities: {format_amount(result.metrics.get('total_liabilities'))}",
        f"  Total equity: {format_amount(result.metrics.get('total_equity'))}",
        f"  Debt ratio: {format_ratio(result.metrics.get('debt_ratio'))}",
        f"  Current ratio: {format_multiple(result.metrics.get('current_ratio'))}",
        f"  Operating margin: {format_ratio(result.metrics.get('operating_margin'))}",
        f"  Net margin: {format_ratio(result.metrics.get('net_margin'))}",
        f"  ROA: {format_ratio(result.metrics.get('roa'))}",
        f"  Operating cash flow: {format_amount(result.metrics.get('operating_cash_flow'))}",
        "",
        "Preliminary red flags",
    ]
    lines.extend(f"  - {flag}" for flag in result.red_flags)
    lines.append("")
    lines.append("Metric notes")
    for key, note in result.notes.items():
        lines.append(f"  - {key}: {note}")
    return "\n".join(lines)


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.configure(padx=18, pady=18)
        self.selected_match: CompanyMatch | None = None
        self.latest_result = None

        self.query_var = tk.StringVar(value="MSFT")
        self.output_var = tk.StringVar(value=str(Path.cwd() / "sec_financial_screening.xlsx"))

        tk.Label(root, text="Ticker or company name", font=("Malgun Gothic", 11, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(
            root,
            text="This desktop SEC version does not require an API key. Set SEC_USER_AGENT for production identification.",
            fg="#555555",
            wraplength=760,
            justify="left",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))

        tk.Entry(root, textvariable=self.query_var, width=46, font=("Malgun Gothic", 10)).grid(row=2, column=0, sticky="w")
        tk.Button(root, text="Find company", width=14, command=self.preview_candidates).grid(row=2, column=1, sticky="w", padx=(8, 0))
        tk.Button(root, text="Run screening", width=14, command=self.run_screening).grid(row=2, column=2, sticky="e")

        tk.Label(root, text="Save workbook").grid(row=3, column=0, sticky="w", pady=(12, 4))
        tk.Entry(root, textvariable=self.output_var, width=74).grid(row=4, column=0, columnspan=2, sticky="ew")
        tk.Button(root, text="Browse", command=self.pick_output).grid(row=4, column=2, sticky="e", padx=(8, 0))

        info_frame = tk.Frame(root)
        info_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(14, 10))
        self.user_agent_label = tk.Label(info_frame, text=f"User-Agent: {get_user_agent()}", anchor="w", justify="left", fg="#333333")
        self.user_agent_label.pack(fill=tk.X)
        self.selection_label = tk.Label(info_frame, text="Selected company: -", anchor="w", justify="left", fg="#333333")
        self.selection_label.pack(fill=tk.X, pady=(6, 0))

        tk.Label(root, text="Preview / result", font=("Malgun Gothic", 11, "bold")).grid(row=6, column=0, sticky="w")
        self.result_text = scrolledtext.ScrolledText(root, width=90, height=24, font=("Consolas", 10))
        self.result_text.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        self.result_text.insert("1.0", "Find a company first, then run screening.\n")
        self.result_text.configure(state="disabled")

        export_frame = tk.Frame(root)
        export_frame.grid(row=8, column=0, columnspan=3, sticky="w", pady=(12, 0))
        tk.Button(export_frame, text="Save Excel", width=12, command=self.export_excel).pack(side=tk.LEFT)
        tk.Button(export_frame, text="Clear", width=12, command=self.clear_output).pack(side=tk.LEFT, padx=(8, 0))

        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(7, weight=1)

    def set_result_text(self, text: str):
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", text)
        self.result_text.configure(state="disabled")

    def pick_output(self):
        output = choose_output_path(self.root)
        if output:
            self.output_var.set(output)

    def clear_output(self):
        self.selected_match = None
        self.latest_result = None
        self.selection_label.configure(text="Selected company: -")
        self.set_result_text("Find a company first, then run screening.\n")

    def preview_candidates(self):
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning(APP_TITLE, "Enter a ticker or company name.", parent=self.root)
            return
        try:
            matches = run_with_wait(self.root, "Searching SEC company candidates...", search_companies, query, 8)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Company search failed:\n{exc}", parent=self.root)
            return
        if not matches:
            messagebox.showinfo(APP_TITLE, "No SEC company match found.", parent=self.root)
            return
        selected = choose_company(self.root, matches)
        if selected is None:
            return
        self.selected_match = selected
        self.latest_result = None
        self.selection_label.configure(
            text=f"Selected company: {selected.company_name} ({selected.ticker}, CIK {selected.cik})"
        )
        preview_text = [
            f"Selected company: {selected.company_name}",
            f"Ticker: {selected.ticker}",
            f"CIK: {selected.cik}",
            f"Industry: {selected.sic_description or '-'}",
            "",
            "Ready to run SEC screening.",
        ]
        self.set_result_text("\n".join(preview_text))

    def run_screening(self):
        if self.selected_match is None:
            messagebox.showwarning(APP_TITLE, "Find and select a company first.", parent=self.root)
            return
        try:
            result = run_with_wait(
                self.root,
                "Downloading SEC company facts and calculating screening metrics...",
                screen_company,
                self.selected_match,
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Screening failed:\n{exc}", parent=self.root)
            return
        self.latest_result = result
        self.set_result_text(build_preview_text(result))

    def export_excel(self):
        if self.latest_result is None:
            messagebox.showwarning(APP_TITLE, "Run screening first.", parent=self.root)
            return
        output = Path(self.output_var.get().strip())
        if not output.name:
            messagebox.showwarning(APP_TITLE, "Choose a valid save path.", parent=self.root)
            return
        try:
            write_screening_workbook(
                [result_to_summary_row(self.latest_result)],
                result_to_flag_rows(self.latest_result),
                result_to_note_rows(self.latest_result),
                output,
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Excel export failed:\n{exc}", parent=self.root)
            return
        messagebox.showinfo(APP_TITLE, f"Saved:\n{output}", parent=self.root)


def main():
    root = create_dialog_root()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
