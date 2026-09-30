# Tweefontein Penstock League Tracker

Windows desktop tracker for the pipeline and concrete teams. Data is stored locally in `penstock_tracker.db` and can be exported to CSV from the League tab.

## Start

1. Install Python 3.13 and tick **Add Python to PATH**.
2. Open Command Prompt in this folder and run `pip install -r requirements.txt`.
3. Double-click `run_tracker.bat`.

## Operating rules

- Alignment is fixed at CH0 (Final Penstock TEE 2) to CH244 (80° bend).
- Pipe installation accepts manual chainages, a survey CSV, or two GPS timestamp photos.
- To replace an incorrect as-built entry: select its row in the Installation Log, load the corrected survey CSV, then click **Update Selected Record**. A blank Quantity is safely treated as 1.
- Survey CSV columns may be `Chainage`, or `Latitude` and `Longitude` (`Lat`/`Lon` also work).
- The latest supplied 10-Sep-2026 survey is built into the package. Its no-header format (`Point ID, Y westing, X southing, elevation`) is automatically recognized as Hartebeesthoek94 / Lo31 and converted to the KMZ chainage. Use **Load supplied 10-Sep as-built** to review PSL3–PSL8 before updating or saving the installation entry.
- The Pipe Installation tab displays the actual KMZ plan alignment—not a straight progress bar—including CH0 Final TEE 2, CH162 119° bend, CH244 80° bend, blue installed-pipe sections, orange concrete encasement, and a dashed yellow preview of the currently loaded survey.
- GPS photos are projected onto the KMZ alignment; the program displays their offset from the alignment for a reasonableness check.
- If a photo has no EXIF GPS metadata, chainage is read from its filename. Examples: `CH164.2.jpg` and `CH170.2.jpg`. This supports timestamp-camera and WhatsApp images whose GPS metadata was stripped.
- RN may be left blank and shows as **Pending**.
- Pipe progress is length based; remaining types are quantity based against the supplied inventory.
- Pipe encasement requires start and end chainages. If timestamp photos are used, volume is suggested pro-rata from the 182.0295 m³ design total and may be corrected to the concrete docket volume.
- Concrete Quantity defaults to 1 when left blank. Sleeper entries still use Quantity × 0.04 m³ when no volume is entered.
- **Scan RN encasement folder / ZIP** accepts a `Chainage Pics` folder or ZIP, groups CH-labelled images by RN subfolder, and previews the inferred minimum/maximum chainage. A mistakenly named `CH280` folder is flagged and treated as RN280. Load one detected range into the form, verify the cast date and docket volume, then save it.
- Sleeper volume defaults to quantity × 0.2 × 0.2 × 1.0 m. The design total is 72 sleepers (2.88 m³).
- Cube dates are calculated automatically at 7, 14 and 28 days. Seven-day acceptance is 60% of target; 28-day acceptance is 100% of target. The 14-day result is monitoring only.
- The Cube Results tab plots every result for a selected pour against its 7-day and 28-day contractual target markers. Select a row in the results log to switch the graph to that pour. Green markers comply, red markers fail, and 14-day markers remain monitoring-only.
- League percentage = actual ÷ target × 100 for the selected period. Pipeline actual is metres laid; Concrete actual is m³ cast.
- The Dashboard Plan vs Actual panel defaults to a 23-Sep-2026 target and allows any target date in `YYYY-MM-DD` format. Select Pipe laying, Concrete works, or Sleepers. It calculates planned %, actual %, percentage-point variance, plan attainment, remaining quantity, and the required production per remaining workday. Sundays are excluded.

## Concrete lab PDF merger

- Select the Lab Results folder, Raw Data folder, inspection register `.xlsx`, and Merged Results output folder.
- **Preview Matches** groups lab PDFs by their first four-digit filename reference and finds the RN from lab filenames or the first five pages of lab PDF text.
- Raw PDFs are matched only by the same four-digit filename reference or the RN in their filename. Raw PDF text is never searched.
- **Merge Ready Files** produces each pack in this order: Coverpage → RN lab report → remaining lab documents → all matching raw PDFs.
- A job without a raw-data match is not merged. The colour-coded `concrete_lab_merger_report.xlsx` lists matched jobs, missing register inspections, and unmatched lab files.
- Output PDFs use the inspection-register Description as their filename. When it is unavailable, the fallback is `RN[number] Concrete Test Results - [reference].pdf`.

## Evidence caveat

Many WhatsApp or edited images lose GPS EXIF data. When this happens, use the survey CSV or enter verified chainages manually. Keep the original timestamp photos in the site quality folder; the database stores their file paths rather than copying the photographs.


## RN Concrete Events update — 2026-09-29
- One RN/date can contain multiple structures (e.g. Sleeper x10 + multiple pipe encasements).
- Build structures in the Concrete Encasement batch, then save the entire RN event once.
- Cube Results accepts a PDF directly. Filename RN is authoritative.
- A saved RN lab result propagates to every concrete structure with the same RN/cast date.
- Existing database rows are retained; schema additions are automatic.
