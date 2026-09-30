
SUBSOIL CHECKLIST UPLOADER

Default evidence directory:
C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Desktop\INREP36600 - Tweefontein TSF Project Files\15 Quality\6. Inspections\4. Subsoil Pipes

PHOTO ORDER

Drilled / perforated:
1. Excavation
2. Bedding + Pipe Lay - pipe length on bedding
3. Bedding + Pipe Lay - weld
4. Blanket Layer Backfill - 19mm stone
5. Bidim Wrap
6. Density / Insitu Backfill - Layer 1

Solid:
1. Excavation
2. Bedding + Pipe Lay - pipe length on bedding
3. Bedding + Pipe Lay - weld
4. Blanket Layer Backfill - loose insitu material
5. Density / Insitu Backfill - Layer
6. Unused

The files inside each RN photo folder are sorted numerically by filename.
Example: 1.jpeg, 2.jpeg, 3.jpeg...

The program also searches the matching system's "3. Invert Levels" folder
for a PDF within an RN-labelled folder and uploads that PDF under Optional
Supporting Documents.

INSTALL:
python -m pip install -r requirements.txt

RUN:
python subsoil_checklist_uploader.py

The application intentionally waits for you to log in and manually open the
correct online checklist. Before upload it attempts to verify the online
Inspection Request No. against the selected RN.
