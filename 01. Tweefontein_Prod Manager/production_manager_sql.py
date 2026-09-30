from __future__ import annotations

import os
import re
import sqlite3
import sys
import threading
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from openpyxl import load_workbook


APP_TITLE = "Tweefontein Production Manager"
VERSION = "3.9.26 PENSTOCK CONTROL DASHBOARD"

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "tweefontein_production_v3_9.db"
EXPORT_DIR = BASE_DIR / "exports"

PENSTOCK_DESIGN_CHAINAGE = 244.042
PENSTOCK_TARGET_DATE = date(2026, 9, 19)
PENSTOCK_MASS_CONCRETE_M3 = 183.0315
PENSTOCK_TEMPORARY_M3 = 3.12
PENSTOCK_FINAL_M3 = 6.24
PENSTOCK_SLEEPERS_M3 = 2.88
PENSTOCK_TOTAL_CONCRETE_M3 = 195.2715
PENSTOCK_NORMAL_CAST_M3 = 9.0

PERIODS = [
    (1, "1. Jan - Feb", r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop\INREP36600 - Tweefontein TSF Project Files\09 Daily Costing\Costing\1. Jan - Feb"),
    (2, "2. Feb - Mar", r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop\INREP36600 - Tweefontein TSF Project Files\09 Daily Costing\Costing\2. Feb - Mar"),
    (3, "3. Mar - Apr", r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop\INREP36600 - Tweefontein TSF Project Files\09 Daily Costing\Costing\3. Mar - Apr"),
    (4, "4. Apr - May", r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop\INREP36600 - Tweefontein TSF Project Files\09 Daily Costing\Costing\4. Apr - May"),
    (5, "5. May - Jun", r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop\INREP36600 - Tweefontein TSF Project Files\09 Daily Costing\Costing\5. May - Jun"),
    (6, "6. Jun - Jul", r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop\INREP36600 - Tweefontein TSF Project Files\09 Daily Costing\Costing\6. Jun - Jul"),
    (7, "7. Jul - Aug", r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop\INREP36600 - Tweefontein TSF Project Files\09 Daily Costing\Costing\7. Jul - Aug"),
]

# Parent directory containing the numbered monthly costing folders.
# New folders such as "8. Aug - Sep", "9. Sep - Oct", etc. are discovered
# automatically by discover_periods(), so the source code no longer needs a
# new PERIODS entry every month.
COSTING_ROOT = Path(
    r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop\INREP36600 - Tweefontein TSF Project Files\09 Daily Costing\Costing"
)


def discover_periods():
    """Return configured + newly-created numbered costing period folders."""
    periods = {
        int(order): (int(order), period, folder_text)
        for order, period, folder_text in PERIODS
    }

    if COSTING_ROOT.exists():
        for folder in COSTING_ROOT.iterdir():
            if not folder.is_dir():
                continue

            # Expected examples:
            #   7. Jul - Aug
            #   8. Aug - Sep
            #   9. Sep - Oct
            match = re.match(r"^\s*(\d+)\.\s*(.+?)\s*$", folder.name)
            if not match:
                continue

            order = int(match.group(1))
            periods[order] = (order, folder.name, str(folder))

    return [periods[key] for key in sorted(periods)]

MONTHS = {
    "jan":1,"january":1,"feb":2,"february":2,"mar":3,"march":3,
    "apr":4,"april":4,"may":5,"jun":6,"june":6,"junl":7,
    "jul":7,"july":7,"aug":8,"august":8,"sep":9,"sept":9,
    "september":9,"oct":10,"october":10,"nov":11,"november":11,
    "dec":12,"december":12,
}

PROG_COLUMNS = {
    # Verified source columns from every TASK ... PROG tab:
    # H = Daily +Profit/Loss
    # I = Progressive / Monthly Total / Total to date
    "daily_profit_loss": "H",
    "cumulative_profit_loss": "I",
    "actual_daily": "L",
    "actual_cumulative": "M",
    "planned_daily": "N",
    "planned_cumulative": "O",
}

# -----------------------------------------------------------------------------
# TEMPORARY PRODUCTION RECOVERY MILESTONES
# Edit this one block when management gives a new cumulative target/deadline.
# The override is DISPLAY-ONLY: it does not overwrite the imported SQL programme.
# Sundays are excluded automatically.  Once the deadline has passed, the app
# falls back to the normal programme until a new active target is entered here.
# -----------------------------------------------------------------------------
PRODUCTION_RECOVERY_TARGETS = {
    6: {
        "name": "Key Cut to fill",
        "cumulative_target": 166328.0,
        "deadline": date(2026, 8, 25),
    },
    7: {
        "name": "Process fill TSF",
        "cumulative_target": 160609.0,
        "deadline": date(2026, 8, 25),
    },
    15: {
        "name": "Waste Rock L/H",
        "cumulative_target": 49205.0,
        "deadline": date(2026, 8, 25),
    },
}


def apply_production_recovery_target(df, activity_no, as_of=None):
    """Return a display copy of *df* with an active recovery plan applied.

    Recovery logic:
      remaining gap = milestone cumulative target - actual cumulative to the
                      last completed day before *as_of*
      daily target  = remaining gap / remaining non-Sunday days through deadline

    The calculation re-runs each day.  It never modifies SQLite or the source
    Excel programme.  The day after the deadline it returns the normal plan.
    """
    cfg = PRODUCTION_RECOVERY_TARGETS.get(activity_no)
    out = df.copy()
    if not cfg or out.empty:
        return out, None

    as_of = as_of or date.today()
    if isinstance(as_of, datetime):
        as_of = as_of.date()
    deadline = cfg["deadline"]

    # Temporary instruction expires automatically after the milestone date.
    if as_of > deadline:
        return out, None

    out["report_date"] = pd.to_datetime(out["report_date"])
    as_of_ts = pd.Timestamp(as_of)
    prior_day_ts = as_of_ts - pd.Timedelta(days=1)

    # Use the latest completed cumulative actual BEFORE today.  This keeps
    # today's recovery target stable while today's production is being captured.
    baseline_rows = out[
        (out["report_date"] <= prior_day_ts)
        & out["actual_cumulative"].notna()
    ].sort_values("report_date")

    if baseline_rows.empty:
        baseline_actual = 0.0
        baseline_date = None
    else:
        baseline_row = baseline_rows.iloc[-1]
        baseline_actual = float(baseline_row["actual_cumulative"] or 0.0)
        baseline_date = baseline_row["report_date"].date()

    workdays = []
    cursor = as_of
    while cursor <= deadline:
        if cursor.weekday() != 6:  # Monday=0 ... Sunday=6
            workdays.append(cursor)
        cursor += timedelta(days=1)

    remaining_gap = max(float(cfg["cumulative_target"]) - baseline_actual, 0.0)
    if not workdays or remaining_gap <= 0:
        return out, {
            **cfg,
            "baseline_actual": baseline_actual,
            "baseline_date": baseline_date,
            "remaining_gap": remaining_gap,
            "workdays_remaining": len(workdays),
            "daily_target": 0.0,
            "as_of": as_of,
            "achieved": remaining_gap <= 0,
        }

    daily_target = remaining_gap / len(workdays)
    workday_set = set(workdays)

    # Replace only the DISPLAY plan between today and the deadline.
    # Sundays are shown with zero daily target and a flat cumulative target.
    workdays_elapsed = 0
    for idx in out.sort_values("report_date").index:
        row_date = out.at[idx, "report_date"].date()
        if row_date < as_of or row_date > deadline:
            continue
        if row_date in workday_set:
            workdays_elapsed += 1
            out.at[idx, "planned_daily"] = daily_target
        elif row_date.weekday() == 6:
            out.at[idx, "planned_daily"] = 0.0
        out.at[idx, "planned_cumulative"] = (
            baseline_actual + daily_target * workdays_elapsed
        )

    return out, {
        **cfg,
        "baseline_actual": baseline_actual,
        "baseline_date": baseline_date,
        "remaining_gap": remaining_gap,
        "workdays_remaining": len(workdays),
        "daily_target": daily_target,
        "as_of": as_of,
        "achieved": False,
    }


def clean(value) -> str:
    return "" if value is None else str(value).strip()


def excel_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and value > 1000:
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
    if isinstance(value, str):
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
        if not pd.isna(parsed):
            return parsed.date()
    return None


def infer_period_end(period_name: str, file_name: str, ws) -> tuple[int, int] | None:
    text = f"{period_name} {file_name}".lower()
    hits = []
    for token, month_no in MONTHS.items():
        match = re.search(rf"\b{re.escape(token)}\b", text)
        if match:
            hits.append((match.start(), month_no))
    end_month = sorted(hits)[-1][1] if hits else None

    year = None
    for row in range(1, min(ws.max_row, 10) + 1):
        for col in range(1, min(ws.max_column, 20) + 1):
            value = ws.cell(row, col).value
            if isinstance(value, (int, float)) and 2020 <= int(value) <= 2100:
                year = int(value)
                break
        if year:
            break
    return (year, end_month) if year and end_month else None


def resolve_day(value, period_end):
    parsed = excel_date(value)
    if parsed:
        return parsed
    if not period_end or not isinstance(value, (int, float)):
        return None
    day = int(value)
    if not 1 <= day <= 31:
        return None
    year, month = period_end
    if day >= 26:
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def task_code_from_sheet(sheet_name: str):
    match = re.search(r"(TASK\s+\d+(?:\.\d+)?)", sheet_name, re.IGNORECASE)
    return match.group(1).upper() if match else None


def task_parts(task_code: str):
    match = re.search(r"(\d+)(?:\.(\d+))?", task_code)
    if not match:
        return 9999, 9999
    return int(match.group(1)), int(match.group(2) or 0)


def read_task_list(ws):
    tasks = {}
    for row in range(1, ws.max_row + 1):
        code = clean(ws.cell(row, 1).value).upper()
        if not re.fullmatch(r"TASK\s+\d+(?:\.\d+)?", code):
            continue
        description = clean(ws.cell(row, 3).value)
        section = clean(ws.cell(row, 5).value) or clean(ws.cell(row, 6).value)
        unit = clean(ws.cell(row, 7).value)
        allowable = ws.cell(row, 8).value
        tasks[code] = {
            "description": description,
            "section": section,
            "unit": unit,
            "allowable": float(allowable) if isinstance(allowable, (int, float)) else None,
        }
    return tasks


def discover_files():
    found, missing = [], []
    # Use dynamically discovered numbered costing folders. This allows new
    # monthly periods to be added without editing the Python source.
    for order, period, folder_text in discover_periods():
        folder = Path(folder_text)
        if not folder.exists():
            missing.append(str(folder))
            continue
        for path in sorted(folder.rglob("*.xlsx"), key=lambda p: p.name.lower()):
            name = path.name.lower()
            if name.startswith("~$"):
                continue
            if any(token in name for token in (
                "register", "production.xlsx", "report", "summary export",
                "borehole", "plant.xlsx"
            )):
                continue

            normalized_name = re.sub(r"\s+", " ", name).strip()
            waste_prefix = "inrep36600 phase 3 - daily costing waste rock"
            is_waste_costing = normalized_name.startswith(waste_prefix)
            is_phase3_costing = (
                normalized_name.startswith("inrep36600 phase 3 - daily costing")
                and not is_waste_costing
            )
            if not (is_phase3_costing or is_waste_costing):
                continue

            found.append((order, period, path))
    return found, missing


def management_activity(task_code: str, description: str, prog_sheet: str, file_name: str):
    d = re.sub(r"[^a-z0-9]+", " ", description.lower()).strip()
    sheet = prog_sheet.lower()
    filename = file_name.lower()

    is_waste_workbook = filename.startswith(
        "inrep36600 phase 3 - daily costing waste rock"
    )
    if is_waste_workbook:
        # Waste-rock workbook now also carries the Penstock costing package.
        # Keep the original Waste Rock activities as 15/16 and append the
        # Penstock activities after the existing Activity 17.
        waste_mapping = {
            "TASK 1": (15, "Waste Rock L/H"),
            "TASK 2": (16, "Waste Rock Process"),
            "TASK 3": (18, "Penstock Waste Rock L/H"),
            "TASK 4": (19, "Penstock Process Waste Rock"),
            "TASK 5": (20, "Penstock Erecting Formwork"),
            "TASK 6": (21, "Penstock Strip Formwork"),
            "TASK 7": (22, "Penstock Floating Concrete"),
            "TASK 8.1": (23, "Penstock Steel Fixing"),
            "TASK 8.2": (24, "Penstock Cast Blinding & Concrete"),
            "TASK 8.3": (25, "Penstock Pipe Lay"),
            "TASK 9": (26, "Penstock Laying Pipe Fittings"),
            "TASK 10": (27, "Penstock Pipe Excavation"),
        }
        return waste_mapping.get(task_code, (None, description))

    mapping = {
        "TASK 1": (1, "Clear & grub"),
        "TASK 2": (2, "L/H Clear & grub"),
        "TASK 4": (3, "Topsoil Strip"),
        "TASK 5": (4, "L/H Topsoil"),
        "TASK 8.1": (5, "Cut to fill (TSF)"),
        "TASK 7": (6, "Cut to fill Key cut"),
        "TASK 6": (7, "Processing fill"),
        "TASK 9": (9, "Cut to spoil B/C"),
        "TASK 10": (10, "Roadbed"),
        "TASK 12": (11, "RWD Silt Trap Roadbed"),
        "TASK 13": (12, "Process Fill Silt Trap"),
        "TASK 11": (17, "RWD Manhole & Subsoil"),
    }
    if task_code in mapping:
        return mapping[task_code]

    if task_code in {"TASK 3", "TASK 8.3"} or ("silt" in sheet and "spoil" in d):
        return 8, "Cut to spoil B/C (RWD/Silt Trap)"
    if task_code == "TASK 8.2" and "rwd" in sheet:
        return 14, "Cut to fill (RWD)"
    if task_code == "TASK 6.1" or "prog 6.1" in sheet or "task 6.1" in sheet:
        return 13, "Process RWD Fill"
    if "process" in d and "rwd" in (d + " " + sheet):
        return 13, "Process RWD Fill"

    return None, description


class Database:
    def __init__(self, path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.create_schema()

    def connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        # SQLite foreign-key enforcement is connection-specific.  Enabling it
        # here (rather than only in create_schema) guarantees that cascades and
        # relationship checks are active for every import/read/write session.
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def create_schema(self):
        with self.connect() as con:
            con.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS workbooks(
                id INTEGER PRIMARY KEY,
                period_order INTEGER NOT NULL,
                period_name TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT UNIQUE NOT NULL,
                modified_time REAL NOT NULL,
                imported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks(
                id INTEGER PRIMARY KEY,
                workbook_id INTEGER NOT NULL REFERENCES workbooks(id) ON DELETE CASCADE,
                task_code TEXT NOT NULL,
                task_major INTEGER NOT NULL,
                task_minor INTEGER NOT NULL,
                task_description TEXT NOT NULL,
                section TEXT,
                unit TEXT,
                allowable REAL,
                prog_sheet TEXT NOT NULL,
                management_activity_no INTEGER,
                management_activity_name TEXT,
                UNIQUE(workbook_id, prog_sheet)
            );

            CREATE TABLE IF NOT EXISTS daily_records(
                id INTEGER PRIMARY KEY,
                task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                report_date TEXT,
                excel_row INTEGER NOT NULL,
                daily_profit_loss REAL,
                cumulative_profit_loss REAL,
                actual_daily REAL,
                actual_cumulative REAL,
                planned_daily REAL,
                planned_cumulative REAL,
                source_planned_daily REAL,
                source_planned_cumulative REAL,
                UNIQUE(task_id, excel_row)
            );

            CREATE TABLE IF NOT EXISTS task_metadata(
                task_description TEXT PRIMARY KEY,
                responsible_foreman TEXT DEFAULT '',
                comments TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS office_labour(
                report_date TEXT PRIMARY KEY,
                use_default INTEGER NOT NULL DEFAULT 1,
                actual_hours REAL NOT NULL,
                overtime_factor REAL NOT NULL,
                effective_hours REAL NOT NULL,
                daily_cost REAL NOT NULL,
                day_type TEXT NOT NULL DEFAULT 'Normal Working Day',
                notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS activity_comments(
                report_date TEXT NOT NULL,
                management_activity_no INTEGER NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(report_date, management_activity_no)
            );

            CREATE TABLE IF NOT EXISTS league_target_overrides(
                team_name TEXT NOT NULL,
                target_date TEXT NOT NULL,
                target_quantity REAL NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(team_name, target_date)
            );

            CREATE TABLE IF NOT EXISTS production_no_work_dates(
                report_date TEXT PRIMARY KEY,
                reason TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_log(
                id INTEGER PRIMARY KEY,
                imported_at TEXT NOT NULL,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT
            );

            CREATE TABLE IF NOT EXISTS penstock_components(
                component_id TEXT PRIMARY KEY,
                sequence_no INTEGER NOT NULL,
                component_name TEXT NOT NULL,
                component_type TEXT NOT NULL,
                nominal_length REAL NOT NULL DEFAULT 0,
                chainage_length REAL NOT NULL DEFAULT 0,
                start_chainage REAL NOT NULL DEFAULT 0,
                end_chainage REAL NOT NULL DEFAULT 0,
                installed INTEGER NOT NULL DEFAULT 0,
                actual_date TEXT NOT NULL DEFAULT '',
                rn TEXT NOT NULL DEFAULT '',
                qc_status TEXT NOT NULL DEFAULT 'Pending',
                asbuilt_status TEXT NOT NULL DEFAULT 'Pending',
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS penstock_casts(
                item_id TEXT PRIMARY KEY,
                sequence_no INTEGER NOT NULL,
                cast_type TEXT NOT NULL,
                start_chainage REAL,
                end_chainage REAL,
                design_quantity REAL NOT NULL,
                strength_mpa REAL NOT NULL,
                actual_quantity REAL NOT NULL DEFAULT 0,
                cast_date TEXT NOT NULL DEFAULT '',
                rn TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS penstock_concrete_results(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_id TEXT UNIQUE NOT NULL,
                cast_item TEXT NOT NULL DEFAULT '',
                structure TEXT NOT NULL DEFAULT '',
                chainage TEXT NOT NULL DEFAULT '',
                rn TEXT NOT NULL DEFAULT '',
                cast_date TEXT NOT NULL,
                test_date TEXT NOT NULL,
                age_days INTEGER NOT NULL,
                required_mpa REAL NOT NULL,
                result_mpa REAL NOT NULL,
                lab_reference TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS penstock_settings(
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_records(report_date);
            CREATE INDEX IF NOT EXISTS idx_task_order ON tasks(task_major,task_minor);
            CREATE INDEX IF NOT EXISTS idx_period_order ON workbooks(period_order);
            CREATE INDEX IF NOT EXISTS idx_penstock_result_dates
                ON penstock_concrete_results(cast_date,test_date);
            """)

            # v3.9.4 migration: retain the unadjusted Excel programme values.
            # This makes the production-plan rules idempotent and preserves
            # the programme source for audit/history.
            cols = {row["name"] for row in con.execute("PRAGMA table_info(daily_records)")}
            if "source_planned_daily" not in cols:
                con.execute("ALTER TABLE daily_records ADD COLUMN source_planned_daily REAL")
            if "source_planned_cumulative" not in cols:
                con.execute("ALTER TABLE daily_records ADD COLUMN source_planned_cumulative REAL")
            con.execute(
                """UPDATE daily_records
                   SET source_planned_daily=planned_daily
                   WHERE source_planned_daily IS NULL"""
            )
            con.execute(
                """UPDATE daily_records
                   SET source_planned_cumulative=planned_cumulative
                   WHERE source_planned_cumulative IS NULL"""
            )

            self._seed_penstock_data(con)

        self.apply_daily_plan_rules()

    @staticmethod
    def _seed_penstock_data(con):
        """Create the drawing-controlled Penstock schedules once per database."""
        settings = {
            "design_chainage": str(PENSTOCK_DESIGN_CHAINAGE),
            "target_date": PENSTOCK_TARGET_DATE.isoformat(),
            "mass_concrete_m3": str(PENSTOCK_MASS_CONCRETE_M3),
            "temporary_m3": str(PENSTOCK_TEMPORARY_M3),
            "final_m3": str(PENSTOCK_FINAL_M3),
            "sleepers_m3": str(PENSTOCK_SLEEPERS_M3),
        }
        con.executemany(
            "INSERT OR IGNORE INTO penstock_settings(setting_key,setting_value) VALUES(?,?)",
            list(settings.items()),
        )

        components = []
        ordered = [
            ("Final Penstock TEE 2", "Fitting", 0.0),
            ("1.3m Pipe", "Pipe", 1.3),
            ("Final Penstock TEE 1", "Fitting", 0.0),
            ("6.3m Pipe", "Pipe", 6.258),
        ]
        ordered.extend((f"9.1m Pipe {number}", "Pipe", 9.144) for number in range(25,18,-1))
        ordered.append(("Temporary Penstock TEE", "Fitting", 0.0))
        ordered.extend((f"9.1m Pipe {number}", "Pipe", 9.144) for number in range(18,8,-1))
        ordered.extend([
            ("119 deg bend", "Fitting", 0.0),
            ("3.3m Pipe", "Pipe", 3.36),
        ])
        ordered.extend((f"9.1m Pipe {number}", "Pipe", 9.144) for number in range(8,0,-1))
        ordered.append(("5m Pipe", "Pipe", 5.0))

        chainage = 0.0
        legacy_installed = {"119 deg bend", "3.3m Pipe", "9.1m Pipe 1", "9.1m Pipe 2", "9.1m Pipe 3"}
        for sequence_no, (name, component_type, nominal) in enumerate(ordered, start=1):
            used_length = max(0.0, min(nominal, PENSTOCK_DESIGN_CHAINAGE-chainage))
            start_ch = chainage
            end_ch = chainage + used_length
            component_id = f"PEN-{sequence_no:02d}"
            installed = 1 if name in legacy_installed else 0
            components.append((
                component_id, sequence_no, name, component_type, nominal,
                used_length, start_ch, end_ch, installed,
                "", "", "Recorded" if installed else "Pending", "Pending", ""
            ))
            chainage = end_ch
        con.executemany(
            """INSERT OR IGNORE INTO penstock_components(
                   component_id,sequence_no,component_name,component_type,
                   nominal_length,chainage_length,start_chainage,end_chainage,
                   installed,actual_date,rn,qc_status,asbuilt_status,notes
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            components,
        )

        casts = []
        for index in range(21):
            start_ch = index * 12.0
            end_ch = min((index + 1) * 12.0, PENSTOCK_DESIGN_CHAINAGE)
            quantity = (end_ch-start_ch) * 1.0 * 0.75
            casts.append((
                f"ENC-{index+1:02d}", index+1, "Mass Encasement",
                start_ch, end_ch, quantity, 15.0, 0.0, "", "",
                "2 x 6 m casting cycle" if index < 20 else "Final balance cast",
            ))
        casts.extend([
            ("TEMP-01", 22, "Temporary Penstock", None, None, 3.12, 35.0, 0.0, "", "", "1.3 x 1.2 x 2 m"),
            ("FINAL-01", 23, "Final Penstock", None, None, 6.24, 35.0, 0.0, "", "", "1.3 x 1.2 x 4 m"),
        ])
        for batch in range(1,9):
            count = 10 if batch <= 7 else 2
            actual_count = 10 if batch == 1 else (8 if batch == 2 else 0)
            casts.append((
                f"SLP-{batch:02d}", 23+batch, "Sleepers", None, None,
                count*0.2*0.2*1.0, 35.0,
                actual_count*0.2*0.2*1.0, "", "", f"{count} sleeper batch",
            ))
        con.executemany(
            """INSERT OR IGNORE INTO penstock_casts(
                   item_id,sequence_no,cast_type,start_chainage,end_chainage,
                   design_quantity,strength_mpa,actual_quantity,cast_date,rn,notes
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            casts,
        )

    def apply_daily_plan_rules(self):
        """
        Apply site minimum daily production plans from 06 Aug 2026 onward.

        Rules:
        * Waste Rock L/H >= 1,000 m3/day.
        * Waste Rock Process >= 1,000 m3/day.
        * Key Cut + RWD Cut to Fill (6 + 14): combined actual <= 3,100
          selects a 3,100 m3/day group target; above 3,100 selects 3,500.
          The selected target is split proportionally between Activities 6 and 14.
        * Processing trigger (7 + 12 + 13): combined actual <= 2,100 selects
          a 2,100 m3/day group target; above 2,100 selects 2,800. The selected
          target is split proportionally between Activities 7 and 13 only.
          Activity 12 contributes to the actual trigger but keeps its own plan.
          Activity 10 is excluded from this production-target logic entirely.

        The Excel values are retained in source_planned_* so this method can
        safely run after every import and at every application start.
        """
        effective = "2026-08-06"

        with self.connect() as con:
            # Always restore the post-effective records to the source programme
            # before applying the rules. This prevents repeated inflation.
            con.execute(
                """UPDATE daily_records
                   SET planned_daily=source_planned_daily,
                       planned_cumulative=source_planned_cumulative
                   WHERE report_date>=?""",
                (effective,),
            )

            rows = con.execute(
                """SELECT d.id,d.task_id,d.report_date,d.actual_daily,d.source_planned_daily,
                          d.source_planned_cumulative,t.management_activity_no,
                          w.period_order,w.file_name
                   FROM daily_records d
                   JOIN tasks t ON t.id=d.task_id
                   JOIN workbooks w ON w.id=t.workbook_id
                   WHERE t.management_activity_no IS NOT NULL
                   ORDER BY d.report_date,w.period_order,d.id"""
            ).fetchall()

            # Work only with the latest owning period for each date. The main
            # and waste-rock workbooks within that period remain valid together.
            owner_by_date = {}
            for row in rows:
                owner_by_date[row["report_date"]] = max(
                    owner_by_date.get(row["report_date"], -1),
                    int(row["period_order"]),
                )

            active = [
                row for row in rows
                if int(row["period_order"]) == owner_by_date[row["report_date"]]
            ]
            by_date = {}
            for row in active:
                by_date.setdefault(row["report_date"], []).append(row)

            daily_adjustments = {}
            last_group_ratios = {}

            def source_qty(row):
                value = row["source_planned_daily"]
                return float(value) if value is not None else 0.0

            def set_qty(row, value):
                value = float(value)
                con.execute(
                    "UPDATE daily_records SET planned_daily=? WHERE id=?",
                    (value, row["id"]),
                )
                daily_adjustments[row["id"]] = value - source_qty(row)

            for report_date in sorted(by_date):
                date_rows = by_date[report_date]
                by_activity = {}
                for row in date_rows:
                    activity = int(row["management_activity_no"])
                    by_activity.setdefault(activity, []).append(row)

                is_effective = report_date >= effective
                is_sunday = datetime.strptime(report_date, "%Y-%m-%d").weekday() == 6

                # Sunday is a non-production day. It overrides every adjusted
                # production-target rule below. Set all activities handled by
                # this planner to zero, then skip the rest of the target logic
                # for this date.
                if is_effective and is_sunday:
                    for row in date_rows:
                        set_qty(row, 0.0)
                    continue

                # Individual Waste Rock minimums. There should normally be one
                # record per activity; update each matching owner-period row.
                if is_effective:
                    for activity in (15, 16):
                        for row in by_activity.get(activity, []):
                            actual = row["actual_daily"]
                            actual = float(actual) if actual is not None else 0.0
                            
                            if actual <= 1000.0:
                                set_qty(row, 1000.0)
                            else:
                                set_qty(row, 1400.0)

                # Actual-driven grouped targets. Trigger activities determine
                # whether the low or high target applies. The selected target is
                # then distributed only across target_activities using the source
                # programme proportions.
                #
                # Processing: 7 + 12 + 13 determine the target, but only 7 + 13
                # receive the 2,100/2,800 allocation. Activity 10 is excluded.
                actual_target_rules = {
                    (6, 14): {
                        "target_activities": (6, 14),
                        "threshold": 3100.0,
                        "low_target": 3100.0,
                        "high_target": 3500.0,
                    },
                    (7, 12, 13): {
                        "target_activities": (7, 13),
                        "threshold": 2100.0,
                        "low_target": 2100.0,
                        "high_target": 2800.0,
                    },
                }

                for trigger_activities, cfg in actual_target_rules.items():
                    trigger_rows = [
                        row
                        for activity in trigger_activities
                        for row in by_activity.get(activity, [])
                    ]

                    if not trigger_rows:
                        continue

                    combined_actual = sum(
                        float(row["actual_daily"] or 0.0)
                        for row in trigger_rows
                    )

                    target = (
                        cfg["low_target"]
                        if combined_actual <= cfg["threshold"]
                        else cfg["high_target"]
                    )

                    target_rows = [
                        row
                        for activity in cfg["target_activities"]
                        for row in by_activity.get(activity, [])
                    ]

                    if not target_rows:
                        continue

                    original_total = sum(source_qty(row) for row in target_rows)
                    group_key = tuple(cfg["target_activities"])

                    if original_total > 0.0:
                        ratios = {
                            int(row["management_activity_no"]):
                                source_qty(row) / original_total
                            for row in target_rows
                        }
                        last_group_ratios[group_key] = ratios
                    else:
                        ratios = last_group_ratios.get(group_key)

                    if is_effective and ratios:
                        for row in target_rows:
                            activity = int(row["management_activity_no"])
                            set_qty(row, target * ratios.get(activity, 0.0))

            # Explicit no-work dates override every normal planning rule. These
            # dates are stored separately from the imported Excel programme so
            # they survive synchronisation and can be safely restored later.
            no_work_dates = {
                row["report_date"]
                for row in con.execute(
                    "SELECT report_date FROM production_no_work_dates"
                ).fetchall()
            }
            for report_date in sorted(no_work_dates):
                for row in by_date.get(report_date, []):
                    set_qty(row, 0.0)

            # Keep each activity's planned cumulative curve consistent with the
            # adjusted daily plan. The adjustment carries forward by management
            # activity across monthly workbooks, while the source cumulative
            # remains untouched for audit purposes.
            running_delta = {}
            for row in active:
                if row["report_date"] < effective:
                    continue
                activity = int(row["management_activity_no"])
                delta = daily_adjustments.get(row["id"], 0.0)
                running_delta[activity] = running_delta.get(activity, 0.0) + delta
                source_cum = row["source_planned_cumulative"]
                if source_cum is not None:
                    con.execute(
                        "UPDATE daily_records SET planned_cumulative=? WHERE id=?",
                        (float(source_cum) + running_delta[activity], row["id"]),
                    )

    def set_no_work_range(self, start_date, end_date, reason="No work"):
        """Persist a date range whose Daily Production planned quantities are zero."""
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("End date cannot be before start date.")

        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as con:
            current = start
            while current <= end:
                con.execute(
                    """INSERT INTO production_no_work_dates(
                           report_date,reason,updated_at
                       ) VALUES(?,?,?)
                       ON CONFLICT(report_date) DO UPDATE SET
                           reason=excluded.reason,
                           updated_at=excluded.updated_at""",
                    (current.isoformat(), reason or "No work", now),
                )
                current += timedelta(days=1)

        self.apply_daily_plan_rules()

    def clear_no_work_range(self, start_date, end_date):
        """Remove no-work overrides and rebuild the normal calculated programme."""
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("End date cannot be before start date.")

        with self.connect() as con:
            con.execute(
                """DELETE FROM production_no_work_dates
                   WHERE report_date BETWEEN ? AND ?""",
                (start.isoformat(), end.isoformat()),
            )

        self.apply_daily_plan_rules()

    def no_work_dates(self):
        with self.connect() as con:
            return {
                row["report_date"]: row["reason"]
                for row in con.execute(
                    """SELECT report_date,reason
                       FROM production_no_work_dates
                       ORDER BY report_date"""
                ).fetchall()
            }

    def is_current(self, path):
        with self.connect() as con:
            row = con.execute(
                "SELECT modified_time FROM workbooks WHERE file_path=?",
                (str(path),)
            ).fetchone()
        return bool(row and abs(row["modified_time"] - path.stat().st_mtime) < 0.001)

    def import_workbook(self, period_order, period_name, path):
        """Synchronise one costing workbook into SQLite without changing IDs.

        Earlier builds deleted the workbooks row and inserted it again whenever
        the Excel modified time changed.  If foreign-key cascades were not active
        on that particular SQLite connection, the old tasks survived and the new
        INSERT then collided with UNIQUE(workbook_id, prog_sheet).

        This version performs an in-place resynchronisation:
          1. UPSERT the workbook metadata by file_path.
          2. UPDATE an existing task identified by (workbook_id, prog_sheet), or
             INSERT it only when the sheet is genuinely new.
          3. UPSERT each daily row by (task_id, excel_row).

        Existing workbook/task IDs therefore remain stable, preserving all links
        used elsewhere in the Production Manager.
        """
        now = datetime.now().isoformat(timespec="seconds")
        try:
            wb = load_workbook(path, data_only=True, read_only=True)
            if "TASK LIST" not in wb.sheetnames:
                return False, "No TASK LIST tab"

            task_list = read_task_list(wb["TASK LIST"])
            period_end = infer_period_end(period_name, path.name, wb["TASK LIST"])
            prog_sheets = [s for s in wb.sheetnames if "PROG" in s.upper()]
            if not prog_sheets:
                return False, "No TASK ... PROG tabs"

            with self.connect() as con:
                # Keep the existing workbook ID.  This is critical because tasks
                # and daily records already reference it.
                existing = con.execute(
                    "SELECT id FROM workbooks WHERE file_path=?", (str(path),)
                ).fetchone()

                if existing:
                    workbook_id = int(existing["id"])
                    con.execute(
                        """UPDATE workbooks
                           SET period_order=?, period_name=?, file_name=?,
                               modified_time=?, imported_at=?
                           WHERE id=?""",
                        (period_order, period_name, path.name,
                         path.stat().st_mtime, now, workbook_id)
                    )
                else:
                    cur = con.execute(
                        """INSERT INTO workbooks(
                               period_order,period_name,file_name,file_path,
                               modified_time,imported_at)
                           VALUES(?,?,?,?,?,?)""",
                        (period_order, period_name, path.name, str(path),
                         path.stat().st_mtime, now)
                    )
                    workbook_id = int(cur.lastrowid)

                task_count = record_count = 0

                for sheet_name in prog_sheets:
                    code = task_code_from_sheet(sheet_name)
                    if not code or code not in task_list:
                        continue
                    meta = task_list[code]
                    major, minor = task_parts(code)

                    management_no, management_name = management_activity(
                        code, meta["description"], sheet_name, path.name
                    )

                    is_waste_workbook = path.name.lower().startswith(
                        "inrep36600 phase 3 - daily costing waste rock"
                    )
                    # The Waste Rock workbook contains Activities 15/16 plus
                    # the Penstock package (TASK 3 through TASK 10). Ignore only
                    # blank/unconfigured future task rows.
                    if is_waste_workbook and management_no is None:
                        continue

                    # Reuse the existing task ID for this workbook/sheet.
                    existing_task = con.execute(
                        """SELECT id FROM tasks
                           WHERE workbook_id=? AND prog_sheet=?""",
                        (workbook_id, sheet_name)
                    ).fetchone()

                    if existing_task:
                        task_id = int(existing_task["id"])
                        con.execute(
                            """UPDATE tasks SET
                                   task_code=?, task_major=?, task_minor=?,
                                   task_description=?, section=?, unit=?, allowable=?,
                                   management_activity_no=?, management_activity_name=?
                               WHERE id=?""",
                            (code, major, minor, meta["description"],
                             meta["section"], meta["unit"], meta["allowable"],
                             management_no, management_name, task_id)
                        )
                    else:
                        cur = con.execute(
                            """INSERT INTO tasks(
                                   workbook_id,task_code,task_major,task_minor,
                                   task_description,section,unit,allowable,prog_sheet,
                                   management_activity_no,management_activity_name)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (workbook_id, code, major, minor, meta["description"],
                             meta["section"], meta["unit"], meta["allowable"],
                             sheet_name, management_no, management_name)
                        )
                        task_id = int(cur.lastrowid)

                    task_count += 1
                    con.execute(
                        "INSERT OR IGNORE INTO task_metadata(task_description) VALUES(?)",
                        (meta["description"],)
                    )

                    # Default responsibility from the site allocation.
                    # Preserve any manually edited non-empty foreman already in
                    # the database; only seed blank metadata.
                    if is_waste_workbook:
                        default_foreman = None
                        if management_no in {15, 16}:
                            default_foreman = "Promise Seerane"
                        elif management_no in {25, 26}:
                            default_foreman = "Martin Lehihi"
                        elif management_no in range(18, 28):
                            default_foreman = "Phillip Masha"

                        if default_foreman:
                            con.execute(
                                """UPDATE task_metadata
                                   SET responsible_foreman=?
                                   WHERE task_description=?
                                     AND TRIM(COALESCE(responsible_foreman,''))=''""",
                                (default_foreman, meta["description"])
                            )

                    ws = wb[sheet_name]
                    imported_excel_rows = []
                    for row in range(16, 47):
                        report_date = resolve_day(ws[f"A{row}"].value, period_end)
                        if not report_date:
                            continue

                        values = {}
                        for field, col in PROG_COLUMNS.items():
                            value = ws[f"{col}{row}"].value
                            values[field] = (
                                float(value) if isinstance(value, (int, float)) else None
                            )

                        imported_excel_rows.append(row)
                        con.execute(
                            """INSERT INTO daily_records(
                                   task_id,report_date,excel_row,daily_profit_loss,
                                   cumulative_profit_loss,actual_daily,actual_cumulative,
                                   planned_daily,planned_cumulative,
                                   source_planned_daily,source_planned_cumulative)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(task_id,excel_row) DO UPDATE SET
                                   report_date=excluded.report_date,
                                   daily_profit_loss=excluded.daily_profit_loss,
                                   cumulative_profit_loss=excluded.cumulative_profit_loss,
                                   actual_daily=excluded.actual_daily,
                                   actual_cumulative=excluded.actual_cumulative,
                                   planned_daily=excluded.planned_daily,
                                   planned_cumulative=excluded.planned_cumulative,
                                   source_planned_daily=excluded.source_planned_daily,
                                   source_planned_cumulative=excluded.source_planned_cumulative""",
                            (task_id, report_date.isoformat(), row,
                             values["daily_profit_loss"], values["cumulative_profit_loss"],
                             values["actual_daily"], values["actual_cumulative"],
                             values["planned_daily"], values["planned_cumulative"],
                             values["planned_daily"], values["planned_cumulative"])
                        )
                        record_count += 1

                    # If a date was cleared from rows 16:46 in Excel, remove only
                    # that stale imported row.  The task itself and its ID remain.
                    if imported_excel_rows:
                        placeholders = ",".join("?" for _ in imported_excel_rows)
                        con.execute(
                            f"""DELETE FROM daily_records
                                WHERE task_id=? AND excel_row BETWEEN 16 AND 46
                                  AND excel_row NOT IN ({placeholders})""",
                            (task_id, *imported_excel_rows)
                        )
                    else:
                        con.execute(
                            "DELETE FROM daily_records WHERE task_id=? AND excel_row BETWEEN 16 AND 46",
                            (task_id,)
                        )

                if task_count == 0:
                    raise ValueError("No TASK LIST entries matched TASK ... PROG tabs")

                con.execute(
                    "INSERT INTO import_log(imported_at,file_path,status,message) VALUES(?,?,?,?)",
                    (now, str(path), "imported",
                     f"{task_count} tasks; {record_count} records; cost source H/I; safe resync")
                )

            self.apply_daily_plan_rules()
            return True, f"{task_count} tasks; {record_count} records"

        except Exception as exc:
            with self.connect() as con:
                con.execute(
                    "INSERT INTO import_log(imported_at,file_path,status,message) VALUES(?,?,?,?)",
                    (now, str(path), "failed", str(exc))
                )
            return False, str(exc)

    @staticmethod
    def office_rate_per_effective_hour():
        # Existing office complement:
        # 1 x 69.17, 7 x 54.06, 1 x 86.89, 2 x 90.00, 1 x 80.00
        return 69.17 + (7 * 54.06) + 86.89 + (2 * 90.00) + 80.00

    @staticmethod
    def default_office_hours(report_date):
        day = datetime.strptime(report_date, "%Y-%m-%d").date().weekday()
        if day == 5:  # Saturday
            return 7.0, 1.5
        if day == 6:  # Sunday
            return 0.0, 2.0
        return 10.0, 1.0

    def get_office_labour(self, report_date):
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM office_labour WHERE report_date=?",
                (report_date,)
            ).fetchone()
        if row:
            return dict(row)

        actual_hours, factor = self.default_office_hours(report_date)
        effective = actual_hours * factor
        cost = -(self.office_rate_per_effective_hour() * effective)
        weekday = datetime.strptime(report_date, "%Y-%m-%d").strftime("%A")
        return {
            "report_date": report_date,
            "use_default": 1,
            "actual_hours": actual_hours,
            "overtime_factor": factor,
            "effective_hours": effective,
            "daily_cost": cost,
            "day_type": weekday,
            "notes": "",
            "saved": False,
        }

    def save_office_labour(self, report_date, use_default, actual_hours, overtime_factor, day_type, notes):
        if use_default:
            actual_hours, overtime_factor = self.default_office_hours(report_date)
        effective = float(actual_hours) * float(overtime_factor)
        cost = -(self.office_rate_per_effective_hour() * effective)
        with self.connect() as con:
            con.execute(
                """INSERT INTO office_labour(
                    report_date,use_default,actual_hours,overtime_factor,effective_hours,daily_cost,
                    day_type,notes,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(report_date) DO UPDATE SET
                    use_default=excluded.use_default,
                    actual_hours=excluded.actual_hours,
                    overtime_factor=excluded.overtime_factor,
                    effective_hours=excluded.effective_hours,
                    daily_cost=excluded.daily_cost,
                    day_type=excluded.day_type,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (report_date,1 if use_default else 0,float(actual_hours),float(overtime_factor),
                 effective,cost,day_type,notes,datetime.now().isoformat(timespec="seconds"))
            )
        return cost

    def save_activity_comment(self, report_date, activity_no, comment):
        with self.connect() as con:
            con.execute(
                """INSERT INTO activity_comments(report_date,management_activity_no,comment,updated_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(report_date,management_activity_no) DO UPDATE SET
                     comment=excluded.comment,updated_at=excluded.updated_at""",
                (report_date,int(activity_no),comment,datetime.now().isoformat(timespec="seconds"))
            )

    def activity_comment(self, report_date, activity_no):
        with self.connect() as con:
            row=con.execute(
                "SELECT comment FROM activity_comments WHERE report_date=? AND management_activity_no=?",
                (report_date,int(activity_no))
            ).fetchone()
        return row["comment"] if row else ""


    def activity_foreman(self, activity_no):
        """Return the saved foreman for a management activity."""
        with self.connect() as con:
            row = con.execute(
                """SELECT COALESCE(m.responsible_foreman,'') AS responsible_foreman
                   FROM tasks t
                   LEFT JOIN task_metadata m
                     ON m.task_description=t.task_description
                   WHERE t.management_activity_no=?
                   ORDER BY
                     CASE WHEN COALESCE(m.responsible_foreman,'')='' THEN 1 ELSE 0 END,
                     t.id
                   LIMIT 1""",
                (int(activity_no),)
            ).fetchone()
        return row["responsible_foreman"] if row else ""

    def save_activity_foreman(self, activity_no, foreman_name):
        """
        Save one foreman against every task description mapped to the
        selected management activity. This does not update or delete
        activity_comments.
        """
        with self.connect() as con:
            descriptions = con.execute(
                """SELECT DISTINCT task_description
                   FROM tasks
                   WHERE management_activity_no=?""",
                (int(activity_no),)
            ).fetchall()

            for row in descriptions:
                con.execute(
                    """INSERT INTO task_metadata(
                           task_description,responsible_foreman
                       ) VALUES(?,?)
                       ON CONFLICT(task_description) DO UPDATE SET
                           responsible_foreman=excluded.responsible_foreman""",
                    (row["task_description"], foreman_name)
                )

    TEAM_DEFAULT_TARGETS = {
        "Makanyane": 2800.0,
        "Silas": 3500.0,
        "Pleasure": 1400.0,
        "Moshe": 1400.0,
    }

    TEAM_ACTIVITY_NUMBERS = {
        "Makanyane": [7, 13],
        "Silas": [6, 14],
        "Pleasure": [15],
        "Moshe": [16],
    }

    # Programme minimum production requirements used as fixed visual
    # reference lines on the team daily production graphs.  These are
    # intentionally separate from the editable Champions League targets.
    TEAM_PROGRAMME_MINIMUMS = {
        "Pleasure": 1000.0,
        "Moshe": 1000.0,
        "Silas": 3100.0,
        "Makanyane": 2100.0,
    }

    @staticmethod
    def league_week_bounds(reference_date):
        if isinstance(reference_date, str):
            reference_date = datetime.strptime(reference_date, "%Y-%m-%d").date()
        days_since_thursday = (reference_date.weekday() - 3) % 7
        week_start = reference_date - timedelta(days=days_since_thursday)
        return week_start, week_start + timedelta(days=6)

    def save_target_override(self, team_name, target_date, target_quantity, reason):
        with self.connect() as con:
            con.execute(
                """INSERT INTO league_target_overrides(
                       team_name,target_date,target_quantity,reason,updated_at
                   ) VALUES(?,?,?,?,?)
                   ON CONFLICT(team_name,target_date) DO UPDATE SET
                       target_quantity=excluded.target_quantity,
                       reason=excluded.reason,
                       updated_at=excluded.updated_at""",
                (
                    team_name,
                    target_date,
                    float(target_quantity),
                    reason,
                    datetime.now().isoformat(timespec="seconds"),
                )
            )

    def clear_target_override(self, team_name, target_date):
        with self.connect() as con:
            con.execute(
                "DELETE FROM league_target_overrides WHERE team_name=? AND target_date=?",
                (team_name, target_date)
            )

    def target_for_day(self, team_name, target_date):
        with self.connect() as con:
            row = con.execute(
                """SELECT target_quantity,reason
                   FROM league_target_overrides
                   WHERE team_name=? AND target_date=?""",
                (team_name, target_date)
            ).fetchone()
        if row:
            return float(row["target_quantity"]), row["reason"], True

        target_day = datetime.strptime(target_date, "%Y-%m-%d").date()

        # Standard target days are Thu, Fri, Sat, Mon, Tue and Wed.
        # Sunday carries zero target unless explicitly edited.
        if target_day.weekday() == 6:
            return 0.0, "Default Sunday rest day", False

        # Makanyane's standard target changed from 2,700 to 2,800 m3/day
        # effective 06 Aug 2026. Preserve historical league calculations.
        if team_name == "Makanyane" and target_day < date(2026, 8, 6):
            return 2700.0, "", False

        return float(self.TEAM_DEFAULT_TARGETS[team_name]), "", False

    def owning_period_order(self, report_date, period_name):
        with self.connect() as con:
            if period_name != "All periods":
                row = con.execute(
                    """SELECT MAX(w.period_order) AS period_order
                       FROM daily_records d
                       JOIN tasks t ON t.id=d.task_id
                       JOIN workbooks w ON w.id=t.workbook_id
                       WHERE d.report_date=? AND w.period_name=?""",
                    (report_date, period_name)
                ).fetchone()
            else:
                row = con.execute(
                    """SELECT MAX(w.period_order) AS period_order
                       FROM daily_records d
                       JOIN tasks t ON t.id=d.task_id
                       JOIN workbooks w ON w.id=t.workbook_id
                       WHERE d.report_date=?""",
                    (report_date,)
                ).fetchone()
        return row["period_order"] if row else None

    def activity_actual_for_day(self, activity_no, report_date, period_name):
        """
        Return one management activity's actual daily production.

        Activities 15 and 16 are read only from:
        INREP36600 Phase 3 - Daily Costing Waste rock <period>
        TASK 1 PROG and TASK 2 PROG respectively.
        """
        owner_order = self.owning_period_order(report_date, period_name)
        if owner_order is None:
            return 0.0

        sql = """SELECT SUM(COALESCE(d.actual_daily,0)) AS actual
                 FROM daily_records d
                 JOIN tasks t ON t.id=d.task_id
                 JOIN workbooks w ON w.id=t.workbook_id
                 WHERE d.report_date=?
                   AND w.period_order=?
                   AND t.management_activity_no=?"""
        params = [report_date, owner_order, int(activity_no)]

        if int(activity_no) in (15, 16):
            sql += """ AND LOWER(w.file_name) LIKE
                       'inrep36600 phase 3 - daily costing waste rock%'"""
            sql += " AND t.task_code=?"
            params.append("TASK 1" if int(activity_no) == 15 else "TASK 2")
        else:
            sql += """ AND LOWER(w.file_name) NOT LIKE
                       'inrep36600 phase 3 - daily costing waste rock%'"""

        with self.connect() as con:
            row = con.execute(sql, params).fetchone()
        return float(row["actual"] or 0)

    def team_actual_for_day(self, team_name, report_date, period_name):
        return sum(
            self.activity_actual_for_day(activity_no, report_date, period_name)
            for activity_no in self.TEAM_ACTIVITY_NUMBERS[team_name]
        )

    def league_daily_records(self, start_date, end_date, period_name):
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        rows = []
        current = start
        while current <= end:
            day_text = current.isoformat()
            for team_name in self.TEAM_DEFAULT_TARGETS:
                target, reason, edited = self.target_for_day(team_name, day_text)
                actual = self.team_actual_for_day(team_name, day_text, period_name)
                rows.append({
                    "Date": current,
                    "Team": team_name,
                    "Target": target,
                    "Actual": actual,
                    "Variance": actual - target,
                    "Target Edited": edited,
                    "Reason": reason,
                })
            current += timedelta(days=1)
        return pd.DataFrame(rows)

    def _week_team_percentage(
        self,
        team_name,
        week_start,
        week_end,
        status_date,
        period_name,
    ):
        daily = self.league_daily_records(
            week_start.isoformat(), week_end.isoformat(), period_name
        )
        group = daily[daily["Team"] == team_name].copy()

        full_week_target = float(group["Target"].sum())
        actual_to_status = float(
            group[group["Date"] <= status_date]["Actual"].sum()
        )
        percentage = (
            actual_to_status / full_week_target * 100.0
            if full_week_target else 0.0
        )
        return full_week_target, actual_to_status, percentage

    def league_standings(self, reference_date, period_name):
        status_date = (
            datetime.strptime(reference_date, "%Y-%m-%d").date()
            if isinstance(reference_date, str)
            else reference_date
        )
        week_start, week_end = self.league_week_bounds(status_date)

        daily = self.league_daily_records(
            week_start.isoformat(), week_end.isoformat(), period_name
        )

        previous_start = week_start - timedelta(days=7)
        previous_end = week_end - timedelta(days=7)

        rows = []
        for team_name in self.TEAM_DEFAULT_TARGETS:
            plan, actual_to_status, percentage = self._week_team_percentage(
                team_name, week_start, week_end, status_date, period_name
            )

            previous_status = status_date - timedelta(days=7)
            _prev_plan, _prev_actual, previous_percentage = (
                self._week_team_percentage(
                    team_name,
                    previous_start,
                    previous_end,
                    previous_status,
                    period_name,
                )
            )
            gd = percentage - previous_percentage

            rows.append({
                "Team": team_name,
                "Plan": plan,
                "Actual": actual_to_status,
                "GD": gd,
                "PTS": percentage,
                "Status Date": status_date,
                "Incentive": (
                    "QUALIFIED" if percentage >= 100 else "NOT QUALIFIED"
                ),
            })

        standings = (
            pd.DataFrame(rows)
            .sort_values(["PTS", "Actual"], ascending=[False, False])
            .reset_index(drop=True)
        )
        standings.insert(0, "No", range(1, len(standings) + 1))
        return standings, daily, week_start, week_end

    def team_graph_data(self, team_name, start_date, end_date, period_name):
        daily = self.league_daily_records(start_date, end_date, period_name)
        team = daily[daily["Team"] == team_name].copy().sort_values("Date")
        team["Cumulative Target"] = team["Target"].cumsum()
        team["Cumulative Actual"] = team["Actual"].cumsum()
        team["Achievement"] = (
            team["Cumulative Actual"] / team["Cumulative Target"] * 100.0
        ).fillna(0)
        return team

    def periods(self):
        with self.connect() as con:
            rows = con.execute(
                "SELECT DISTINCT period_order,period_name FROM workbooks ORDER BY period_order"
            ).fetchall()
        return [r["period_name"] for r in rows]

    def dates(self, period_name):
        sql = """SELECT DISTINCT d.report_date
                 FROM daily_records d
                 JOIN tasks t ON t.id=d.task_id
                 JOIN workbooks w ON w.id=t.workbook_id"""
        params = []
        if period_name != "All periods":
            sql += " WHERE w.period_name=?"
            params.append(period_name)
        sql += " ORDER BY d.report_date"
        with self.connect() as con:
            return [r[0] for r in con.execute(sql, params).fetchall()]

    def daily_summary(self, report_date, period_name):
        """Crossover-safe Daily Summary financial resolver."""
        sql = """
        SELECT
            w.period_order,w.period_name,w.file_name,
            t.management_activity_no,t.management_activity_name,
            t.task_code,t.task_description,
            COALESCE(m.responsible_foreman,'') AS responsible_foreman,
            COALESCE(c.comment,'') AS comments,
            d.daily_profit_loss,d.cumulative_profit_loss
        FROM daily_records d
        JOIN tasks t ON t.id=d.task_id
        JOIN workbooks w ON w.id=t.workbook_id
        LEFT JOIN task_metadata m ON m.task_description=t.task_description
        LEFT JOIN activity_comments c ON c.report_date=d.report_date
             AND c.management_activity_no=t.management_activity_no
        WHERE d.report_date=?
          AND t.management_activity_no IS NOT NULL
        """
        params = [report_date]
        if period_name != "All periods" and period_name != "__ROLLFORWARD_INTERNAL__":
            sql += " AND w.period_name=?"
            params.append(period_name)

        with self.connect() as con:
            df = pd.read_sql_query(sql, con, params=params)

        if df.empty:
            return df

        # Resolve overlapping monthly workbook rows independently per activity.
        df["_score"] = (
            df["daily_profit_loss"].notna().astype(int)
            + df["cumulative_profit_loss"].notna().astype(int)
            + (df["daily_profit_loss"].fillna(0) != 0).astype(int)
            + (df["cumulative_profit_loss"].fillna(0) != 0).astype(int)
        )
        df = (
            df.sort_values(
                ["management_activity_no", "_score", "period_order", "file_name"],
                ascending=[True, False, False, True]
            )
            .drop_duplicates("management_activity_no", keep="first")
            .sort_values("management_activity_no")
            .drop(columns=["_score"])
        )

        # Do not recurse while calculating the roll-forward movements.
        if period_name == "__ROLLFORWARD_INTERNAL__":
            return df

        self._daily_summary_closing_override = None

        # Confirmed site reference:
        # 14 Sep 2026 Daily P/L = -R42,401.07
        # 14 Sep 2026 closing Total-to-date = -R358,083.70
        if period_name == "All periods" and report_date >= "2026-09-14":
            anchor_date = "2026-09-14"
            anchor_closing = -358083.70

            if report_date == anchor_date:
                closing = anchor_closing
            else:
                with self.connect() as con:
                    dates = [
                        r["report_date"] for r in con.execute(
                            """SELECT DISTINCT d.report_date
                               FROM daily_records d
                               JOIN tasks t ON t.id=d.task_id
                               WHERE t.management_activity_no IS NOT NULL
                                 AND d.report_date>? AND d.report_date<=?
                               ORDER BY d.report_date""",
                            (anchor_date, report_date)
                        ).fetchall()
                    ]

                movement = 0.0
                for day in dates:
                    day_df = self.daily_summary(day, "__ROLLFORWARD_INTERNAL__")
                    if not day_df.empty:
                        movement += float(
                            pd.to_numeric(
                                day_df["daily_profit_loss"], errors="coerce"
                            ).fillna(0.0).sum()
                        )
                    office = self.get_office_labour(day)
                    movement += float(office.get("daily_cost", 0.0) or 0.0)

                closing = anchor_closing + movement

            self._daily_summary_closing_override = float(closing)

        return df

    def activities(self, period_name):
        sql = """
        SELECT DISTINCT
            COALESCE(t.management_activity_name,t.task_description) AS task_description,
            t.task_code,t.task_major,t.task_minor,
            t.management_activity_no,
            w.period_order,w.period_name
        FROM tasks t JOIN workbooks w ON w.id=t.workbook_id
        WHERE t.management_activity_no IS NOT NULL
        """
        params = []
        if period_name != "All periods":
            sql += " AND w.period_name=?"
            params.append(period_name)
        sql += " ORDER BY w.period_order,t.management_activity_no,t.task_major,t.task_minor"
        with self.connect() as con:
            return con.execute(sql, params).fetchall()

    def activity_history(self, task_description, period_name):
        with self.connect() as con:
            activity_row = con.execute(
                """SELECT management_activity_no
                   FROM tasks
                   WHERE COALESCE(management_activity_name,task_description)=?
                     AND management_activity_no IS NOT NULL
                   ORDER BY management_activity_no
                   LIMIT 1""",
                (task_description,)
            ).fetchone()

        if not activity_row:
            return pd.DataFrame()

        activity_no = int(activity_row["management_activity_no"])
        sql = """
        SELECT
            d.report_date,w.period_order,w.period_name,w.file_name,
            t.task_code,
            COALESCE(t.management_activity_name,t.task_description) AS task_description,
            t.unit,d.planned_daily,d.actual_daily,
            d.planned_cumulative,d.actual_cumulative,
            d.daily_profit_loss,d.cumulative_profit_loss
        FROM daily_records d
        JOIN tasks t ON t.id=d.task_id
        JOIN workbooks w ON w.id=t.workbook_id
        WHERE t.management_activity_no=?
        """
        params = [activity_no]
        if period_name != "All periods":
            sql += " AND w.period_name=?"
            params.append(period_name)

        if activity_no in (15, 16):
            sql += """ AND LOWER(w.file_name) LIKE
                       'inrep36600 phase 3 - daily costing waste rock%'"""
            sql += " AND t.task_code=?"
            params.append("TASK 1" if activity_no == 15 else "TASK 2")
        else:
            sql += """ AND LOWER(w.file_name) NOT LIKE
                       'inrep36600 phase 3 - daily costing waste rock%'"""

        sql += " ORDER BY d.report_date,w.period_order"
        with self.connect() as con:
            df = pd.read_sql_query(
                sql, con, params=params, parse_dates=["report_date"]
            )

        if not df.empty:
            df = (
                df.sort_values(["report_date","period_order"])
                .drop_duplicates("report_date", keep="last")
            )

            # v3.9.24: Daily Production must use the same authoritative
            # date/period resolver as Team Champions League.  This prevents
            # overlapping monthly costing workbooks from leaving the activity
            # chart on a stale/zero duplicate while the league reads the
            # latest owning period.
            authoritative_actuals = []
            for report_dt in df["report_date"]:
                day_text = pd.Timestamp(report_dt).date().isoformat()
                authoritative_actuals.append(
                    self.activity_actual_for_day(
                        activity_no,
                        day_text,
                        period_name,
                    )
                )

            df["actual_daily"] = authoritative_actuals

            # Rebuild the displayed cumulative actual from the same resolved
            # daily values, so Daily Quantity and Total to Date cannot disagree
            # with one another after a period overlap.
            df = df.sort_values("report_date").copy()
            df["actual_cumulative"] = (
                pd.to_numeric(df["actual_daily"], errors="coerce")
                .fillna(0.0)
                .cumsum()
            )

        return df

    def timeline(self, period_name):
        sql = """
        SELECT
            d.report_date AS Date,w.period_name AS Period,
            t.task_code AS Task_Code,COALESCE(t.management_activity_name,t.task_description) AS Activity,
            t.unit AS Unit,d.planned_daily AS Planned_Daily,
            d.actual_daily AS Actual_Daily,
            d.planned_cumulative AS Planned_Total,
            d.actual_cumulative AS Actual_Total,
            d.daily_profit_loss AS Daily_PL,
            d.cumulative_profit_loss AS Cumulative_PL
        FROM daily_records d
        JOIN tasks t ON t.id=d.task_id
        JOIN workbooks w ON w.id=t.workbook_id
        """
        params = []
        if period_name != "All periods":
            sql += " WHERE w.period_name=?"
            params.append(period_name)
        sql += " ORDER BY d.report_date,w.period_order,t.task_major,t.task_minor"
        with self.connect() as con:
            return pd.read_sql_query(sql, con, params=params, parse_dates=["Date"])

    def penstock_setting(self, key, default=""):
        with self.connect() as con:
            row = con.execute(
                "SELECT setting_value FROM penstock_settings WHERE setting_key=?",
                (key,),
            ).fetchone()
        return default if row is None else row["setting_value"]

    def save_penstock_setting(self, key, value):
        with self.connect() as con:
            con.execute(
                """INSERT INTO penstock_settings(setting_key,setting_value)
                   VALUES(?,?)
                   ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value""",
                (key, str(value)),
            )

    def penstock_components(self):
        with self.connect() as con:
            return pd.read_sql_query(
                "SELECT * FROM penstock_components ORDER BY sequence_no", con
            )

    def save_penstock_component(self, component_id, installed, actual_date, rn, qc_status, asbuilt_status):
        with self.connect() as con:
            con.execute(
                """UPDATE penstock_components
                   SET installed=?,actual_date=?,rn=?,qc_status=?,asbuilt_status=?
                   WHERE component_id=?""",
                (
                    1 if installed else 0, actual_date.strip(), rn.strip(),
                    qc_status.strip(), asbuilt_status.strip(), component_id,
                ),
            )

    def penstock_casts(self):
        with self.connect() as con:
            return pd.read_sql_query(
                """SELECT *,MAX(design_quantity-actual_quantity,0) AS remaining_quantity
                   FROM penstock_casts ORDER BY sequence_no""",
                con,
            )

    def save_penstock_cast(self, item_id, actual_quantity, cast_date, rn, notes):
        with self.connect() as con:
            row = con.execute(
                "SELECT design_quantity FROM penstock_casts WHERE item_id=?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown casting item: {item_id}")
            quantity = float(actual_quantity)
            if quantity < 0:
                raise ValueError("Actual concrete quantity cannot be negative.")
            con.execute(
                """UPDATE penstock_casts
                   SET actual_quantity=?,cast_date=?,rn=?,notes=?
                   WHERE item_id=?""",
                (quantity, cast_date.strip(), rn.strip(), notes.strip(), item_id),
            )

    def penstock_target_summary(self, status_date, target_date):
        casts = self.penstock_casts()
        design = float(casts["design_quantity"].sum()) if not casts.empty else 0.0
        actual = float(casts["actual_quantity"].sum()) if not casts.empty else 0.0
        remaining = max(design-actual, 0.0)
        working_days = 0
        cursor = status_date
        while cursor <= target_date:
            if cursor.weekday() != 6:
                working_days += 1
            cursor += timedelta(days=1)
        daily = remaining/working_days if working_days else remaining

        by_strength = {}
        if not casts.empty:
            for strength, group in casts.groupby("strength_mpa"):
                grade_design = float(group["design_quantity"].sum())
                grade_actual = float(group["actual_quantity"].sum())
                by_strength[float(strength)] = {
                    "design": grade_design,
                    "actual": grade_actual,
                    "remaining": max(grade_design-grade_actual, 0.0),
                    "daily": max(grade_design-grade_actual, 0.0)/working_days if working_days else max(grade_design-grade_actual, 0.0),
                }
        return {
            "design": design, "actual": actual, "remaining": remaining,
            "working_days": working_days, "daily": daily,
            "cycles_per_day": daily/PENSTOCK_NORMAL_CAST_M3,
            "by_strength": by_strength,
        }

    def penstock_activity_snapshot(self, activity_no, report_date):
        """Return the latest programme record on or before the chosen date."""
        with self.connect() as con:
            row = con.execute(
                """SELECT d.report_date,t.unit,d.planned_cumulative,d.actual_cumulative
                   FROM daily_records d
                   JOIN tasks t ON t.id=d.task_id
                   JOIN workbooks w ON w.id=t.workbook_id
                   WHERE t.management_activity_no=? AND d.report_date<=?
                   ORDER BY d.report_date DESC,w.period_order DESC LIMIT 1""",
                (activity_no, report_date),
            ).fetchone()
        return dict(row) if row else None

    def save_penstock_result(self, sample_id, cast_item, structure, chainage, rn,
                              cast_date, test_date, required_mpa, result_mpa,
                              lab_reference, notes):
        cast_day = datetime.strptime(cast_date, "%Y-%m-%d").date()
        test_day = datetime.strptime(test_date, "%Y-%m-%d").date()
        age_days = (test_day-cast_day).days
        if age_days < 0:
            raise ValueError("Test date cannot be before the casting date.")
        if not sample_id.strip():
            raise ValueError("Enter a sample/cube ID.")
        with self.connect() as con:
            con.execute(
                """INSERT INTO penstock_concrete_results(
                       sample_id,cast_item,structure,chainage,rn,cast_date,test_date,
                       age_days,required_mpa,result_mpa,lab_reference,notes,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(sample_id) DO UPDATE SET
                       cast_item=excluded.cast_item,structure=excluded.structure,
                       chainage=excluded.chainage,rn=excluded.rn,
                       cast_date=excluded.cast_date,test_date=excluded.test_date,
                       age_days=excluded.age_days,required_mpa=excluded.required_mpa,
                       result_mpa=excluded.result_mpa,lab_reference=excluded.lab_reference,
                       notes=excluded.notes,updated_at=excluded.updated_at""",
                (
                    sample_id.strip(), cast_item.strip(), structure.strip(), chainage.strip(),
                    rn.strip(), cast_date, test_date, age_days, float(required_mpa),
                    float(result_mpa), lab_reference.strip(), notes.strip(),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def delete_penstock_result(self, sample_id):
        with self.connect() as con:
            con.execute(
                "DELETE FROM penstock_concrete_results WHERE sample_id=?",
                (sample_id,),
            )

    def penstock_results(self):
        with self.connect() as con:
            df = pd.read_sql_query(
                "SELECT * FROM penstock_concrete_results ORDER BY test_date DESC,sample_id",
                con,
            )
        if not df.empty:
            df["status"] = df.apply(
                lambda row: (
                    "PASS" if float(row["result_mpa"]) >= float(row["required_mpa"])
                    else ("EARLY / MONITOR" if int(row["age_days"]) < 28 else "FAIL")
                ),
                axis=1,
            )
        return df


def qty(value):
    return "-" if pd.isna(value) else f"{float(value):,.0f}"


def money(value):
    """Format positive values as R x and negative values as -R x."""
    if value is None or pd.isna(value):
        return "R -"
    if isinstance(value, str):
        raw = value.strip()
        bracket_negative = raw.startswith("(") and raw.endswith(")")
        cleaned = raw.replace("R", "").replace(" ", "").replace(",", "").strip("()")
        try:
            number = float(cleaned)
        except ValueError:
            return value
        if bracket_negative:
            number = -abs(number)
    else:
        number = float(value)
    prefix = "-R " if number < 0 else "R "
    return f"{prefix}{abs(number):,.2f}"


def is_negative_money(value):
    if value is None or pd.isna(value):
        return False
    if isinstance(value, (int, float)):
        return float(value) < 0
    raw = str(value).strip()
    return raw.startswith("-") or (raw.startswith("(") and raw.endswith(")"))


class SummaryCellGrid(ttk.Frame):
    """Scrollable summary grid with independent formatting for every cell."""

    HEADER_BG = "#D9D7D2"
    BORDER = "#8A8A8A"
    WHITE_BG = "#FFFFFF"
    ZERO_FG = "#111827"
    POSITIVE_BG = "#D9FBE5"
    POSITIVE_FG = "#166534"
    NEGATIVE_BG = "#FDE2E2"
    NEGATIVE_FG = "#B91C1C"

    def __init__(self, master, headers, widths):
        super().__init__(master)
        self.headers = headers
        self.base_widths = list(widths)
        self.widths = list(widths)
        # Minimum readable widths used when the window is narrower than the
        # original desktop design. The grid then contracts to the viewport
        # instead of forcing columns/buttons off-screen.
        default_mins = [58, 150, 125, 105, 105, 105, 165]
        self.min_widths = [
            default_mins[i] if i < len(default_mins) else 80
            for i in range(len(self.widths))
        ]

        self.canvas = tk.Canvas(
            self,
            highlightthickness=0,
            background=self.WHITE_BG,
        )
        self.v_scroll = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview
        )
        self.h_scroll = ttk.Scrollbar(
            self, orient="horizontal", command=self.canvas.xview
        )
        self.canvas.configure(
            yscrollcommand=self.v_scroll.set,
            xscrollcommand=self.h_scroll.set,
        )

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.v_scroll.grid(row=0, column=1, sticky="ns")
        self.h_scroll.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.inner = tk.Frame(self.canvas, background=self.WHITE_BG)
        self.window_id = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )
        self.inner.bind("<Configure>", self._update_scrollregion)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind_all("<MouseWheel>", self._mousewheel)

    def _update_scrollregion(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, _event=None):
        available = max(1, self.canvas.winfo_width())
        base_total = sum(self.base_widths)
        min_total = sum(self.min_widths)

        if available >= base_total:
            widths = list(self.base_widths)
        elif available >= min_total:
            # Scale the preferred widths down proportionally, but keep each
            # column readable. This makes the complete table fit common
            # laptop resolutions such as 1366 x 768.
            scale = available / base_total
            widths = [
                max(min_w, int(base_w * scale))
                for base_w, min_w in zip(self.base_widths, self.min_widths)
            ]
            # Trim any rounding/minimum-width overflow from flexible columns.
            overflow = sum(widths) - available
            for idx in (6, 1, 2, 5, 4, 3, 0):
                if overflow <= 0:
                    break
                room = max(0, widths[idx] - self.min_widths[idx])
                take = min(room, overflow)
                widths[idx] -= take
                overflow -= take
        else:
            widths = list(self.min_widths)

        self.widths = widths
        for column, width in enumerate(widths):
            self.inner.grid_columnconfigure(
                column, minsize=width, weight=1 if column in (1, 6) else 0
            )

        content_width = max(sum(widths), available)
        self.canvas.itemconfigure(self.window_id, width=content_width)

    def _mousewheel(self, event):
        if self.winfo_ismapped():
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    @staticmethod
    def colours(sign):
        if sign < 0:
            return (
                SummaryCellGrid.NEGATIVE_BG,
                SummaryCellGrid.NEGATIVE_FG,
            )
        if sign > 0:
            return (
                SummaryCellGrid.POSITIVE_BG,
                SummaryCellGrid.POSITIVE_FG,
            )
        return SummaryCellGrid.WHITE_BG, SummaryCellGrid.ZERO_FG

    def render(self, rows):
        """
        rows contains:
        {
            "values": [...],
            "signs": [number-or-None per column],
            "bold": bool,
        }
        """
        for widget in self.inner.winfo_children():
            widget.destroy()

        # SummaryCellGrid is built from plain tk.Label widgets, so it does not
        # inherit ttk/CustomTkinter TV scaling.  Read the root presentation mode
        # explicitly and size this table for viewing across a meeting room.
        tv = bool(getattr(self.winfo_toplevel(), "_tv_mode", False))
        header_font_size = 18 if tv else 10
        body_font_size = 17 if tv else 10
        header_pady = 11 if tv else 10
        body_pady = 7 if tv else 6
        cell_padx = 10 if tv else 7

        for column, (header, width) in enumerate(
            zip(self.headers, self.widths)
        ):
            label = tk.Label(
                self.inner,
                text=header,
                background=self.HEADER_BG,
                foreground="#111111",
                font=("Segoe UI", header_font_size, "bold"),
                relief="solid",
                borderwidth=1,
                anchor="center",
                padx=cell_padx,
                pady=header_pady,
            )
            label.grid(row=0, column=column, sticky="nsew")
            self.inner.grid_columnconfigure(
                column, minsize=width, weight=1 if column in (1, 6) else 0
            )

        for row_index, row in enumerate(rows, start=1):
            values = row["values"]
            signs = row["signs"]
            bold = row.get("bold", False)

            for column, value in enumerate(values):
                sign = signs[column]
                bg, fg = self.colours(sign) if sign is not None else (
                    self.WHITE_BG,
                    self.ZERO_FG,
                )

                anchor = "w" if column in (1, 2, 6) else "center"
                font = (
                    "Segoe UI",
                    body_font_size,
                    "bold" if bold else "normal",
                )
                label = tk.Label(
                    self.inner,
                    text=value,
                    background=bg,
                    foreground=fg,
                    font=font,
                    relief="solid",
                    borderwidth=1,
                    anchor=anchor,
                    padx=cell_padx,
                    pady=body_pady,
                )
                label.grid(
                    row=row_index,
                    column=column,
                    sticky="nsew",
                )

        self.inner.update_idletasks()
        self._update_scrollregion()


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} — {VERSION}")
        # Start at a safe size, then maximise to the available Windows work
        # area. The old 1560x920 / 1280x780 fixed layout clipped controls on
        # 1366x768 laptops.
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()

        # TV / presentation scaling.  A 55-inch display is normally viewed from
        # several metres away, so use larger controls, fonts and table rows on
        # Full-HD/4K screens.  Ctrl+T toggles this at any time.
        self._tv_mode = screen_w >= 1920
        self._normal_widget_scale = 1.0
        self._tv_widget_scale = 1.30 if screen_w < 3000 else 1.50
        ctk.set_widget_scaling(
            self._tv_widget_scale if self._tv_mode else self._normal_widget_scale
        )
        ctk.set_window_scaling(1.0)

        start_w = min(1560, max(960, screen_w - 80))
        start_h = min(920, max(620, screen_h - 100))
        self.geometry(f"{start_w}x{start_h}")
        self.minsize(900, 600)
        self._fullscreen = False
        self._sidebar_visible = True
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")

        self.db = Database(DB_PATH)
        self.syncing = False
        self.current_figure = None
        self.current_canvas = None
        self.period_var = ctk.StringVar(value="All periods")
        self.date_var = ctk.StringVar(value="")
        self.activity_var = ctk.StringVar(value="")
        self.activity_lookup = {}
        self.office_hours_var = ctk.StringVar(value="10")
        self.office_factor_var = ctk.StringVar(value="1.0")
        self.office_result_var = ctk.StringVar(value="Office labour: R -")
        self.office_use_default_var = ctk.BooleanVar(value=True)
        self.office_day_type_var = ctk.StringVar(value="Normal Working Day")
        self.office_notes_var = ctk.StringVar(value="")
        self.comment_activity_var = ctk.StringVar(value="")
        self.comment_text_var = ctk.StringVar(value="")
        self.foreman_activity_var = ctk.StringVar(value="")
        self.foreman_name_var = ctk.StringVar(value="")
        today = date.today()
        league_start, league_end = self.db.league_week_bounds(today)
        self.league_reference_var = ctk.StringVar(value=today.isoformat())
        self.league_team_var = ctk.StringVar(value="Makanyane")
        self.league_target_date_var = ctk.StringVar(value=league_start.isoformat())
        self.league_target_qty_var = ctk.StringVar(value="2800")
        self.league_target_reason_var = ctk.StringVar(value="")
        self.graph_start_var = ctk.StringVar(value=league_start.isoformat())
        self.graph_end_var = ctk.StringVar(value=league_end.isoformat())
        self.no_work_start_var = ctk.StringVar(value="")
        self.no_work_end_var = ctk.StringVar(value="")
        self.no_work_reason_var = ctk.StringVar(value="No work")
        self.penstock_status_date_var = ctk.StringVar(value=today.isoformat())
        self.penstock_target_date_var = ctk.StringVar(
            value=self.db.penstock_setting("target_date", PENSTOCK_TARGET_DATE.isoformat())
        )
        self.penstock_cast_item_var = ctk.StringVar(value="ENC-01")
        self.penstock_cast_qty_var = ctk.StringVar(value="0")
        self.penstock_cast_date_var = ctk.StringVar(value=today.isoformat())
        self.penstock_cast_rn_var = ctk.StringVar(value="")
        self.penstock_cast_notes_var = ctk.StringVar(value="")
        self.penstock_component_var = ctk.StringVar(value="PEN-01")
        self.penstock_installed_var = ctk.BooleanVar(value=False)
        self.penstock_component_date_var = ctk.StringVar(value=today.isoformat())
        self.penstock_component_rn_var = ctk.StringVar(value="")
        self.penstock_qc_var = ctk.StringVar(value="Pending")
        self.penstock_asbuilt_var = ctk.StringVar(value="Pending")
        self.result_sample_var = ctk.StringVar(value="")
        self.result_cast_item_var = ctk.StringVar(value="ENC-01")
        self.result_structure_var = ctk.StringVar(value="Mass Encasement")
        self.result_chainage_var = ctk.StringVar(value="")
        self.result_rn_var = ctk.StringVar(value="")
        self.result_cast_date_var = ctk.StringVar(value=today.isoformat())
        self.result_test_date_var = ctk.StringVar(value=today.isoformat())
        self.result_required_var = ctk.StringVar(value="15")
        self.result_mpa_var = ctk.StringVar(value="")
        self.result_lab_ref_var = ctk.StringVar(value="")
        self.result_notes_var = ctk.StringVar(value="")
        self.penstock_layout_canvas = None
        self.penstock_layout_figure = None

        self.configure_styles()
        self.build_ui()
        self.bind("<F11>", self.toggle_fullscreen)
        self.bind("<Escape>", self.exit_fullscreen)
        self.bind("<Control-b>", self.toggle_sidebar)
        self.bind("<Control-t>", self.toggle_tv_mode)
        self.after(120, self.maximise_window)
        self.refresh_all()

    def maximise_window(self):
        """Use the full available desktop area without forcing true fullscreen."""
        try:
            self.state("zoomed")  # Windows
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def toggle_fullscreen(self, _event=None):
        self._fullscreen = not self._fullscreen
        self.attributes("-fullscreen", self._fullscreen)
        return "break"

    def exit_fullscreen(self, _event=None):
        if self._fullscreen:
            self._fullscreen = False
            self.attributes("-fullscreen", False)
        return "break"

    def toggle_sidebar(self, _event=None):
        """Hide/show the navigation panel to gain ~250-310 px table width."""
        if not hasattr(self, "side"):
            return "break"
        if self._sidebar_visible:
            self.side.grid_remove()
            self._sidebar_visible = False
        else:
            self.side.grid()
            self._sidebar_visible = True
        return "break"

    def toggle_tv_mode(self, _event=None):
        """Toggle large-format presentation sizing for a TV/projector."""
        self._tv_mode = not self._tv_mode
        ctk.set_widget_scaling(
            self._tv_widget_scale if self._tv_mode else self._normal_widget_scale
        )
        self.configure_styles()
        # Re-render the active data views so Matplotlib text also follows the mode.
        try:
            self.refresh_all()
        except Exception:
            pass
        return "break"

    def configure_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        tv = getattr(self, "_tv_mode", False)
        # TV sizing is driven by viewing distance, not by the physical pixel
        # density alone.  Make the DATA substantially larger while keeping the
        # surrounding controls moderate so the charts/tables retain screen area.
        body_size = 17 if tv else 10
        heading_size = 18 if tv else 10
        row_height = 46 if tv else 30
        heading_pad = (12, 14) if tv else (8, 9)
        style.configure("Treeview", rowheight=row_height, font=("Segoe UI", body_size),
                        background="#FFFFFF",fieldbackground="#FFFFFF")
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", heading_size),
                        padding=heading_pad)
        style.map("Treeview",background=[("selected","#2563EB")],
                  foreground=[("selected","#FFFFFF")])

        # Matplotlib text must be scaled separately from CustomTkinter/ttk.
        plt.rcParams.update({
            "font.size": 16 if tv else 10,
            "axes.titlesize": 21 if tv else 12,
            "axes.labelsize": 17 if tv else 10,
            "xtick.labelsize": 15 if tv else 9,
            "ytick.labelsize": 15 if tv else 9,
            "legend.fontsize": 15 if tv else 9,
        })

    def build_ui(self):
        self.grid_columnconfigure(1,weight=1)
        self.grid_rowconfigure(0,weight=1)

        side_width = 250 if self.winfo_screenwidth() <= 1440 else 310
        side=ctk.CTkFrame(self,width=side_width,corner_radius=0)
        self.side = side
        side.grid(row=0,column=0,sticky="nsew")
        side.grid_propagate(False)

        ctk.CTkLabel(side,text="Tweefontein\nProduction Manager",
                     font=ctk.CTkFont(size=24,weight="bold"),
                     justify="left").pack(anchor="w",padx=20,pady=(24,4))
        ctk.CTkLabel(side,text="BUILD 3.9.26 • PENSTOCK CONTROL",
                     text_color=("gray40","gray70")).pack(anchor="w",padx=20,pady=(0,18))

        ctk.CTkButton(side,text="Synchronise Task Tabs → SQL",
                      command=self.sync_async,height=42).pack(fill="x",padx=20,pady=(0,8))
        ctk.CTkButton(side,text="Open Export Folder",
                      command=self.open_exports,fg_color="gray35",
                      hover_color="gray25").pack(fill="x",padx=20,pady=(0,8))
        ctk.CTkButton(side,text="Hide Sidebar  (Ctrl+B)",
                      command=self.toggle_sidebar,fg_color="gray45",
                      hover_color="gray35").pack(fill="x",padx=20,pady=(0,18))

        ctk.CTkLabel(side,text="Period").pack(anchor="w",padx=20)
        self.period_menu=ctk.CTkOptionMenu(
            side,variable=self.period_var,values=["All periods"],
            command=lambda _v:self.period_changed()
        )
        self.period_menu.pack(fill="x",padx=20,pady=(4,12))

        self.status=ctk.CTkLabel(side,text="Ready",wraplength=270,
                                 justify="left",anchor="w",
                                 text_color=("gray40","gray70"))
        self.status.pack(side="bottom",fill="x",padx=20,pady=20)

        body=ctk.CTkFrame(self,corner_radius=0)
        body.grid(row=0,column=1,sticky="nsew")
        body.grid_columnconfigure(0,weight=1)
        body.grid_rowconfigure(0,weight=1)

        self.tabs=ctk.CTkTabview(body)
        tab_pad = 8 if self.winfo_screenwidth() <= 1440 else 18
        self.tabs.grid(row=0,column=0,sticky="nsew",padx=tab_pad,pady=tab_pad)

        self.summary_tab=self.tabs.add("Daily Summary")
        self.production_tab=self.tabs.add("Daily Production")
        self.timeline_tab=self.tabs.add("All Activities Timeline")
        self.league_tab=self.tabs.add("Team Champions League")
        self.penstock_tab=self.tabs.add("Penstock Control")
        self.log_tab=self.tabs.add("Import Log")

        self.build_summary()
        self.build_production()
        self.build_timeline()
        self.build_league()
        self.build_penstock_control()
        self.build_log()

    def build_summary(self):
        self.summary_tab.grid_columnconfigure(0,weight=1)
        self.summary_tab.grid_rowconfigure(4,weight=1)

        # Two control rows prevent action buttons from disappearing off the
        # right edge on laptop screens.
        controls=ctk.CTkFrame(self.summary_tab)
        controls.grid(row=0,column=0,sticky="ew",padx=8,pady=8)
        primary=ctk.CTkFrame(controls,fg_color="transparent")
        primary.pack(fill="x",padx=2,pady=(2,1))
        actions=ctk.CTkFrame(controls,fg_color="transparent")
        actions.pack(fill="x",padx=2,pady=(1,2))

        ctk.CTkLabel(primary,text="Date").pack(side="left",padx=(6,4))
        self.date_menu=ctk.CTkOptionMenu(
            primary,variable=self.date_var,values=["-"],width=125,
            command=lambda _v:self.load_summary()
        )
        self.date_menu.pack(side="left",padx=4)

        self.default_check=ctk.CTkCheckBox(
            primary,text="Use default hours",variable=self.office_use_default_var,
            command=self.toggle_office_default,width=120
        )
        self.default_check.pack(side="left",padx=(8,3))
        ctk.CTkLabel(primary,text="Hours").pack(side="left",padx=(4,2))
        self.office_hours_entry=ctk.CTkEntry(primary,textvariable=self.office_hours_var,width=52)
        self.office_hours_entry.pack(side="left",padx=2)
        ctk.CTkLabel(primary,text="Factor").pack(side="left",padx=(4,2))
        self.office_factor_menu = ctk.CTkOptionMenu(
            primary,variable=self.office_factor_var,
            values=["1.0", "1.5", "2.0"],width=64
        )
        self.office_factor_menu.pack(side="left",padx=2)
        self.office_day_menu=ctk.CTkOptionMenu(
            primary,variable=self.office_day_type_var,
            values=["Normal Working Day","Short Day","Long Weekend","Rain Day",
                    "No Production","Shutdown","Safety Stand-down","Restricted Access",
                    "Saturday","Sunday","Public Holiday"],width=145
        )
        self.office_day_menu.pack(side="left",padx=3)
        self.office_notes_entry=ctk.CTkEntry(
            primary,textvariable=self.office_notes_var,placeholder_text="Day notes",width=120
        )
        self.office_notes_entry.pack(side="left",padx=3,fill="x",expand=True)

        ctk.CTkButton(actions,text="Save Office Labour",
                      command=self.save_office_labour,width=130).pack(side="left",padx=5)
        ctk.CTkLabel(actions,text="F11 = Full screen   •   Ctrl+B = Hide sidebar   •   Ctrl+T = TV size",
                     text_color=("gray40","gray70")).pack(side="left",padx=8)
        ctk.CTkButton(actions,text="Download PNG",
                      command=self.export_summary_png,width=115).pack(side="right",padx=5)
        ctk.CTkButton(actions,text="Download CSV",
                      command=self.export_summary_csv,width=115).pack(side="right",padx=5)

        self.summary_heading=ctk.CTkLabel(
            self.summary_tab,text="Daily Summary",
            font=ctk.CTkFont(size=21,weight="bold"),anchor="w"
        )
        self.summary_heading.grid(row=1,column=0,sticky="ew",padx=10,pady=(0,5))

        comment_bar=ctk.CTkFrame(self.summary_tab)
        comment_bar.grid(row=2,column=0,sticky="ew",padx=8,pady=(0,6))
        ctk.CTkLabel(comment_bar,text="Daily activity comment").pack(side="left",padx=8)
        self.comment_activity_menu=ctk.CTkOptionMenu(
            comment_bar,variable=self.comment_activity_var,values=["-"],width=280,
            command=lambda _v:self.load_selected_comment()
        )
        self.comment_activity_menu.pack(side="left",padx=5)
        ctk.CTkEntry(comment_bar,textvariable=self.comment_text_var,
                     placeholder_text="Comment for selected activity and date",width=450).pack(
                         side="left",padx=5,fill="x",expand=True)
        ctk.CTkButton(comment_bar,text="Save Comment",command=self.save_activity_comment,width=120).pack(
            side="left",padx=6)

        foreman_bar=ctk.CTkFrame(self.summary_tab)
        foreman_bar.grid(row=3,column=0,sticky="ew",padx=8,pady=(0,6))
        ctk.CTkLabel(foreman_bar,text="Responsible foreman").pack(
            side="left",padx=8
        )
        self.foreman_activity_menu=ctk.CTkOptionMenu(
            foreman_bar,
            variable=self.foreman_activity_var,
            values=["-"],
            width=280,
            command=lambda _v:self.load_selected_foreman()
        )
        self.foreman_activity_menu.pack(side="left",padx=5)
        ctk.CTkEntry(
            foreman_bar,
            textvariable=self.foreman_name_var,
            placeholder_text="Foreman name for selected activity",
            width=380
        ).pack(side="left",padx=5)
        ctk.CTkButton(
            foreman_bar,
            text="Save Foreman",
            command=self.save_activity_foreman,
            width=120
        ).pack(side="left",padx=6)

        columns=("Activity No.","Task Description","Responsible Foreman",
                 "Daily +Profit/Loss","Monthly Totals","Total to date","Comments")
        frame=ctk.CTkFrame(self.summary_tab)
        frame.grid(row=4,column=0,sticky="nsew",padx=8,pady=8)
        frame.grid_columnconfigure(0,weight=1)
        frame.grid_rowconfigure(0,weight=1)

        self.summary_grid=SummaryCellGrid(
            frame,
            headers=columns,
            widths=[105,300,205,170,170,170,330],
        )
        self.summary_grid.grid(row=0,column=0,sticky="nsew")

    def build_production(self):
        self.production_tab.grid_columnconfigure(0,weight=1)
        self.production_tab.grid_rowconfigure(2,weight=3,minsize=220)
        self.production_tab.grid_rowconfigure(3,weight=2,minsize=150)

        controls=ctk.CTkFrame(self.production_tab)
        controls.grid(row=0,column=0,sticky="ew",padx=8,pady=8)
        primary=ctk.CTkFrame(controls,fg_color="transparent")
        primary.pack(fill="x",padx=2,pady=(2,1))
        actions=ctk.CTkFrame(controls,fg_color="transparent")
        actions.pack(fill="x",padx=2,pady=(1,1))
        overrides=ctk.CTkFrame(controls,fg_color="transparent")
        overrides.pack(fill="x",padx=2,pady=(1,2))

        ctk.CTkLabel(primary,text="Activity 1–17").pack(side="left",padx=(6,4))
        self.activity_menu=ctk.CTkOptionMenu(
            primary,variable=self.activity_var,values=["-"],width=280,
            command=lambda _v:self.load_production()
        )
        self.activity_menu.pack(side="left",padx=4)
        ctk.CTkLabel(primary,text="Graph from").pack(side="left",padx=(8,3))
        self.production_graph_start_var=ctk.StringVar(value="")
        ctk.CTkEntry(primary,textvariable=self.production_graph_start_var,width=100).pack(side="left",padx=2)
        ctk.CTkLabel(primary,text="to").pack(side="left",padx=3)
        self.production_graph_end_var=ctk.StringVar(value="")
        ctk.CTkEntry(primary,textvariable=self.production_graph_end_var,width=100).pack(side="left",padx=2)
        ctk.CTkButton(primary,text="Apply Graph Range",command=self.load_production,width=120).pack(side="left",padx=5)

        ctk.CTkLabel(actions,text="F11 full screen • Ctrl+B sidebar • Ctrl+T TV size",
                     text_color=("gray40","gray70")).pack(side="left",padx=6)
        ctk.CTkButton(actions,text="Download Chart PNG",
                      command=self.export_chart_png,width=135).pack(side="right",padx=4)
        ctk.CTkButton(actions,text="Download Table PNG",
                      command=self.export_production_png,width=135).pack(side="right",padx=4)
        ctk.CTkButton(actions,text="Download CSV",
                      command=self.export_production_csv,width=110).pack(side="right",padx=4)

        ctk.CTkLabel(overrides,text="No-work plan override").pack(
            side="left",padx=(6,4)
        )
        ctk.CTkLabel(overrides,text="From").pack(side="left",padx=(6,2))
        ctk.CTkEntry(
            overrides,textvariable=self.no_work_start_var,width=105,
            placeholder_text="YYYY-MM-DD"
        ).pack(side="left",padx=2)
        ctk.CTkLabel(overrides,text="To").pack(side="left",padx=(6,2))
        ctk.CTkEntry(
            overrides,textvariable=self.no_work_end_var,width=105,
            placeholder_text="YYYY-MM-DD"
        ).pack(side="left",padx=2)
        ctk.CTkEntry(
            overrides,textvariable=self.no_work_reason_var,width=170,
            placeholder_text="Reason"
        ).pack(side="left",padx=6)
        ctk.CTkButton(
            overrides,text="Set Planned = 0",
            command=self.apply_no_work_override,width=135
        ).pack(side="left",padx=4)
        ctk.CTkButton(
            overrides,text="Restore Normal Plan",
            command=self.restore_no_work_override,width=150,
            fg_color="#5B5B5B",hover_color="#444444"
        ).pack(side="left",padx=4)

        self.production_heading=ctk.CTkLabel(
            self.production_tab,text="Daily Production",
            font=ctk.CTkFont(size=21,weight="bold"),anchor="w"
        )
        self.production_heading.grid(row=1,column=0,sticky="ew",padx=10,pady=(0,5))

        # Graph and table now share the available height instead of using a
        # fixed 330-pixel chart that clipped the table on smaller screens.
        self.chart_frame=ctk.CTkFrame(self.production_tab)
        self.chart_frame.grid(row=2,column=0,sticky="nsew",padx=8,pady=(4,4))

        columns=("Date","Planned - Daily Quantity","Actual - Daily Quantity",
                 "Planned - Total to Date","Actual - Total to Date","Variance")
        frame=ctk.CTkFrame(self.production_tab)
        frame.grid(row=3,column=0,sticky="nsew",padx=8,pady=(4,8))
        frame.grid_columnconfigure(0,weight=1);frame.grid_rowconfigure(0,weight=1)
        self.production_tree=ttk.Treeview(frame,columns=columns,show="headings")
        for col,w in zip(columns,[105,155,155,165,165,105]):
            self.production_tree.heading(col,text=col)
            self.production_tree.column(col,width=w,minwidth=85,anchor="center",stretch=True)
        self.production_tree.grid(row=0,column=0,sticky="nsew")
        scroll=ttk.Scrollbar(frame,orient="vertical",command=self.production_tree.yview)
        scroll.grid(row=0,column=1,sticky="ns")
        hscroll=ttk.Scrollbar(frame,orient="horizontal",command=self.production_tree.xview)
        hscroll.grid(row=1,column=0,sticky="ew")
        self.production_tree.configure(yscrollcommand=scroll.set,xscrollcommand=hscroll.set)
        self.production_tree.tag_configure("above",foreground="#166534")
        self.production_tree.tag_configure("below",foreground="#B91C1C")
        self.production_tree.tag_configure(
            "no_work",background="#E5E7EB",foreground="#374151"
        )

    def build_timeline(self):
        self.timeline_tab.grid_columnconfigure(0,weight=1)
        self.timeline_tab.grid_rowconfigure(1,weight=1)
        controls=ctk.CTkFrame(self.timeline_tab)
        controls.grid(row=0,column=0,sticky="ew",padx=8,pady=8)
        ctk.CTkButton(controls,text="Refresh",command=self.load_timeline).pack(side="left",padx=8)
        ctk.CTkButton(controls,text="Download CSV",command=self.export_timeline_csv).pack(side="right",padx=8)

        columns=("Date","Period","Task Code","Activity","Unit","Planned Daily",
                 "Actual Daily","Planned Total","Actual Total","Daily P/L")
        frame=ctk.CTkFrame(self.timeline_tab)
        frame.grid(row=1,column=0,sticky="nsew",padx=8,pady=8)
        frame.grid_columnconfigure(0,weight=1);frame.grid_rowconfigure(0,weight=1)
        self.timeline_tree=ttk.Treeview(frame,columns=columns,show="headings")
        widths=[100,110,90,260,70,120,120,130,130,120]
        for col,w in zip(columns,widths):
            self.timeline_tree.heading(col,text=col)
            self.timeline_tree.column(col,width=w,anchor="w" if col=="Activity" else "center")
        self.timeline_tree.grid(row=0,column=0,sticky="nsew")
        scroll=ttk.Scrollbar(frame,orient="vertical",command=self.timeline_tree.yview)
        scroll.grid(row=0,column=1,sticky="ns")
        hscroll=ttk.Scrollbar(frame,orient="horizontal",command=self.timeline_tree.xview)
        hscroll.grid(row=1,column=0,sticky="ew")
        self.timeline_tree.configure(yscrollcommand=scroll.set,xscrollcommand=hscroll.set)

    def build_league(self):
        self.league_tab.grid_columnconfigure(0,weight=1)
        self.league_tab.grid_rowconfigure(4,weight=1)

        controls=ctk.CTkFrame(self.league_tab)
        controls.grid(row=0,column=0,sticky="ew",padx=8,pady=8)
        ctk.CTkLabel(controls,text="Select any date in league week").pack(
            side="left",padx=6
        )
        ctk.CTkEntry(
            controls,textvariable=self.league_reference_var,width=110
        ).pack(side="left",padx=4)
        ctk.CTkButton(
            controls,text="Load Thu–Wed League",command=self.load_league
        ).pack(side="left",padx=8)
        ctk.CTkButton(
            controls,text="Download PNG",command=self.export_league_png
        ).pack(side="right",padx=8)
        ctk.CTkButton(
            controls,text="Download CSV",command=self.export_league_csv
        ).pack(side="right",padx=8)

        self.league_week_label=ctk.CTkLabel(
            self.league_tab,text="Thursday–Wednesday Production Champions League",
            font=ctk.CTkFont(size=20,weight="bold"),anchor="w"
        )
        self.league_week_label.grid(
            row=1,column=0,sticky="ew",padx=10,pady=(0,5)
        )

        target_bar=ctk.CTkFrame(self.league_tab)
        target_bar.grid(row=2,column=0,sticky="ew",padx=8,pady=5)
        ctk.CTkLabel(target_bar,text="Fair target adjustment").pack(
            side="left",padx=6
        )
        self.league_team_menu=ctk.CTkOptionMenu(
            target_bar,variable=self.league_team_var,
            values=["Makanyane","Silas","Pleasure","Moshe"],
            width=130,command=lambda _v:self.load_league_target_editor()
        )
        self.league_team_menu.pack(side="left",padx=4)
        self.league_target_date_menu=ctk.CTkOptionMenu(
            target_bar,variable=self.league_target_date_var,
            values=["-"],width=115,
            command=lambda _v:self.load_league_target_editor()
        )
        self.league_target_date_menu.pack(side="left",padx=4)
        ctk.CTkLabel(target_bar,text="Target m³").pack(side="left",padx=(10,3))
        ctk.CTkEntry(
            target_bar,textvariable=self.league_target_qty_var,width=85
        ).pack(side="left",padx=3)
        ctk.CTkLabel(target_bar,text="Reason").pack(side="left",padx=(10,3))
        ctk.CTkEntry(
            target_bar,textvariable=self.league_target_reason_var,width=260
        ).pack(side="left",padx=3)
        ctk.CTkButton(
            target_bar,text="Save Target",command=self.save_league_target,width=100
        ).pack(side="left",padx=6)
        ctk.CTkButton(
            target_bar,text="Restore Default",
            command=self.restore_league_target,width=110
        ).pack(side="left",padx=4)

        graph_bar=ctk.CTkFrame(self.league_tab)
        graph_bar.grid(row=3,column=0,sticky="ew",padx=8,pady=5)
        ctk.CTkLabel(graph_bar,text="Progress graphs").pack(side="left",padx=6)
        self.graph_team_menu=ctk.CTkOptionMenu(
            graph_bar,variable=self.league_team_var,
            values=["Makanyane","Silas","Pleasure","Moshe"],width=130,
            command=lambda _v:self.render_league_graphs()
        )
        self.graph_team_menu.pack(side="left",padx=4)
        ctk.CTkLabel(graph_bar,text="From").pack(side="left",padx=(10,3))
        ctk.CTkEntry(
            graph_bar,textvariable=self.graph_start_var,width=105
        ).pack(side="left",padx=3)
        ctk.CTkLabel(graph_bar,text="To").pack(side="left",padx=(8,3))
        ctk.CTkEntry(
            graph_bar,textvariable=self.graph_end_var,width=105
        ).pack(side="left",padx=3)
        ctk.CTkButton(
            graph_bar,text="Update Interactive Graphs",
            command=self.render_league_graphs
        ).pack(side="left",padx=8)

        main=ctk.CTkTabview(self.league_tab)
        main.grid(row=4,column=0,sticky="nsew",padx=8,pady=8)
        standings_tab=main.add("League Table")
        daily_tab=main.add("Daily Targets")
        graphs_tab=main.add("Progress Graphs")
        team_tabs = {
            team_name: main.add(team_name)
            for team_name in ("Makanyane", "Silas", "Pleasure", "Moshe")
        }

        for tab in (standings_tab,daily_tab,graphs_tab,*team_tabs.values()):
            tab.grid_columnconfigure(0,weight=1)
            tab.grid_rowconfigure(0,weight=1)

        columns=("No","Team Name","Plan","Actual","GD","PTS","Incentive")
        frame=ctk.CTkFrame(standings_tab)
        frame.grid(row=0,column=0,sticky="nsew")
        frame.grid_rowconfigure(0,weight=1);frame.grid_columnconfigure(0,weight=1)
        self.league_tree=ttk.Treeview(frame,columns=columns,show="headings")
        for col,w in zip(columns,[65,190,145,145,120,120,150]):
            self.league_tree.heading(col,text=col)
            self.league_tree.column(col,width=w,anchor="center")
        self.league_tree.grid(row=0,column=0,sticky="nsew")
        vs=ttk.Scrollbar(frame,orient="vertical",command=self.league_tree.yview)
        vs.grid(row=0,column=1,sticky="ns")
        hs=ttk.Scrollbar(frame,orient="horizontal",command=self.league_tree.xview)
        hs.grid(row=1,column=0,sticky="ew")
        self.league_tree.configure(yscrollcommand=vs.set,xscrollcommand=hs.set)
        self.league_tree.tag_configure(
            "qualified",background="#DCFCE7",foreground="#166534",
            font=("Segoe UI Semibold",10)
        )
        self.league_tree.tag_configure(
            "notyet",background="#FEE2E2",foreground="#991B1B"
        )

        daily_columns=("Date","Team","Target","Actual","Variance",
                       "Counts in Score","Edited","Reason")
        daily_frame=ctk.CTkFrame(daily_tab)
        daily_frame.grid(row=0,column=0,sticky="nsew")
        daily_frame.grid_rowconfigure(0,weight=1)
        daily_frame.grid_columnconfigure(0,weight=1)
        self.league_daily_tree=ttk.Treeview(
            daily_frame,columns=daily_columns,show="headings"
        )
        for col,w in zip(daily_columns,[105,120,110,110,110,110,80,300]):
            self.league_daily_tree.heading(col,text=col)
            self.league_daily_tree.column(
                col,width=w,anchor="w" if col=="Reason" else "center"
            )
        self.league_daily_tree.grid(row=0,column=0,sticky="nsew")
        dvs=ttk.Scrollbar(
            daily_frame,orient="vertical",command=self.league_daily_tree.yview
        )
        dvs.grid(row=0,column=1,sticky="ns")
        dhs=ttk.Scrollbar(
            daily_frame,orient="horizontal",command=self.league_daily_tree.xview
        )
        dhs.grid(row=1,column=0,sticky="ew")
        self.league_daily_tree.configure(
            yscrollcommand=dvs.set,xscrollcommand=dhs.set
        )

        self.league_graph_frame=ctk.CTkFrame(graphs_tab)
        self.league_graph_frame.grid(row=0,column=0,sticky="nsew")

        # Dedicated production tables for each league team. These use the same
        # From/To range as the interactive team graphs so figures reconcile.
        self.league_team_trees = {}
        team_columns = (
            "Date", "Daily Target", "Daily Actual", "Daily Variance",
            "Cumulative Target", "Cumulative Actual",
            "Cumulative Variance", "Achievement %"
        )
        team_widths = [105, 115, 115, 120, 145, 145, 150, 115]
        for team_name, team_tab in team_tabs.items():
            team_frame = ctk.CTkFrame(team_tab)
            team_frame.grid(row=0,column=0,sticky="nsew")
            team_frame.grid_rowconfigure(1,weight=1)
            team_frame.grid_columnconfigure(0,weight=1)

            heading = ctk.CTkLabel(
                team_frame,
                text=f"{team_name} — Daily & Cumulative Production",
                font=ctk.CTkFont(size=18,weight="bold"),
                anchor="w"
            )
            heading.grid(row=0,column=0,sticky="ew",padx=8,pady=(8,4))

            table_frame = ctk.CTkFrame(team_frame)
            table_frame.grid(row=1,column=0,sticky="nsew",padx=6,pady=(0,6))
            table_frame.grid_rowconfigure(0,weight=1)
            table_frame.grid_columnconfigure(0,weight=1)

            tree = ttk.Treeview(
                table_frame,columns=team_columns,show="headings"
            )
            for col,width in zip(team_columns,team_widths):
                tree.heading(col,text=col)
                tree.column(col,width=width,anchor="center")
            tree.grid(row=0,column=0,sticky="nsew")

            tvs = ttk.Scrollbar(
                table_frame,orient="vertical",command=tree.yview
            )
            tvs.grid(row=0,column=1,sticky="ns")
            ths = ttk.Scrollbar(
                table_frame,orient="horizontal",command=tree.xview
            )
            ths.grid(row=1,column=0,sticky="ew")
            tree.configure(yscrollcommand=tvs.set,xscrollcommand=ths.set)

            tree.tag_configure(
                "ahead",background="#DCFCE7",foreground="#166534"
            )
            tree.tag_configure(
                "behind",background="#FEE2E2",foreground="#991B1B"
            )
            tree.tag_configure(
                "even",background="#FFFFFF",foreground="#111827"
            )
            self.league_team_trees[team_name] = tree

    def build_penstock_control(self):
        self.penstock_tab.grid_columnconfigure(0, weight=1)
        self.penstock_tab.grid_rowconfigure(0, weight=1)
        inner = ctk.CTkTabview(self.penstock_tab)
        inner.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.penstock_target_tab = inner.add("Target & Layout")
        self.penstock_cast_tab = inner.add("Cast Register")
        self.penstock_component_tab = inner.add("Pipe Chainage")
        self.penstock_results_tab = inner.add("Concrete Results")
        for tab in (self.penstock_target_tab, self.penstock_cast_tab,
                    self.penstock_component_tab, self.penstock_results_tab):
            tab.grid_columnconfigure(0, weight=1)

        # Target and spatial-layout dashboard.
        self.penstock_target_tab.grid_rowconfigure(2, weight=1)
        controls = ctk.CTkFrame(self.penstock_target_tab)
        controls.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(controls, text="Status date").pack(side="left", padx=(8,3))
        ctk.CTkEntry(controls, textvariable=self.penstock_status_date_var, width=110).pack(side="left", padx=3)
        ctk.CTkLabel(controls, text="Target date").pack(side="left", padx=(12,3))
        ctk.CTkEntry(controls, textvariable=self.penstock_target_date_var, width=110).pack(side="left", padx=3)
        ctk.CTkButton(controls, text="Recalculate to Target", command=self.load_penstock_control, width=165).pack(side="left", padx=8)
        ctk.CTkButton(controls, text="Save Target Date", command=self.save_penstock_target_date, width=135,
                      fg_color="#5B5B5B", hover_color="#444444").pack(side="left", padx=3)
        ctk.CTkButton(controls, text="Export Summary CSV", command=self.export_penstock_summary,
                      width=145).pack(side="right", padx=8)

        cards = ctk.CTkFrame(self.penstock_target_tab)
        cards.grid(row=1, column=0, sticky="ew", padx=6, pady=(0,6))
        for col in range(6): cards.grid_columnconfigure(col, weight=1)
        self.penstock_kpi_labels = {}
        card_specs = [
            ("remaining", "CONCRETE LEFT", "m3"),
            ("days", "WORKING DAYS", "days"),
            ("daily", "REQUIRED / DAY", "m3/day"),
            ("cycles", "9 m3 CYCLES / DAY", "casts/day"),
            ("grade15", "15 MPa / DAY", "m3/day"),
            ("grade35", "35 MPa / DAY", "m3/day"),
        ]
        for col, (key, heading, unit) in enumerate(card_specs):
            card = ctk.CTkFrame(cards, border_width=1)
            card.grid(row=0, column=col, sticky="nsew", padx=3, pady=4)
            ctk.CTkLabel(card, text=heading, text_color=("gray35","gray70")).pack(pady=(8,1))
            label = ctk.CTkLabel(card, text="-", font=ctk.CTkFont(size=25, weight="bold"))
            label.pack(pady=1)
            ctk.CTkLabel(card, text=unit, text_color=("gray40","gray65")).pack(pady=(0,8))
            self.penstock_kpi_labels[key] = label

        self.penstock_layout_frame = ctk.CTkFrame(self.penstock_target_tab)
        self.penstock_layout_frame.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)

        # Concrete casting register.
        self.penstock_cast_tab.grid_rowconfigure(1, weight=1)
        cast_form = ctk.CTkFrame(self.penstock_cast_tab)
        cast_form.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(cast_form, text="Cast item").pack(side="left", padx=(8,3))
        self.penstock_cast_menu = ctk.CTkOptionMenu(cast_form, variable=self.penstock_cast_item_var,
                                                    values=["ENC-01"], width=120,
                                                    command=lambda _v:self.load_selected_penstock_cast())
        self.penstock_cast_menu.pack(side="left", padx=3)
        ctk.CTkLabel(cast_form, text="Actual m3").pack(side="left", padx=(8,3))
        ctk.CTkEntry(cast_form, textvariable=self.penstock_cast_qty_var, width=80).pack(side="left", padx=3)
        ctk.CTkLabel(cast_form, text="Date").pack(side="left", padx=(8,3))
        ctk.CTkEntry(cast_form, textvariable=self.penstock_cast_date_var, width=105).pack(side="left", padx=3)
        ctk.CTkLabel(cast_form, text="RN").pack(side="left", padx=(8,3))
        ctk.CTkEntry(cast_form, textvariable=self.penstock_cast_rn_var, width=90).pack(side="left", padx=3)
        ctk.CTkEntry(cast_form, textvariable=self.penstock_cast_notes_var,
                     placeholder_text="Casting notes", width=240).pack(side="left", padx=6, fill="x", expand=True)
        ctk.CTkButton(cast_form, text="Save Cast", command=self.save_penstock_cast, width=110).pack(side="right", padx=8)
        cast_cols=("Item","Type","Start CH","End CH","Design m3","Actual m3","Remaining","MPa","Date","RN","Status")
        self.penstock_cast_tree=self._make_tree(self.penstock_cast_tab, cast_cols,
                                                [85,150,90,90,100,100,100,70,100,90,105], row=1)
        self.penstock_cast_tree.bind("<<TreeviewSelect>>",self._penstock_cast_tree_selected)
        self.penstock_cast_tree.tag_configure("complete", background="#DCFCE7", foreground="#166534")
        self.penstock_cast_tree.tag_configure("partial", background="#FEF3C7", foreground="#92400E")

        # Identified components control actual spatial position.
        self.penstock_component_tab.grid_rowconfigure(1, weight=1)
        component_form = ctk.CTkFrame(self.penstock_component_tab)
        component_form.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ctk.CTkLabel(component_form, text="Component").pack(side="left", padx=(8,3))
        self.penstock_component_menu=ctk.CTkOptionMenu(component_form, variable=self.penstock_component_var,
                                                       values=["PEN-01"], width=125,
                                                       command=lambda _v:self.load_selected_penstock_component())
        self.penstock_component_menu.pack(side="left", padx=3)
        ctk.CTkCheckBox(component_form, text="Installed", variable=self.penstock_installed_var).pack(side="left", padx=8)
        ctk.CTkLabel(component_form, text="Date").pack(side="left", padx=(5,2))
        ctk.CTkEntry(component_form, textvariable=self.penstock_component_date_var, width=105).pack(side="left", padx=2)
        ctk.CTkEntry(component_form, textvariable=self.penstock_component_rn_var, placeholder_text="RN", width=80).pack(side="left", padx=4)
        self.penstock_qc_menu=ctk.CTkOptionMenu(component_form, variable=self.penstock_qc_var,
                                                values=["Pending","Recorded","Accepted","Failed"], width=105)
        self.penstock_qc_menu.pack(side="left", padx=4)
        self.penstock_asbuilt_menu=ctk.CTkOptionMenu(component_form, variable=self.penstock_asbuilt_var,
                                                     values=["Pending","Recorded","Accepted","Failed","Not Required"], width=115)
        self.penstock_asbuilt_menu.pack(side="left", padx=4)
        ctk.CTkButton(component_form, text="Save Component", command=self.save_penstock_component,
                      width=125).pack(side="right", padx=8)
        component_cols=("ID","Seq","Component","Type","Start CH","End CH","Length","Installed","Date","RN","QC","As-Built")
        self.penstock_component_tree=self._make_tree(self.penstock_component_tab, component_cols,
                                                     [80,55,190,85,90,90,80,85,100,80,95,100], row=1)
        self.penstock_component_tree.bind("<<TreeviewSelect>>",self._penstock_component_tree_selected)
        self.penstock_component_tree.tag_configure("installed", background="#DCFCE7", foreground="#166534")

        # Concrete strength results.
        self.penstock_results_tab.grid_rowconfigure(2, weight=1)
        result_form1=ctk.CTkFrame(self.penstock_results_tab)
        result_form1.grid(row=0,column=0,sticky="ew",padx=6,pady=(6,2))
        entries1=[
            ("Sample",self.result_sample_var,100),("Cast item",self.result_cast_item_var,90),
            ("Structure",self.result_structure_var,150),("Chainage",self.result_chainage_var,100),
            ("RN",self.result_rn_var,80),("Cast date",self.result_cast_date_var,105),
            ("Test date",self.result_test_date_var,105),
        ]
        for label,var,width in entries1:
            ctk.CTkLabel(result_form1,text=label).pack(side="left",padx=(6,2))
            ctk.CTkEntry(result_form1,textvariable=var,width=width).pack(side="left",padx=2)
        result_form2=ctk.CTkFrame(self.penstock_results_tab)
        result_form2.grid(row=1,column=0,sticky="ew",padx=6,pady=(2,6))
        for label,var,width in [
            ("Required MPa",self.result_required_var,80),("Result MPa",self.result_mpa_var,80),
            ("Lab ref",self.result_lab_ref_var,110),("Notes",self.result_notes_var,280),
        ]:
            ctk.CTkLabel(result_form2,text=label).pack(side="left",padx=(6,2))
            ctk.CTkEntry(result_form2,textvariable=var,width=width).pack(side="left",padx=2)
        ctk.CTkButton(result_form2,text="Save Result",command=self.save_penstock_result,width=110).pack(side="right",padx=6)
        ctk.CTkButton(result_form2,text="Delete Selected",command=self.delete_penstock_result,width=125,
                      fg_color="#B91C1C",hover_color="#991B1B").pack(side="right",padx=4)
        result_cols=("Sample","Cast","Structure","Chainage","RN","Cast Date","Test Date","Age","Required","Result","Status","Lab Ref")
        self.penstock_results_tree=self._make_tree(self.penstock_results_tab,result_cols,
                                                   [90,80,145,90,75,95,95,55,80,80,115,100],row=2)
        self.penstock_results_tree.tag_configure("pass",background="#DCFCE7",foreground="#166534")
        self.penstock_results_tree.tag_configure("fail",background="#FEE2E2",foreground="#991B1B")
        self.penstock_results_tree.tag_configure("monitor",background="#FEF3C7",foreground="#92400E")

    @staticmethod
    def _make_tree(parent, columns, widths, row):
        frame=ctk.CTkFrame(parent)
        frame.grid(row=row,column=0,sticky="nsew",padx=6,pady=6)
        frame.grid_columnconfigure(0,weight=1); frame.grid_rowconfigure(0,weight=1)
        tree=ttk.Treeview(frame,columns=columns,show="headings")
        for column,width in zip(columns,widths):
            tree.heading(column,text=column)
            tree.column(column,width=width,minwidth=55,anchor="center",stretch=True)
        tree.grid(row=0,column=0,sticky="nsew")
        v=ttk.Scrollbar(frame,orient="vertical",command=tree.yview); v.grid(row=0,column=1,sticky="ns")
        h=ttk.Scrollbar(frame,orient="horizontal",command=tree.xview); h.grid(row=1,column=0,sticky="ew")
        tree.configure(yscrollcommand=v.set,xscrollcommand=h.set)
        return tree

    def build_log(self):
        self.log_tab.grid_columnconfigure(0,weight=1)
        self.log_tab.grid_rowconfigure(0,weight=1)
        columns=("Time","Status","File","Message")
        self.log_tree=ttk.Treeview(self.log_tab,columns=columns,show="headings")
        for col,w in zip(columns,[160,90,320,520]):
            self.log_tree.heading(col,text=col)
            self.log_tree.column(col,width=w,anchor="w")
        self.log_tree.grid(row=0,column=0,sticky="nsew",padx=8,pady=8)

    @staticmethod
    def _parse_iso_date(value, label):
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(f"{label} must be YYYY-MM-DD.") from exc

    def save_penstock_target_date(self):
        try:
            target=self._parse_iso_date(self.penstock_target_date_var.get(),"Target date")
            self.db.save_penstock_setting("target_date",target.isoformat())
            self.load_penstock_control()
            messagebox.showinfo("Target saved",f"Penstock concrete target date: {target:%d %b %Y}")
        except Exception as exc:
            messagebox.showerror("Target date",str(exc))

    def _planned_chainage(self, snapshot):
        if not snapshot or snapshot.get("planned_cumulative") is None:
            return None
        value=float(snapshot["planned_cumulative"] or 0.0)
        unit=str(snapshot.get("unit") or "").lower()
        if "no" in unit or "nr" in unit:
            return min(PENSTOCK_DESIGN_CHAINAGE,max(0.0,value/28.0*PENSTOCK_DESIGN_CHAINAGE))
        return min(PENSTOCK_DESIGN_CHAINAGE,max(0.0,value))

    def load_penstock_control(self):
        try:
            status_date=self._parse_iso_date(self.penstock_status_date_var.get(),"Status date")
            target_date=self._parse_iso_date(self.penstock_target_date_var.get(),"Target date")
            summary=self.db.penstock_target_summary(status_date,target_date)
            grade15=summary["by_strength"].get(15.0,{"daily":0.0})["daily"]
            grade35=summary["by_strength"].get(35.0,{"daily":0.0})["daily"]
            values={
                "remaining":f"{summary['remaining']:,.2f}",
                "days":f"{summary['working_days']}",
                "daily":f"{summary['daily']:,.2f}",
                "cycles":f"{summary['cycles_per_day']:,.2f}",
                "grade15":f"{grade15:,.2f}",
                "grade35":f"{grade35:,.2f}",
            }
            for key,value in values.items(): self.penstock_kpi_labels[key].configure(text=value)
            self.current_penstock_summary=summary
            self._load_penstock_casts()
            self._load_penstock_components()
            self._load_penstock_results()
            self._render_penstock_layout(status_date)
        except Exception as exc:
            messagebox.showerror("Penstock Control",str(exc))

    def _render_penstock_layout(self, status_date):
        for child in self.penstock_layout_frame.winfo_children(): child.destroy()
        if self.penstock_layout_figure is not None: plt.close(self.penstock_layout_figure)
        components=self.db.penstock_components()
        pipe_plan=self.db.penstock_activity_snapshot(25,status_date.isoformat())
        concrete_plan=self.db.penstock_activity_snapshot(24,status_date.isoformat())
        planned_ch=self._planned_chainage(pipe_plan)
        installed=components[(components["installed"]==1)&(components["chainage_length"]>0)]
        installed_length=float(installed["chainage_length"].sum()) if not installed.empty else 0.0
        accepted=components[(components["qc_status"].isin(["Accepted","Recorded"]))&(components["chainage_length"]>0)]
        qc_length=float(accepted["chainage_length"].sum()) if not accepted.empty else 0.0

        fig,(ax,ax2)=plt.subplots(2,1,figsize=(12,4.7),dpi=100,gridspec_kw={"height_ratios":[2.0,1.0]})
        ax.barh([0],[PENSTOCK_DESIGN_CHAINAGE],left=[0],height=.28,color="#D1D5DB",label="Design route")
        if planned_ch is not None and planned_ch>0:
            ax.barh([0],[planned_ch],left=[0],height=.28,color="#F59E0B",alpha=.70,label="Planned position")
        for idx,(_,row) in enumerate(installed.iterrows()):
            ax.barh([0],[row["chainage_length"]],left=[row["start_chainage"]],height=.17,
                    color="#16A34A",label="Installed identified sections" if idx==0 else None)
        for ch,label in [(0,"FINAL"),(70.969,"TEMP"),(244.042,"OUTLET")]:
            ax.axvline(ch,color="#1E3A5F",linestyle="--",linewidth=1.2)
            ax.text(ch,.24,f"{label}\nCH{ch:.3f}",ha="center",va="bottom",fontsize=8,fontweight="bold")
        ax.set_xlim(0,PENSTOCK_DESIGN_CHAINAGE); ax.set_yticks([]); ax.set_xlabel("Chainage (m)")
        plan_text="No Activity 25 programme quantity" if planned_ch is None else f"Planned CH {planned_ch:.3f}"
        ax.set_title(f"Penstock Plan vs Identified Actual - {status_date:%d %b %Y} | {plan_text} | Installed {installed_length:.3f} m | QC recorded/accepted {qc_length:.3f} m")
        ax.grid(True,axis="x",alpha=.2); ax.legend(loc="lower center",ncol=3,bbox_to_anchor=(.5,-.48))

        summary=self.current_penstock_summary
        grade15=summary["by_strength"].get(15.0,{"design":0,"actual":0})
        grade35=summary["by_strength"].get(35.0,{"design":0,"actual":0})
        labels=["15 MPa","35 MPa","All concrete"]
        design=[grade15.get("design",0),grade35.get("design",0),summary["design"]]
        actual=[grade15.get("actual",0),grade35.get("actual",0),summary["actual"]]
        y=range(len(labels)); ax2.barh(y,design,color="#CBD5E1",label="Design m3")
        ax2.barh(y,actual,color="#2563EB",label="Recorded actual m3")
        ax2.set_yticks(list(y),labels); ax2.set_xlabel("Concrete (m3)"); ax2.grid(True,axis="x",alpha=.2)
        activity24="No Activity 24 record"
        if concrete_plan and concrete_plan.get("actual_cumulative") is not None:
            activity24=f"Activity 24 cumulative actual: {float(concrete_plan['actual_cumulative']):,.2f} {concrete_plan.get('unit') or ''}"
        ax2.set_title(f"Casting Register vs Design | {activity24}",fontsize=10)
        ax2.legend(loc="lower right")
        fig.tight_layout()
        self.penstock_layout_figure=fig
        self.penstock_layout_canvas=FigureCanvasTkAgg(fig,master=self.penstock_layout_frame)
        self.penstock_layout_canvas.draw(); self.penstock_layout_canvas.get_tk_widget().pack(fill="both",expand=True,padx=4,pady=4)

    def _load_penstock_casts(self):
        casts=self.db.penstock_casts(); self.current_penstock_casts=casts
        ids=casts["item_id"].tolist() if not casts.empty else ["-"]
        self.penstock_cast_menu.configure(values=ids)
        if self.penstock_cast_item_var.get() not in ids:self.penstock_cast_item_var.set(ids[0])
        self.penstock_cast_tree.delete(*self.penstock_cast_tree.get_children())
        for _,row in casts.iterrows():
            remaining=max(float(row["design_quantity"])-float(row["actual_quantity"]),0.0)
            status="Complete" if remaining<=.0005 else ("In Progress" if float(row["actual_quantity"])>0 else "Not Started")
            tag="complete" if status=="Complete" else ("partial" if status=="In Progress" else "")
            self.penstock_cast_tree.insert("","end",iid=row["item_id"],values=(
                row["item_id"],row["cast_type"],"-" if pd.isna(row["start_chainage"]) else f"{row['start_chainage']:.3f}",
                "-" if pd.isna(row["end_chainage"]) else f"{row['end_chainage']:.3f}",
                f"{row['design_quantity']:.3f}",f"{row['actual_quantity']:.3f}",f"{remaining:.3f}",
                f"{row['strength_mpa']:.0f}",row["cast_date"],row["rn"],status),tags=(tag,))

    def load_selected_penstock_cast(self):
        casts=getattr(self,"current_penstock_casts",self.db.penstock_casts())
        row=casts[casts["item_id"]==self.penstock_cast_item_var.get()]
        if row.empty:return
        item=row.iloc[0]; self.penstock_cast_qty_var.set(f"{item['actual_quantity']:.3f}")
        self.penstock_cast_date_var.set(item["cast_date"] or date.today().isoformat())
        self.penstock_cast_rn_var.set(item["rn"] or ""); self.penstock_cast_notes_var.set(item["notes"] or "")

    def _penstock_cast_tree_selected(self, _event=None):
        selected=self.penstock_cast_tree.selection()
        if selected:
            self.penstock_cast_item_var.set(selected[0]); self.load_selected_penstock_cast()

    def save_penstock_cast(self):
        try:
            cast_date=self.penstock_cast_date_var.get().strip()
            if cast_date:self._parse_iso_date(cast_date,"Cast date")
            self.db.save_penstock_cast(self.penstock_cast_item_var.get(),float(self.penstock_cast_qty_var.get()),
                                        cast_date,self.penstock_cast_rn_var.get(),self.penstock_cast_notes_var.get())
            self.load_penstock_control()
        except Exception as exc:messagebox.showerror("Save cast",str(exc))

    def _load_penstock_components(self):
        df=self.db.penstock_components(); self.current_penstock_components=df
        ids=df["component_id"].tolist() if not df.empty else ["-"]
        self.penstock_component_menu.configure(values=ids)
        if self.penstock_component_var.get() not in ids:self.penstock_component_var.set(ids[0])
        self.penstock_component_tree.delete(*self.penstock_component_tree.get_children())
        for _,row in df.iterrows():
            installed=bool(row["installed"]); tag="installed" if installed else ""
            self.penstock_component_tree.insert("","end",iid=row["component_id"],values=(
                row["component_id"],row["sequence_no"],row["component_name"],row["component_type"],
                f"{row['start_chainage']:.3f}",f"{row['end_chainage']:.3f}",f"{row['chainage_length']:.3f}",
                "Yes" if installed else "No",row["actual_date"],row["rn"],row["qc_status"],row["asbuilt_status"]),tags=(tag,))

    def load_selected_penstock_component(self):
        df=getattr(self,"current_penstock_components",self.db.penstock_components())
        row=df[df["component_id"]==self.penstock_component_var.get()]
        if row.empty:return
        item=row.iloc[0]; self.penstock_installed_var.set(bool(item["installed"]))
        self.penstock_component_date_var.set(item["actual_date"] or date.today().isoformat())
        self.penstock_component_rn_var.set(item["rn"] or ""); self.penstock_qc_var.set(item["qc_status"])
        self.penstock_asbuilt_var.set(item["asbuilt_status"])

    def _penstock_component_tree_selected(self, _event=None):
        selected=self.penstock_component_tree.selection()
        if selected:
            self.penstock_component_var.set(selected[0]); self.load_selected_penstock_component()

    def save_penstock_component(self):
        try:
            actual_date=self.penstock_component_date_var.get().strip()
            if actual_date:self._parse_iso_date(actual_date,"Actual date")
            self.db.save_penstock_component(self.penstock_component_var.get(),self.penstock_installed_var.get(),
                                             actual_date,self.penstock_component_rn_var.get(),self.penstock_qc_var.get(),
                                             self.penstock_asbuilt_var.get())
            self.load_penstock_control()
        except Exception as exc:messagebox.showerror("Save component",str(exc))

    def _load_penstock_results(self):
        df=self.db.penstock_results(); self.current_penstock_results=df
        cast_ids=self.db.penstock_casts()["item_id"].tolist()
        self.penstock_results_tree.delete(*self.penstock_results_tree.get_children())
        for _,row in df.iterrows():
            tag="pass" if row["status"]=="PASS" else ("fail" if row["status"]=="FAIL" else "monitor")
            self.penstock_results_tree.insert("","end",iid=row["sample_id"],values=(
                row["sample_id"],row["cast_item"],row["structure"],row["chainage"],row["rn"],
                row["cast_date"],row["test_date"],row["age_days"],f"{row['required_mpa']:.1f}",
                f"{row['result_mpa']:.1f}",row["status"],row["lab_reference"]),tags=(tag,))

    def save_penstock_result(self):
        try:
            self.db.save_penstock_result(
                self.result_sample_var.get(),self.result_cast_item_var.get(),self.result_structure_var.get(),
                self.result_chainage_var.get(),self.result_rn_var.get(),self.result_cast_date_var.get(),
                self.result_test_date_var.get(),float(self.result_required_var.get()),float(self.result_mpa_var.get()),
                self.result_lab_ref_var.get(),self.result_notes_var.get())
            self._load_penstock_results(); self.result_sample_var.set(""); self.result_mpa_var.set("")
        except Exception as exc:messagebox.showerror("Save concrete result",str(exc))

    def delete_penstock_result(self):
        selected=self.penstock_results_tree.selection()
        if not selected:return
        sample_id=selected[0]
        if messagebox.askyesno("Delete result",f"Delete concrete result {sample_id}?"):
            self.db.delete_penstock_result(sample_id); self._load_penstock_results()

    def export_penstock_summary(self):
        if not hasattr(self,"current_penstock_summary"):return
        status_date=self.penstock_status_date_var.get(); target_date=self.penstock_target_date_var.get()
        s=self.current_penstock_summary
        rows=[
            {"Metric":"Status date","Value":status_date,"Unit":""},
            {"Metric":"Target date","Value":target_date,"Unit":""},
            {"Metric":"Design concrete","Value":s["design"],"Unit":"m3"},
            {"Metric":"Recorded actual","Value":s["actual"],"Unit":"m3"},
            {"Metric":"Remaining","Value":s["remaining"],"Unit":"m3"},
            {"Metric":"Working days","Value":s["working_days"],"Unit":"days"},
            {"Metric":"Required daily","Value":s["daily"],"Unit":"m3/day"},
            {"Metric":"9 m3 cycles per day","Value":s["cycles_per_day"],"Unit":"casts/day"},
        ]
        self.save_csv(pd.DataFrame(rows),"Penstock_Target_Summary.csv")

    def sync_async(self):
        if self.syncing:
            return
        self.syncing=True
        self.status.configure(text="Reading TASK LIST and TASK PROG tabs...")
        def worker():
            files,missing=discover_files()
            imported=unchanged=skipped=0
            notes=[]
            for order,period,path in files:
                if self.db.is_current(path):
                    unchanged+=1
                    continue
                ok,msg=self.db.import_workbook(order,period,path)
                if ok:imported+=1
                else:skipped+=1
                notes.append(f"{path.name}: {msg}")
            self.after(0,lambda:self.sync_done(imported,unchanged,skipped,missing,notes))
        threading.Thread(target=worker,daemon=True).start()

    def sync_done(self,imported,unchanged,skipped,missing,notes):
        self.syncing=False
        self.status.configure(text=f"Imported/updated: {imported}\nUnchanged: {unchanged}\nSkipped: {skipped}")
        self.refresh_all()
        messagebox.showinfo("Synchronisation complete",
                            f"Imported/updated: {imported}\nUnchanged: {unchanged}\nSkipped: {skipped}")

    def refresh_all(self):
        periods=["All periods"]+self.db.periods()
        self.period_menu.configure(values=periods)
        if self.period_var.get() not in periods:self.period_var.set("All periods")
        self.refresh_dates()
        self.refresh_activities()
        self.load_timeline()
        self.load_league()
        self.load_penstock_control()
        self.load_log()

    def period_changed(self):
        self.refresh_dates()
        self.refresh_activities()
        self.load_timeline()
        self.load_league()

    def refresh_dates(self):
        dates=self.db.dates(self.period_var.get())
        values=dates or ["-"]
        self.date_menu.configure(values=values)
        self.date_var.set(values[-1])
        self.load_summary()

    def refresh_activities(self):
        rows=self.db.activities(self.period_var.get())
        unique={}
        for row in rows:
            no=row["management_activity_no"]
            if no is None:
                continue
            unique[int(no)]=row["task_description"]
        labels=[]
        self.activity_lookup={}
        for no in sorted(unique):
            label=f"{no}. {unique[no]}"
            labels.append(label)
            self.activity_lookup[label]=unique[no]
        labels=labels or ["-"]
        self.activity_menu.configure(values=labels)
        if self.activity_var.get() not in labels:
            self.activity_var.set(labels[0])
        self.load_production()

    def load_office_inputs(self, report_date):
        office = self.db.get_office_labour(report_date)
        self.office_use_default_var.set(bool(office.get('use_default',1)))
        self.office_hours_var.set(f"{office['actual_hours']:g}")
        self.office_factor_var.set(f"{office['overtime_factor']:g}")
        self.office_day_type_var.set(office.get('day_type','Normal Working Day'))
        self.office_notes_var.set(office.get('notes',''))
        self.toggle_office_default()
        self.office_result_var.set(
            f"Effective: {office['effective_hours']:g} h | {money(office['daily_cost'])}"
        )
        return office

    def save_office_labour(self):
        report_date = self.date_var.get()
        if not report_date or report_date == "-":
            return
        try:
            hours = float(self.office_hours_var.get().replace(",", "."))
            factor = float(self.office_factor_var.get())
            if hours < 0:
                raise ValueError("Hours cannot be negative")
        except ValueError as exc:
            messagebox.showerror("Office labour", f"Enter valid hours and factor.\n\n{exc}")
            return
        use_default = bool(self.office_use_default_var.get())
        cost = self.db.save_office_labour(
            report_date,use_default,hours,factor,
            self.office_day_type_var.get(),self.office_notes_var.get().strip()
        )
        self.office_result_var.set(
            f"Effective: {hours * factor:g} h | {money(cost)}"
        )
        self.load_summary()

    def toggle_office_default(self):
        state="disabled" if self.office_use_default_var.get() else "normal"
        self.office_hours_entry.configure(state=state)
        self.office_factor_menu.configure(state=state)
        if self.office_use_default_var.get() and self.date_var.get() not in ("","-"):
            hours,factor=self.db.default_office_hours(self.date_var.get())
            self.office_hours_var.set(f"{hours:g}");self.office_factor_var.set(f"{factor:g}")

    def refresh_comment_activities(self, df):
        values=[]
        for _,row in df.iterrows():
            values.append(
                f"{int(row['management_activity_no'])}. "
                f"{row['management_activity_name']}"
            )

        menu_values = values or ["-"]
        self.comment_activity_menu.configure(values=menu_values)
        self.foreman_activity_menu.configure(values=menu_values)

        if values:
            if self.comment_activity_var.get() not in values:
                self.comment_activity_var.set(values[0])
            if self.foreman_activity_var.get() not in values:
                self.foreman_activity_var.set(values[0])
            self.load_selected_comment()
            self.load_selected_foreman()

    def load_selected_comment(self):
        label=self.comment_activity_var.get()
        if label in ("","-") or self.date_var.get() in ("","-"):return
        activity_no=int(label.split('.',1)[0])
        self.comment_text_var.set(self.db.activity_comment(self.date_var.get(),activity_no))

    def save_activity_comment(self):
        label=self.comment_activity_var.get()
        if label in ("","-") or self.date_var.get() in ("","-"):return
        activity_no=int(label.split('.',1)[0])
        self.db.save_activity_comment(self.date_var.get(),activity_no,self.comment_text_var.get().strip())
        self.load_summary()


    def load_selected_foreman(self):
        label=self.foreman_activity_var.get()
        if label in ("","-"):
            self.foreman_name_var.set("")
            return
        activity_no=int(label.split('.',1)[0])
        self.foreman_name_var.set(
            self.db.activity_foreman(activity_no)
        )

    def save_activity_foreman(self):
        label=self.foreman_activity_var.get()
        if label in ("","-"):
            return
        activity_no=int(label.split('.',1)[0])
        foreman=self.foreman_name_var.get().strip()
        self.db.save_activity_foreman(activity_no,foreman)
        self.load_summary()

    def league_week_dates(self):
        reference=datetime.strptime(
            self.league_reference_var.get(),"%Y-%m-%d"
        ).date()
        start,end=self.db.league_week_bounds(reference)
        dates=[]
        current=start
        while current<=end:
            dates.append(current.isoformat())
            current+=timedelta(days=1)
        return start,end,dates

    def load_league_target_editor(self):
        target_date=self.league_target_date_var.get()
        if target_date in ("","-"):
            return
        team=self.league_team_var.get()
        target,reason,_edited=self.db.target_for_day(team,target_date)
        self.league_target_qty_var.set(f"{target:g}")
        self.league_target_reason_var.set(reason)

    def save_league_target(self):
        try:
            target=float(self.league_target_qty_var.get().replace(",","."))
            if target<0:
                raise ValueError("Target cannot be negative.")
        except ValueError as exc:
            messagebox.showerror("League target",str(exc))
            return
        self.db.save_target_override(
            self.league_team_var.get(),
            self.league_target_date_var.get(),
            target,
            self.league_target_reason_var.get().strip()
        )
        self.load_league()

    def restore_league_target(self):
        self.db.clear_target_override(
            self.league_team_var.get(),
            self.league_target_date_var.get()
        )
        self.load_league()

    def load_league(self):
        try:
            reference=datetime.strptime(
                self.league_reference_var.get(),"%Y-%m-%d"
            ).date()
        except ValueError:
            messagebox.showerror("Team League","Use YYYY-MM-DD.")
            return

        standings,daily,start,end=self.db.league_standings(
            reference.isoformat(),self.period_var.get()
        )
        self.current_league=standings
        self.current_league_daily=daily
        self.league_week_label.configure(
            text=(
                f"Production Champions League: Thu {start.strftime('%d %b %Y')} "
                f"to Wed {end.strftime('%d %b %Y')} | "
                f"Status to {reference.strftime('%a %d %b %Y')} | "
                f"Winners presented Fri {(end+timedelta(days=2)).strftime('%d %b %Y')}"
            )
        )

        dates=[]
        current=start
        while current<=end:
            dates.append(current.isoformat())
            current+=timedelta(days=1)
        self.league_target_date_menu.configure(values=dates)
        if self.league_target_date_var.get() not in dates:
            self.league_target_date_var.set(dates[0])
        self.load_league_target_editor()

        self.league_tree.delete(*self.league_tree.get_children())
        for _,row in standings.iterrows():
            tag="qualified" if row["PTS"]>=100 else "notyet"
            gd_arrow = "▲" if row["GD"] > 0 else ("▼" if row["GD"] < 0 else "—")
            self.league_tree.insert("","end",values=(
                f"{int(row['No']):02d}",
                row["Team"],
                qty(row["Plan"]),
                qty(row["Actual"]),
                f"{gd_arrow} {row['GD']:+.0f}%",
                f"{row['PTS']:.0f}%",
                row["Incentive"]
            ),tags=(tag,))

        self.league_daily_tree.delete(*self.league_daily_tree.get_children())
        for _,row in daily.sort_values(["Date","Team"]).iterrows():
            counts_in_score = (
                "YES" if row["Date"] <= reference else "FUTURE"
            )
            self.league_daily_tree.insert("","end",values=(
                row["Date"].strftime("%Y-%m-%d"),row["Team"],
                qty(row["Target"]),qty(row["Actual"]),
                f"{row['Variance']:,.0f}",
                counts_in_score,
                "YES" if row["Target Edited"] else "NO",
                row["Reason"]
            ))
        self.render_league_graphs()

    def refresh_league_team_tables(self):
        """Refresh each team's daily and cumulative table for graph date range."""
        if not hasattr(self, "league_team_trees"):
            return
        try:
            datetime.strptime(self.graph_start_var.get(), "%Y-%m-%d")
            datetime.strptime(self.graph_end_var.get(), "%Y-%m-%d")
        except ValueError:
            return

        for team_name, tree in self.league_team_trees.items():
            df = self.db.team_graph_data(
                team_name,
                self.graph_start_var.get(),
                self.graph_end_var.get(),
                self.period_var.get()
            )
            tree.delete(*tree.get_children())
            if df.empty:
                continue

            cumulative_variance = (
                df["Cumulative Actual"] - df["Cumulative Target"]
            )
            for idx, (_, row) in enumerate(df.iterrows()):
                daily_variance = float(row["Actual"] - row["Target"])
                cum_variance = float(cumulative_variance.loc[row.name])
                achievement = float(row["Achievement"] or 0.0)
                if cum_variance > 0:
                    tag = "ahead"
                elif cum_variance < 0:
                    tag = "behind"
                else:
                    tag = "even"
                tree.insert("", "end", values=(
                    row["Date"].strftime("%Y-%m-%d"),
                    qty(row["Target"]),
                    qty(row["Actual"]),
                    f"{daily_variance:,.0f}",
                    qty(row["Cumulative Target"]),
                    qty(row["Cumulative Actual"]),
                    f"{cum_variance:,.0f}",
                    f"{achievement:.1f}%"
                ), tags=(tag,))

    def render_league_graphs(self):
        try:
            datetime.strptime(self.graph_start_var.get(),"%Y-%m-%d")
            datetime.strptime(self.graph_end_var.get(),"%Y-%m-%d")
        except ValueError:
            messagebox.showerror("League graphs","Use YYYY-MM-DD.")
            return

        self.refresh_league_team_tables()

        df=self.db.team_graph_data(
            self.league_team_var.get(),
            self.graph_start_var.get(),
            self.graph_end_var.get(),
            self.period_var.get()
        )
        if hasattr(self,"league_canvas") and self.league_canvas:
            self.league_canvas.get_tk_widget().destroy()
        if hasattr(self,"league_toolbar") and self.league_toolbar:
            self.league_toolbar.destroy()
        if hasattr(self,"league_figure") and self.league_figure:
            plt.close(self.league_figure)

        fig,axes=plt.subplots(2,1,figsize=(11.5,7.0),dpi=100)
        marker_size=6 if getattr(self, "_tv_mode", False) else 3

        axes[0].plot(
            df["Date"],df["Target"],marker="o",markersize=marker_size,
            linewidth=(2.2 if getattr(self, "_tv_mode", False) else 1.3),label="Planned / Target"
        )
        axes[0].plot(
            df["Date"],df["Actual"],marker="o",markersize=marker_size,
            linewidth=(2.2 if getattr(self, "_tv_mode", False) else 1.3),label="Actual"
        )

        # Fixed programme minimum requirement for the selected production team.
        # This red reference line is independent of editable league targets.
        selected_team = self.league_team_var.get()
        programme_minimum = self.db.TEAM_PROGRAMME_MINIMUMS.get(selected_team)
        if programme_minimum is not None:
            axes[0].axhline(
                y=programme_minimum,
                color="red",
                linestyle="--",
                linewidth=2.0,
                label=f"Programme Minimum ({programme_minimum:,.0f} m³/day)"
            )

        axes[0].set_title(
            f"{selected_team} — Daily Planned vs Actual"
        )
        axes[0].set_ylabel("m³")
        axes[0].grid(True,alpha=.25)
        axes[0].legend()
        axes[0].tick_params(axis="x",rotation=35)

        axes[1].plot(
            df["Date"],df["Cumulative Target"],marker="o",
            markersize=marker_size,linewidth=(2.2 if getattr(self, "_tv_mode", False) else 1.3),label="Cumulative Target"
        )
        axes[1].plot(
            df["Date"],df["Cumulative Actual"],marker="o",
            markersize=marker_size,linewidth=(2.2 if getattr(self, "_tv_mode", False) else 1.3),label="Cumulative Actual"
        )
        axes[1].set_title("Progressive Cumulative Production")
        axes[1].set_ylabel("m³")
        axes[1].grid(True,alpha=.25)
        axes[1].legend()
        axes[1].tick_params(axis="x",rotation=35)

        fig.tight_layout()
        self.league_figure=fig
        self.league_canvas=FigureCanvasTkAgg(
            fig,master=self.league_graph_frame
        )
        self.league_canvas.draw()
        widget=self.league_canvas.get_tk_widget()
        widget.pack(fill="both",expand=True,padx=5,pady=(5,0))
        self.league_toolbar=NavigationToolbar2Tk(
            self.league_canvas,self.league_graph_frame,pack_toolbar=False
        )
        self.league_toolbar.update()
        self.league_toolbar.pack(fill="x",padx=5,pady=(0,5))

    def export_league_csv(self):
        if hasattr(self,"current_league"):
            self.save_csv(
                self.current_league,
                "Production_Champions_League_Weekly.csv"
            )

    def export_league_png(self):
        if not hasattr(self,"current_league"):
            self.load_league()
        if not hasattr(self,"current_league"):
            return
        df=self.current_league
        rows=[]
        for _,r in df.iterrows():
            arrow = "▲" if r["GD"] > 0 else ("▼" if r["GD"] < 0 else "—")
            rows.append([
                f"{int(r['No']):02d}", r["Team"], qty(r["Plan"]),
                qty(r["Actual"]), f"{arrow} {r['GD']:+.0f}%",
                f"{r['PTS']:.0f}%", r["Incentive"]
            ])
        start,end,_dates=self.league_week_dates()
        self.table_png(
            f"Production Champions League | "
            f"{start.strftime('%d %b %Y')} to {end.strftime('%d %b %Y')}",
            ["NO","TEAM NAME","PLAN","ACTUAL","GD","PTS","INCENTIVE"],
            rows,"Production_Champions_League.png",
            [.08,.25,.15,.15,.12,.12,.13]
        )

    def load_summary(self):
        report_date=self.date_var.get()
        if report_date=="-":return
        df=self.db.daily_summary(report_date,self.period_var.get())
        office = self.load_office_inputs(report_date)
        self.current_summary=df
        self.current_office=office
        self.refresh_comment_activities(df)
        self.summary_heading.configure(
            text=f"Site Engineer: Kgali Motumi     Date: {pd.to_datetime(report_date).strftime('%d %b %y')}"
        )
        display_rows=[]
        daily_total=0.0
        cumulative_total=0.0

        for _,row in df.iterrows():
            daily = (
                0.0
                if pd.isna(row["daily_profit_loss"])
                else float(row["daily_profit_loss"])
            )
            monthly = (
                0.0
                if pd.isna(row["cumulative_profit_loss"])
                else float(row["cumulative_profit_loss"])
            )
            daily_total += daily
            cumulative_total += monthly

            # Columns 1–3 follow the Daily +Profit/Loss sign.
            # Each financial value is independently coloured.
            display_rows.append({
                "values": [
                    int(row["management_activity_no"]),
                    row["management_activity_name"],
                    row["responsible_foreman"],
                    money(row["daily_profit_loss"]),
                    money(row["cumulative_profit_loss"]),
                    money(row["cumulative_profit_loss"]),
                    row["comments"],
                ],
                "signs": [
                    daily, daily, daily,
                    daily, monthly, monthly,
                    None,
                ],
            })

        office_cost=float(office["daily_cost"])
        cumulative_total += office_cost

        # v3.9.26: use verified financial roll-forward at period crossover.
        closing_override = getattr(self.db, "_daily_summary_closing_override", None)
        if closing_override is not None:
            cumulative_total = float(closing_override)
        office_comment=office.get("day_type","")
        if office.get("notes"):
            office_comment += (
                " — " if office_comment else ""
            ) + office.get("notes","")

        display_rows.append({
            "values": [
                "", "", "Office labour", "R -",
                money(office_cost), money(office_cost), office_comment,
            ],
            # Daily P/L is blank, therefore the first three and daily cell
            # remain white. Monthly and total cells follow office cost.
            "signs": [
                0, 0, 0, 0,
                office_cost, office_cost,
                None,
            ],
        })

        display_rows.append({
            "values": [
                "", "Cummulative", "",
                money(daily_total),
                money(cumulative_total),
                money(cumulative_total),
                "",
            ],
            "signs": [
                daily_total, daily_total, daily_total,
                daily_total, cumulative_total, cumulative_total,
                None,
            ],
            "bold": True,
        })

        self.summary_grid.render(display_rows)

    def _validated_no_work_range(self):
        start_text = self.no_work_start_var.get().strip()
        end_text = self.no_work_end_var.get().strip()
        if not start_text or not end_text:
            raise ValueError("Enter both From and To dates as YYYY-MM-DD.")
        start = datetime.strptime(start_text, "%Y-%m-%d").date()
        end = datetime.strptime(end_text, "%Y-%m-%d").date()
        if end < start:
            raise ValueError("The To date cannot be before the From date.")
        return start.isoformat(), end.isoformat()

    def apply_no_work_override(self):
        try:
            start_text, end_text = self._validated_no_work_range()
            reason = self.no_work_reason_var.get().strip() or "No work"
            self.db.set_no_work_range(start_text, end_text, reason)
            self.load_production()
            self.load_timeline()
            messagebox.showinfo(
                "No-work dates applied",
                f"Planned production set to 0 m³/day\n"
                f"From: {start_text}\nTo: {end_text}\nReason: {reason}"
            )
        except Exception as exc:
            messagebox.showerror("No-work override", str(exc))

    def restore_no_work_override(self):
        try:
            start_text, end_text = self._validated_no_work_range()
            self.db.clear_no_work_range(start_text, end_text)
            self.load_production()
            self.load_timeline()
            messagebox.showinfo(
                "Normal plan restored",
                f"Removed no-work overrides from {start_text} to {end_text}."
            )
        except Exception as exc:
            messagebox.showerror("Restore normal plan", str(exc))

    def load_production(self):
        label=self.activity_var.get()
        if label=="-":
            return
        description=self.activity_lookup[label]
        raw_df=self.db.activity_history(description,self.period_var.get())
        if raw_df.empty:
            return

        try:
            selected_activity_no = int(str(label).split(".", 1)[0].strip())
        except (TypeError, ValueError):
            selected_activity_no = None

        # Apply any active management recovery milestone as a display-only plan.
        # The source SQL programme remains untouched.
        df, recovery_info = apply_production_recovery_target(
            raw_df, selected_activity_no
        )
        self.current_recovery_info = recovery_info

        if not self.production_graph_start_var.get():
            self.production_graph_start_var.set(
                df["report_date"].min().strftime("%Y-%m-%d")
            )
        if not self.production_graph_end_var.get():
            self.production_graph_end_var.set(
                df["report_date"].max().strftime("%Y-%m-%d")
            )

        self.current_production=df
        self.production_heading.configure(text=f"Activity: {label}")
        self.production_tree.delete(*self.production_tree.get_children())
        no_work = self.db.no_work_dates()
        for _,row in df.iterrows():
            variance=None
            if not pd.isna(row["planned_daily"]) or not pd.isna(row["actual_daily"]):
                variance=(
                    (0 if pd.isna(row["actual_daily"]) else row["actual_daily"])
                    -(0 if pd.isna(row["planned_daily"]) else row["planned_daily"])
                )
            row_date_text = row["report_date"].strftime("%Y-%m-%d")
            if row_date_text in no_work:
                tag = "no_work"
            else:
                tag="" if variance is None else ("above" if variance>=0 else "below")
            self.production_tree.insert("","end",values=(
                row["report_date"].strftime("%d %b %Y"),
                qty(row["planned_daily"]),qty(row["actual_daily"]),
                qty(row["planned_cumulative"]),qty(row["actual_cumulative"]),
                "-" if variance is None else f"{variance:,.0f}"
            ),tags=(tag,))

        chart_df=df.copy()
        try:
            start=pd.to_datetime(self.production_graph_start_var.get())
            end=pd.to_datetime(self.production_graph_end_var.get())
            chart_df=chart_df[
                (chart_df["report_date"]>=start)
                &(chart_df["report_date"]<=end)
            ]
        except Exception:
            pass
        self.render_chart(chart_df)

    def render_chart(self,df):
        if self.current_canvas:
            self.current_canvas.get_tk_widget().destroy()
        if hasattr(self,"production_toolbar") and self.production_toolbar:
            self.production_toolbar.destroy()
        if self.current_figure:
            plt.close(self.current_figure)

        fig,axes=plt.subplots(1,2,figsize=(10.5,3.4),dpi=100)
        marker_size=6 if getattr(self, "_tv_mode", False) else 3
        axes[0].plot(
            df["report_date"],df["planned_daily"],marker="o",
            markersize=marker_size,linewidth=(2.2 if getattr(self, "_tv_mode", False) else 1.2),label="Planned"
        )
        axes[0].plot(
            df["report_date"],df["actual_daily"],marker="o",
            markersize=marker_size,linewidth=(2.2 if getattr(self, "_tv_mode", False) else 1.2),label="Actual"
        )

        # Fixed programme minimum requirements for selected Daily Production
        # activities. These reference lines are independent of the programme's
        # daily planned quantity and are shown only on the Daily Quantity graph.
        activity_minimums = {
            7: 2100.0,   # Processing fill
            15: 1000.0,  # Waste Rock L/H
            16: 1000.0,  # Waste Rock Process
        }
        selected_label = self.activity_var.get()
        try:
            selected_activity_no = int(str(selected_label).split(".", 1)[0].strip())
        except (TypeError, ValueError):
            selected_activity_no = None
        programme_minimum = activity_minimums.get(selected_activity_no)
        if programme_minimum is not None:
            axes[0].axhline(
                y=programme_minimum,
                color="red",
                linestyle="--",
                linewidth=2.0,
                label=f"Programme Minimum ({programme_minimum:,.0f} m³/day)"
            )

        axes[0].set_title("Daily Quantity")
        axes[0].grid(True,alpha=.25)
        axes[0].legend()

        axes[1].plot(
            df["report_date"],df["planned_cumulative"],marker="o",
            markersize=marker_size,linewidth=(2.2 if getattr(self, "_tv_mode", False) else 1.2),label="Planned"
        )
        axes[1].plot(
            df["report_date"],df["actual_cumulative"],marker="o",
            markersize=marker_size,linewidth=(2.2 if getattr(self, "_tv_mode", False) else 1.2),label="Actual"
        )

        # Active cumulative milestone reference (e.g. 166,328 m³ by 25 Aug).
        recovery_info = getattr(self, "current_recovery_info", None)
        if recovery_info is not None:
            milestone = float(recovery_info["cumulative_target"])
            deadline = recovery_info["deadline"]
            axes[1].axhline(
                y=milestone,
                color="red",
                linestyle=":",
                linewidth=1.8,
                label=f"{deadline.strftime('%d %b')} Target ({milestone:,.0f} m³)"
            )
        axes[1].set_title("Total to Date")
        axes[1].grid(True,alpha=.25)
        axes[1].legend()

        for ax in axes:
            ax.tick_params(axis="x",rotation=35)
        fig.tight_layout()

        self.current_figure=fig
        self.current_canvas=FigureCanvasTkAgg(fig,master=self.chart_frame)
        self.current_canvas.draw()
        self.current_canvas.get_tk_widget().pack(
            fill="both",expand=True,padx=5,pady=(5,0)
        )
        self.production_toolbar=NavigationToolbar2Tk(
            self.current_canvas,self.chart_frame,pack_toolbar=False
        )
        self.production_toolbar.update()
        self.production_toolbar.pack(fill="x",padx=5,pady=(0,3))

    def load_timeline(self):
        df=self.db.timeline(self.period_var.get())
        self.current_timeline=df
        self.timeline_tree.delete(*self.timeline_tree.get_children())
        for _,row in df.iterrows():
            self.timeline_tree.insert("", "end", values=(
                row["Date"].strftime("%Y-%m-%d"),row["Period"],row["Task_Code"],
                row["Activity"],row["Unit"],qty(row["Planned_Daily"]),
                qty(row["Actual_Daily"]),qty(row["Planned_Total"]),
                qty(row["Actual_Total"]),money(row["Daily_PL"])
            ))

    def load_log(self):
        self.log_tree.delete(*self.log_tree.get_children())
        with self.db.connect() as con:
            rows=con.execute(
                "SELECT imported_at,status,file_path,message FROM import_log ORDER BY id DESC LIMIT 250"
            ).fetchall()
        for row in rows:
            self.log_tree.insert("", "end", values=(
                row["imported_at"],row["status"],Path(row["file_path"]).name,row["message"]
            ))

    def export_summary_csv(self):
        if not hasattr(self,"current_summary"):
            return
        df = self.current_summary.copy()
        office = getattr(self, "current_office", self.db.get_office_labour(self.date_var.get()))
        office_row = pd.DataFrame([{
            "management_activity_no": "OFFICE",
            "management_activity_name": "Office labour",
            "responsible_foreman": "",
            "daily_profit_loss": None,
            "cumulative_profit_loss": office["daily_cost"],
            "comments": "",
        }])
        self.save_csv(pd.concat([df, office_row], ignore_index=True), "Daily_Summary.csv")

    def export_production_csv(self):
        if hasattr(self,"current_production"):
            self.save_csv(self.current_production,"Daily_Production.csv")

    def export_timeline_csv(self):
        if hasattr(self,"current_timeline"):
            self.save_csv(self.current_timeline,"All_Activities_Timeline.csv")

    def save_csv(self,df,default):
        path=filedialog.asksaveasfilename(defaultextension=".csv",
              filetypes=[("CSV","*.csv")],initialfile=default)
        if path:
            df.to_csv(path,index=False)
            messagebox.showinfo("Export complete",f"Saved:\n{path}")

    def export_summary_png(self):
        if not hasattr(self,"current_summary"):return
        df=self.current_summary.reset_index(drop=True)
        office = getattr(self, "current_office", self.db.get_office_labour(self.date_var.get()))
        rows=[]
        daily_total=cumulative_total=0
        for _,row in df.iterrows():
            daily = 0 if pd.isna(row["daily_profit_loss"]) else float(row["daily_profit_loss"])
            cumulative = 0 if pd.isna(row["cumulative_profit_loss"]) else float(row["cumulative_profit_loss"])
            daily_total += daily
            cumulative_total += cumulative
            rows.append([
                int(row["management_activity_no"]),
                row["management_activity_name"],
                row["responsible_foreman"],
                money(row["daily_profit_loss"]),
                money(row["cumulative_profit_loss"]),
                money(row["cumulative_profit_loss"]),
                row["comments"]
            ])

        office_cost = float(office["daily_cost"])
        closing_override = getattr(self.db, "_daily_summary_closing_override", None)
        # Exclude office labour from Daily +Profit/Loss cumulative only.
        cumulative_total += office_cost
        if closing_override is not None:
            cumulative_total = float(closing_override)
        rows.append(["", "", "Office labour", "R -", money(office_cost), money(office_cost), ""])
        rows.append(["","Cummulative","",money(daily_total),money(cumulative_total),money(cumulative_total),""])
        row_signs=[]
        for _,row in df.iterrows():
            daily=0.0 if pd.isna(row["daily_profit_loss"]) else float(row["daily_profit_loss"])
            monthly=0.0 if pd.isna(row["cumulative_profit_loss"]) else float(row["cumulative_profit_loss"])
            row_signs.append([daily,daily,daily,daily,monthly,monthly,None])
        row_signs.append([0,0,0,0,office_cost,office_cost,None])
        row_signs.append([
            daily_total,daily_total,daily_total,
            daily_total,cumulative_total,cumulative_total,None
        ])

        self.table_png(
            self.summary_heading.cget("text"),
            ["Activity No.","Task Description","Responsible Foreman",
             "Daily +Profit/Loss","Monthly Totals","Total to date","Comments"],
            rows,
            "Daily_Summary.png",
            [.08,.24,.15,.13,.13,.13,.20],
            cell_signs=row_signs,
            total_row_index=len(rows)-1,
        )

    def export_production_png(self):
        if not hasattr(self,"current_production"):return
        df=self.current_production.tail(20)
        rows=[[r["report_date"].strftime("%d %b %Y"),qty(r["planned_daily"]),
               qty(r["actual_daily"]),qty(r["planned_cumulative"]),
               qty(r["actual_cumulative"])] for _,r in df.iterrows()]
        self.table_png(self.production_heading.cget("text"),
                       ["Date","Planned - Daily Quantity","Actual - Daily Quantity",
                        "Planned - Total to Date","Actual - Total to Date"],
                       rows,"Daily_Production.png",[.14,.21,.21,.22,.22])

    def table_png(
        self,title,headers,rows,default,widths,
        money_columns=(),total_row_index=None,cell_signs=None
    ):
        height=max(4.5,2.2+len(rows)*.38)
        fig,ax=plt.subplots(figsize=(15,height),dpi=180)
        ax.axis("off")
        ax.text(0,1.04,title,transform=ax.transAxes,fontsize=15,fontweight="bold")
        table=ax.table(cellText=rows,colLabels=headers,cellLoc="center",colLoc="center",
                       loc="upper left",bbox=[0,.02,1,.94],colWidths=widths)
        table.auto_set_font_size(False);table.set_fontsize(8.5);table.scale(1,1.3)
        for (r,c),cell in table.get_celld().items():
            if r==0:
                cell.set_text_props(fontweight="bold")
                cell.set_height(cell.get_height()*1.3)
                cell.set_facecolor("#D1D5DB")
            else:
                data_index = r - 1
                if total_row_index is not None and data_index == total_row_index:
                    cell.set_facecolor("#E2E8F0")
                    cell.set_text_props(fontweight="bold")
                elif cell_signs is not None:
                    sign=cell_signs[data_index][c]
                    if sign is None:
                        cell.set_facecolor("#FFFFFF")
                        cell.set_text_props(color="#111827")
                    elif float(sign)<0:
                        cell.set_facecolor("#FECACA")
                        cell.set_text_props(color="#991B1B")
                    elif float(sign)>0:
                        cell.set_facecolor("#BBF7D0")
                        cell.set_text_props(color="#166534")
                    else:
                        cell.set_facecolor("#FFFFFF")
                        cell.set_text_props(color="#111827")
                elif c in money_columns:
                    raw=rows[data_index][c]
                    if is_negative_money(raw):
                        cell.set_facecolor("#FECACA")
                        cell.set_text_props(color="#991B1B")
                    elif raw not in ("", "-", "R -", "R 0.00", "-R 0.00"):
                        cell.set_facecolor("#BBF7D0")
                        cell.set_text_props(color="#166534")
                    else:
                        cell.set_facecolor("#FFFFFF")
                        cell.set_text_props(color="#111827")
            if c in (1,2,len(headers)-1):
                cell.set_text_props(ha="left")
        fig.tight_layout()
        path=filedialog.asksaveasfilename(defaultextension=".png",
              filetypes=[("PNG","*.png")],initialfile=default)
        if path:
            fig.savefig(path,bbox_inches="tight")
            messagebox.showinfo("Export complete",f"Saved:\n{path}")
        plt.close(fig)

    def export_chart_png(self):
        if not hasattr(self,"current_production"):return
        df=self.current_production
        fig,axes=plt.subplots(2,1,figsize=(13,8),dpi=180)
        axes[0].plot(df["report_date"],df["planned_daily"],marker="o",label="Planned")
        axes[0].plot(df["report_date"],df["actual_daily"],marker="o",label="Actual")

        activity_minimums = {7: 2100.0, 15: 1000.0, 16: 1000.0}
        selected_label = self.activity_var.get()
        try:
            selected_activity_no = int(str(selected_label).split(".", 1)[0].strip())
        except (TypeError, ValueError):
            selected_activity_no = None
        programme_minimum = activity_minimums.get(selected_activity_no)
        if programme_minimum is not None:
            axes[0].axhline(
                y=programme_minimum, color="red", linestyle="--", linewidth=2.0,
                label=f"Programme Minimum ({programme_minimum:,.0f} m³/day)"
            )

        axes[0].set_title("Daily Quantity");axes[0].grid(True,alpha=.25);axes[0].legend()
        axes[1].plot(df["report_date"],df["planned_cumulative"],marker="o",label="Planned")
        axes[1].plot(df["report_date"],df["actual_cumulative"],marker="o",label="Actual")
        recovery_info = getattr(self, "current_recovery_info", None)
        if recovery_info is not None:
            milestone = float(recovery_info["cumulative_target"])
            deadline = recovery_info["deadline"]
            axes[1].axhline(
                y=milestone, color="red", linestyle=":", linewidth=1.8,
                label=f"{deadline.strftime('%d %b')} Target ({milestone:,.0f} m³)"
            )
        axes[1].set_title("Total to Date");axes[1].grid(True,alpha=.25);axes[1].legend()
        for ax in axes:ax.tick_params(axis="x",rotation=35)
        fig.suptitle(self.production_heading.cget("text"),fontsize=16,fontweight="bold")
        fig.tight_layout()
        path=filedialog.asksaveasfilename(defaultextension=".png",
              filetypes=[("PNG","*.png")],initialfile="Daily_Production_Chart.png")
        if path:
            fig.savefig(path,bbox_inches="tight")
            messagebox.showinfo("Export complete",f"Saved:\n{path}")
        plt.close(fig)

    def open_exports(self):
        EXPORT_DIR.mkdir(exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(EXPORT_DIR)


if __name__=="__main__":
    try:
        App().mainloop()
    except Exception:
        error=traceback.format_exc()
        try:messagebox.showerror("Fatal error",error)
        except Exception:print(error)
        raise
