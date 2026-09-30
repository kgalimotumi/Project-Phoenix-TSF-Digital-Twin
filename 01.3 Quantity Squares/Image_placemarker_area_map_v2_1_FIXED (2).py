import os
import re
import math
import zipfile
import shutil
import tempfile
from datetime import datetime
from xml.etree import ElementTree as ET

import exifread
import simplekml
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon
from shapely.geometry import Polygon, LineString, MultiPolygon
from shapely.ops import transform
from pyproj import Transformer


# ============================================================
# TWEFONTEIN IMAGE PLACEMARKER + AREA MAP GENERATOR
# ============================================================
# 1. Read timestamped/geotagged photos.
# 2. Sort chronologically.
# 3. Create Google Earth placemark KML.
# 4. Connect the GPS points into a polygon.
# 5. Calculate area (m²) + perimeter (m).
# 6. Create a shareable PNG map for WhatsApp.
# 7. Optionally plot the Tweefontein KMZ design as background context.
# ============================================================

# -------- USER SETTINGS --------
IMAGE_FOLDER = r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Pictures\TWF geo photos\Images\promise21aug"
OUTPUT_FOLDER = r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Pictures\TWF geo photos\Placemarks"

# Existing site design map. Leave blank if you only want the walked polygon.
SITE_KMZ = r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Documents\Maps kmz\23-2310-Tweefontein.kmz"

# What the foreman was measuring.
GROUP_NAME = "Promise 21 Aug"
ACTIVITY = "Processing Fill"

# If True, copies the WhatsApp summary to Windows clipboard.
COPY_WHATSAPP_SUMMARY = True

# Site design folders that are useful as permanent background.
BACKGROUND_FOLDERS = {
    "Road Buffer",
    "RWD",
    "DW Channel",
    "TSF",
    "Silt Trap Platform",
    "CW Channel",
    "Service Road",
    "CW Diversion Berms",
    "Drainage",
    "Fence",
}

os.makedirs(OUTPUT_FOLDER, exist_ok=True)

safe_group = re.sub(r"[^A-Za-z0-9_. -]+", "_", GROUP_NAME).strip() or "walk"
output_kml = os.path.join(OUTPUT_FOLDER, f"{safe_group}.kml")
output_png = os.path.join(OUTPUT_FOLDER, f"{safe_group}_AREA_MAP.png")
output_summary = os.path.join(OUTPUT_FOLDER, f"{safe_group}_WHATSAPP.txt")


# ============================================================
# EXIF
# ============================================================

def dms_to_dd(values):
    d = float(values[0].num) / float(values[0].den)
    m = float(values[1].num) / float(values[1].den)
    s = float(values[2].num) / float(values[2].den)
    return d + (m / 60.0) + (s / 3600.0)


def decode_xp_field(value):
    try:
        raw_bytes = bytes(value)
        return raw_bytes.decode("utf-16le").rstrip("\x00").strip()
    except Exception:
        return None


def parse_exif_datetime(tags):
    for key in ("EXIF DateTimeOriginal", "EXIF DateTimeDigitized", "Image DateTime"):
        if key in tags:
            raw = str(tags[key]).strip()
            try:
                return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
            except Exception:
                pass
    return None


def get_exif_data(image_path):
    try:
        with open(image_path, "rb") as f:
            tags = exifread.process_file(f, details=False)

        title = None
        if "Image XPTitle" in tags:
            title = decode_xp_field(tags["Image XPTitle"].values)
        elif "Image ImageDescription" in tags:
            title = str(tags["Image ImageDescription"]).strip()
        elif "Image XPSubject" in tags:
            title = decode_xp_field(tags["Image XPSubject"].values)
        elif "EXIF UserComment" in tags:
            title = str(tags["EXIF UserComment"]).strip()

        lat = tags.get("GPS GPSLatitude")
        lat_ref = tags.get("GPS GPSLatitudeRef")
        lon = tags.get("GPS GPSLongitude")
        lon_ref = tags.get("GPS GPSLongitudeRef")

        coords = None
        if lat and lon and lat_ref and lon_ref:
            lat_dd = dms_to_dd(lat.values)
            lon_dd = dms_to_dd(lon.values)

            if str(lat_ref.values).upper() != "N":
                lat_dd = -lat_dd
            if str(lon_ref.values).upper() != "E":
                lon_dd = -lon_dd

            coords = (lat_dd, lon_dd)

        return {
            "coords": coords,
            "title": title,
            "taken_at": parse_exif_datetime(tags),
        }

    except Exception as e:
        print(f"Error reading {image_path}: {e}")
        return {"coords": None, "title": None, "taken_at": None}


# ============================================================
# GEOMETRY
# ============================================================

def utm_epsg(lon, lat):
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def calculate_polygon_metrics(lon_lat_points):
    if len(lon_lat_points) < 3:
        raise ValueError("At least 3 GPS photos are required to calculate an area.")

    poly = Polygon(lon_lat_points)

    repaired = False
    if not poly.is_valid:
        # A chronological GPS walk can occasionally self-cross because of
        # GPS jitter / point order. buffer(0) repairs it, but may legitimately
        # return a MultiPolygon. Keep all repaired pieces instead of assuming
        # there is always one .exterior ring.
        poly = poly.buffer(0)
        repaired = True

    if poly.is_empty:
        raise ValueError("GPS points did not create a usable polygon.")

    if not isinstance(poly, (Polygon, MultiPolygon)):
        raise ValueError(f"Unsupported repaired geometry: {poly.geom_type}")

    c = poly.centroid
    epsg = utm_epsg(c.x, c.y)
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    poly_m = transform(transformer.transform, poly)

    return poly, abs(poly_m.area), poly_m.length, repaired


def polygon_parts(geometry):
    """Always return a list of Polygon objects."""
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    return []


def distance_m(a, b):
    lon1, lat1 = a
    lon2, lat2 = b
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


# ============================================================
# OPTIONAL KMZ BACKGROUND
# ============================================================

KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}


def parse_coord_text(text):
    pts = []
    if not text:
        return pts
    for token in text.replace("\n", " ").split():
        parts = token.split(",")
        if len(parts) >= 2:
            try:
                pts.append((float(parts[0]), float(parts[1])))
            except Exception:
                pass
    return pts


def extract_background_geometries(kmz_path):
    """
    Returns [(folder_name, geometry_type, points)] for the selected permanent
    design folders in the main Tweefontein KMZ.
    """
    if not kmz_path or not os.path.exists(kmz_path):
        return []

    tmpdir = tempfile.mkdtemp(prefix="twf_kmz_")
    result = []
    try:
        with zipfile.ZipFile(kmz_path, "r") as zf:
            kml_names = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_names:
                return []
            kml_name = "doc.kml" if "doc.kml" in kml_names else kml_names[0]
            zf.extract(kml_name, tmpdir)

        root = ET.parse(os.path.join(tmpdir, kml_name)).getroot()

        def walk_folder(elem, inherited_folder=""):
            name_el = elem.find("kml:name", KML_NS)
            folder_name = (name_el.text or "").strip() if name_el is not None else inherited_folder

            for pm in elem.findall("kml:Placemark", KML_NS):
                if folder_name not in BACKGROUND_FOLDERS:
                    continue

                for line in pm.findall(".//kml:LineString/kml:coordinates", KML_NS):
                    pts = parse_coord_text(line.text)
                    if len(pts) >= 2:
                        result.append((folder_name, "line", pts))

                for ring in pm.findall(".//kml:Polygon//kml:LinearRing/kml:coordinates", KML_NS):
                    pts = parse_coord_text(ring.text)
                    if len(pts) >= 3:
                        result.append((folder_name, "polygon", pts))

            for child in elem.findall("kml:Folder", KML_NS):
                walk_folder(child, folder_name)

        document = root.find("kml:Document", KML_NS)
        if document is not None:
            for folder in document.findall("kml:Folder", KML_NS):
                walk_folder(folder)

        return result
    except Exception as e:
        print(f"Background KMZ warning: {e}")
        return []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# LOAD PHOTOS
# ============================================================

photos = []

for filename in os.listdir(IMAGE_FOLDER):
    if filename.lower().endswith((".jpg", ".jpeg", ".png")):
        filepath = os.path.join(IMAGE_FOLDER, filename)
        data = get_exif_data(filepath)

        if data["coords"]:
            photos.append({
                "filename": filename,
                "filepath": filepath,
                "title": data["title"] or filename,
                "taken_at": data["taken_at"],
                "lat": data["coords"][0],
                "lon": data["coords"][1],
            })
        else:
            print(f"No GPS data: {filename}")

if len(photos) < 3:
    raise SystemExit("Not enough geotagged photos. At least 3 are required.")

# Chronological order first. Filename is fallback for missing EXIF time.
photos.sort(key=lambda p: (
    p["taken_at"] is None,
    p["taken_at"] or datetime.max,
    p["filename"].lower(),
))

walk_points = [(p["lon"], p["lat"]) for p in photos]

poly, area_m2, perimeter_m, repaired = calculate_polygon_metrics(walk_points)
start_end_gap = distance_m(walk_points[0], walk_points[-1])


# ============================================================
# CREATE KML
# ============================================================

kml = simplekml.Kml()

point_folder = kml.newfolder(name=f"{GROUP_NAME} - Photo Points")

for idx, p in enumerate(photos, start=1):
    placemark_name = p["title"] if p["title"] else p["filename"]
    pnt = point_folder.newpoint(
        name=f"{idx:02d} - {placemark_name}",
        coords=[(p["lon"], p["lat"])]
    )
    image_path_kml = p["filepath"].replace("\\", "/")
    when = p["taken_at"].strftime("%Y-%m-%d %H:%M:%S") if p["taken_at"] else "No EXIF timestamp"
    pnt.description = f"""
    <![CDATA[
        <h3>{placemark_name}</h3>
        <p><b>File:</b> {p["filename"]}</p>
        <p><b>Time:</b> {when}</p>
        <img src="file:///{image_path_kml}" width="400"/>
    ]]>
    """

polygon_folder = kml.newfolder(name=f"{GROUP_NAME} - Measured Area")
parts = polygon_parts(poly)

for part_no, part in enumerate(parts, start=1):
    suffix = "" if len(parts) == 1 else f" - Part {part_no}"
    pol = polygon_folder.newpolygon(
        name=f"{GROUP_NAME}{suffix} | Total {area_m2:,.0f} m²",
        outerboundaryis=[(lon, lat) for lon, lat in part.exterior.coords]
    )
    pol.description = (
        f"<b>Activity:</b> {ACTIVITY}<br>"
        f"<b>Total Area:</b> {area_m2:,.2f} m²<br>"
        f"<b>Total Perimeter:</b> {perimeter_m:,.2f} m<br>"
        f"<b>GPS photos:</b> {len(photos)}"
    )
    pol.style.polystyle.color = simplekml.Color.changealphaint(90, simplekml.Color.red)
    pol.style.linestyle.width = 4

kml.save(output_kml)


# ============================================================
# CREATE SHAREABLE MAP PNG
# ============================================================

background = extract_background_geometries(SITE_KMZ)

fig, ax = plt.subplots(figsize=(14, 9))

# Permanent design background.
for folder_name, gtype, pts in background:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    if gtype == "line":
        ax.plot(xs, ys, linewidth=0.8, alpha=0.55)
    else:
        ax.plot(xs, ys, linewidth=0.7, alpha=0.4)

# Walked polygon. Supports both Polygon and repaired MultiPolygon geometry.
for part in polygon_parts(poly):
    x, y = part.exterior.xy
    ax.fill(x, y, alpha=0.26)
    ax.plot(x, y, linewidth=3)

# Photo points and sequence.
for idx, p in enumerate(photos, start=1):
    ax.scatter(p["lon"], p["lat"], s=28, zorder=5)
    # Label every few points to reduce clutter.
    if idx == 1 or idx == len(photos) or idx % max(1, len(photos)//8) == 0:
        ax.annotate(
            str(idx),
            (p["lon"], p["lat"]),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=8,
            weight="bold",
        )

# Fit to the whole site if design background exists; otherwise fit the walk.
if background:
    bx = []
    by = []
    for _, _, pts in background:
        bx.extend([p[0] for p in pts])
        by.extend([p[1] for p in pts])
    if bx and by:
        ax.set_xlim(min(bx), max(bx))
        ax.set_ylim(min(by), max(by))
else:
    minx, miny, maxx, maxy = poly.bounds
    pad_x = max((maxx - minx) * 0.22, 0.00015)
    pad_y = max((maxy - miny) * 0.22, 0.00015)
    ax.set_xlim(minx - pad_x, maxx + pad_x)
    ax.set_ylim(miny - pad_y, maxy + pad_y)

capture_date = next((p["taken_at"] for p in photos if p["taken_at"]), None)
date_text = capture_date.strftime("%d %b %Y") if capture_date else "Date unavailable"

title = (
    f"Tweefontein TSF Phase 3A — {ACTIVITY}\n"
    f"{GROUP_NAME} | {date_text} | {area_m2:,.0f} m²"
)
ax.set_title(title, fontsize=16, weight="bold")

info = (
    f"AREA: {area_m2:,.2f} m²\n"
    f"PERIMETER: {perimeter_m:,.2f} m\n"
    f"GPS PHOTOS: {len(photos)}"
)
if start_end_gap > 25:
    info += f"\nCHECK: Start/end gap {start_end_gap:.1f} m"
if repaired:
    info += "\nCHECK: Polygon geometry repaired"

ax.text(
    0.015, 0.02, info,
    transform=ax.transAxes,
    fontsize=11,
    weight="bold",
    va="bottom",
    bbox=dict(boxstyle="round,pad=0.6", facecolor="white", alpha=0.88),
)

ax.set_xlabel("Longitude")
ax.set_ylabel("Latitude")
ax.set_aspect("equal", adjustable="box")
ax.grid(True, alpha=0.15)

plt.tight_layout()
plt.savefig(output_png, dpi=180, bbox_inches="tight")
plt.close(fig)


# ============================================================
# WHATSAPP SUMMARY
# ============================================================

summary = (
    f"Tweefontein — {ACTIVITY}\n\n"
    f"{GROUP_NAME}\n"
    f"{date_text}\n\n"
    f"Measured Area: {area_m2:,.2f} m²\n"
    f"Perimeter: {perimeter_m:,.2f} m\n"
    f"GPS Photos: {len(photos)}\n\n"
    f"Please ensure that EcoE is notified at least 30 min before the booked "
    f"inspection time if the inspection will continue or be postponed.\n\n"
    f"Booking time slots:\n"
    f"• 15:30 for the following morning\n"
    f"• 11:30 for the afternoon"
)

with open(output_summary, "w", encoding="utf-8") as f:
    f.write(summary)

if COPY_WHATSAPP_SUMMARY:
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(summary)
        root.update()
        root.destroy()
        print("WhatsApp summary copied to clipboard.")
    except Exception as e:
        print(f"Clipboard warning: {e}")


print()
print("=" * 65)
print("TWEFONTEIN IMAGE PLACEMARKER + AREA MAP")
print("=" * 65)
print(f"GPS photos : {len(photos)}")
print(f"Area       : {area_m2:,.2f} m²")
print(f"Perimeter  : {perimeter_m:,.2f} m")
print(f"KML        : {output_kml}")
print(f"MAP PNG    : {output_png}")
print(f"WHATSAPP   : {output_summary}")
print("=" * 65)
