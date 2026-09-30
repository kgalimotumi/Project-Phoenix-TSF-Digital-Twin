# Tweefontein Production Manager SQL v2.2

This version does **not** read the existing `Daily Summary` or `Daily Production` tabs.

## Source of truth

It reads only:

- `TASK LIST`
- every `TASK ... PROG` tab

## Recreated Daily Summary

For a selected date, the program rebuilds:

- Activity No.
- Task Description
- Responsible Foreman
- Daily +Profit/Loss
- Monthly Totals
- Total to date
- Comments
- Cummulative row

`Responsible Foreman` and `Comments` are initially blank because they do not exist in the PROG data. They are held in a separate SQL metadata table for a future input screen.

## Recreated Daily Production

For each task:

- Date
- Planned - Daily Quantity
- Actual - Daily Quantity
- Planned - Total to Date
- Actual - Total to Date
- Variance

## Downloads

- Daily Summary PNG and CSV
- Daily Production table PNG and CSV
- Daily Production chart PNG
- All Activities Timeline CSV

## Important

Delete the older `data\tweefontein_production.db` if you copy this program into an older SQL-version folder. The cleanest method is to extract this ZIP into a new folder.


## Version 2.3
- Negative costs display as `-R 1,234.56`.
- Bracketed values are recognised as negative.
- Positive rows use green styling; negative rows use red styling.
- Exported Daily Summary PNG uses matching green/red monetary cells.


## Version 3.0 — corrected totals

The report now derives totals as follows:

- Daily +Profit/Loss = PROG column F for the selected date.
- Monthly Totals = PROG column G for the selected date.
- Total to date = PROG column G for the selected date.
- The cumulative row is the sum of the unique management activities in the date's owning monthly period.
- The main Phase 3 workbook and waste-rock supplement are combined.
- Phase 4 and unrelated workbooks are excluded.
- Duplicate task sheets from other monthly periods are excluded.
- Negative values are displayed as `-R`.


## Version 3.1 — corrected costing columns

The TASK ... PROG source mapping is now:

- `H` = Daily +Profit/Loss
- `I` = Monthly Total / Total to date
- `L` = Actual Daily Quantity
- `M` = Actual Total to Date
- `N` = Planned Daily Quantity
- `O` = Planned Total to Date

Example:

- For 25 July, Clear & Grub uses row 46.
- Daily +Profit/Loss is read from `H46`.
- Monthly Total and Total to date are read from `I46`.

Delete the existing database or extract this version into a new folder, then synchronise again.


## Version 3.2 — forced H/I rebuild

This version uses a new database file: `data\tweefontein_production_v3_2.db`. Old F/G imports cannot be reused. The title bar must show `3.2 Forced H-I Rebuild`, and the sidebar confirms `Cost source: PROG H = Daily, I = Total`.

## Version 3.3 verified correction

This package was inspected after creation. Its source code contains:

- `VERSION = "3.3 VERIFIED H-I IMPORT"`
- `daily_profit_loss = H`
- `cumulative_profit_loss = I`
- database file `tweefontein_production_v3_3.db`

The ZIP contains no `__pycache__` and no pre-existing database.


## Version 3.4 — Office labour

The Daily Summary now includes Office labour before the cumulative row.

The office complement is calculated as:

`69.17 + 7×54.06 + 86.89 + 2×90.00 + 80.00 = R794.48 per effective hour`

- Monday–Friday defaults to 10 actual hours × 1.0.
- Saturday defaults to 7 actual hours × 1.5 = 10.5 effective hours.
- Sunday defaults to 0 hours with a configurable 2.0 factor.
- Hours and factor can be edited for each report date and saved in SQLite.

For Saturday 25 July:

`-R794.48 × 10.5 = -R8,342.04`

Office labour is included in the screen, CSV, PNG, and cumulative totals.


## Version 3.5

- Adds TASK 6.1 / PROG 6.1 as Activity 13: Process RWD Fill.
- Adds a project calendar for default or custom office hours, day type and notes.
- Adds date-specific comments for each selected activity.
- Adds horizontal and vertical scrollbars to report tables.
- Adds a Production Champions League using the four team combinations supplied by the user.
- 100% or above is marked QUALIFIED for incentive. League points: 3 at 100%+, 1 at 90–99.9%, 0 below 90%.
- Team definitions follow the Production Performance Review presentation.


## Version 3.6 — weekly incentive league

The points system has been removed.

### League cycle

- Each league runs from Thursday to Wednesday.
- A new Thursday starts a new league.
- The winner can be presented on Friday.

### Incentive rule

- Weekly achievement of 100% or above = QUALIFIED.
- Below 100% = NOT QUALIFIED.
- Ranking is by achievement percentage, then variance, then actual quantity.

The achievement calculation follows the production presentation:

`Weekly Actual ÷ Weekly Target × 100`

For example, 17,071 actual against 24,500 target is approximately 70%.

### Default daily team targets

- Makanyane: 2,700 m³
- Silas: 3,500 m³
- Pleasure: 1,400 m³
- Moshe: 1,400 m³

Each team's target can be edited for a specific date when an unexpected event makes the standard target unfair. The adjustment and reason are stored in SQL. Restore Default removes the adjustment.

### Progressive graphs

- Daily Target versus Daily Actual for the selected Thursday–Wednesday week.
- Weekly achievement trend for the most recent eight leagues.
- A 100% incentive threshold is shown on the weekly graph.


## Version 3.6.1 startup correction

CustomTkinter `CTkOptionMenu` uses `variable=`, not `textvariable=`.

The office-labour factor selector has been corrected. The application title should show:

`3.6.1 STARTUP FIX`


## Version 3.6.2
Verified correction: CTkOptionMenu now uses variable= rather than textvariable=.


## Version 3.7

### Daily Production

The activity selector now uses the Daily Summary format:

`1. Clear & grub` through `17. RWD Manhole & Subsoil`.

Activity 13 is `Process RWD Fill`.

Daily Production graphs include selectable start and end dates. Markers are smaller, and the Matplotlib toolbar provides zoom, pan, reset and save controls.

### Team Champions League activity sources

- Makanyane actual = Activity 7 + Activity 10 + Activity 13
- Silas actual = Activity 6 + Activity 14
- Pleasure actual = Activity 15
- Moshe actual = Activity 16

### Default targets

- Makanyane = 2,700 m³/day
- Silas = 3,500 m³/day
- Pleasure = 1,400 m³/day
- Moshe = 1,400 m³/day

Targets can be adjusted for a selected day with a reason. Restore Default removes the adjustment.

### Weekly competition

Each league runs Thursday through Wednesday. Ranking uses:

`Cumulative Actual ÷ Cumulative Target × 100`

A team qualifies for the incentive at 100% or above.

The league table follows the production-review format with average target, average actual, cumulative target, cumulative actual, achievement and incentive status.

Progress graphs support independent date ranges and include interactive zoom and pan controls.


## Version 3.8 — verified production-team sources

### Waste rock source restriction

Activity 15 and Activity 16 are imported only from workbooks whose names begin with:

`INREP36600 Phase 3 - Daily Costing Waste rock`

- Activity 15: TASK 1 / PROG 1 — Waste Rock L/H
- Activity 16: TASK 2 / PROG 2 — Waste Rock Process

No other PROG sheet in that workbook is used for those activities.

### Weekly actual-production mappings

- Makanyane = Activity 7 + Activity 10 + Activity 13
- Silas = Activity 6 + Activity 14
- Pleasure = Activity 15
- Moshe = Activity 16

### Weekly percentage

For every Thursday–Wednesday league:

`PTS = SUM(Actual Daily Quantity) / SUM(Edited or Default Daily Target) × 100`

### GD

`GD = Current league PTS − Previous league PTS`

The league table now follows the presentation layout:

`NO | TEAM NAME | PLAN | ACTUAL | GD | PTS`

The incentive status remains available and is qualified at 100% or above.


## Version 3.9 — in-week status scoring

For a selected status date during the Thursday-Wednesday league:

- Actual numerator includes quantities only up to and including the selected date.
- Target denominator always includes the full week's targets.
- Sunday has a default target of zero unless explicitly edited.
- Future Tuesday and Wednesday targets remain included during a Monday status check.
- Future actual quantities are excluded.

Formula:

`Status PTS = Actual completed to selected date / Full week's target × 100`


## Version 3.9.1 — responsible foreman editor

A Responsible Foreman editor is available directly above the Daily Summary table.

1. Select an activity.
2. Enter the responsible foreman's name.
3. Select **Save Foreman**.

Foreman names are stored in `task_metadata`. Existing date-specific comments remain in
`activity_comments`, so saving a foreman does not overwrite or delete comments.

This release deliberately continues using:

`data\tweefontein_production_v3_9.db`

To preserve comments already entered in v3.9, copy the existing
`tweefontein_production_v3_9.db` file into the new release's `data` folder before running
the application. Do not delete the database.


## Version 3.9.2 — Daily P/L cumulative correction

- Daily +Profit/Loss cumulative sums activity daily P/L only.
- Office labour is excluded from the Daily +Profit/Loss cumulative because its daily cell is blank.
- Office labour remains included in Monthly Totals and Total to date.
- The same v3.9 database filename is retained, preserving comments and foreman names.


## Version 3.9.3 — individual cell colour coding

Daily Summary colouring is now applied per cell rather than per row:

- Negative number: red cell
- Positive number: green cell
- Zero or blank number: white cell
- Activity No., Task Description and Responsible Foreman follow the sign of that row's Daily +Profit/Loss
- Comments remain white
- Monthly Totals and Total to date use their own independent signs
- The same colouring is applied to the downloaded Daily Summary PNG

This release continues using `tweefontein_production_v3_9.db`. Copy the database
and its WAL/SHM companions from the working v3.9.2 folder while the application
is closed, or checkpoint the database before copying.
