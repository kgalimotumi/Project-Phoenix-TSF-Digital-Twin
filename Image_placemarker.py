import os
import exifread
import simplekml

# Paths
image_folder = r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Pictures\TWF geo photos\Images\promise15sep"
output_folder = r"C:\Users\Kgali.Motumi\OneDrive - Stefanutti Stocks\Pictures\TWF geo photos\Placemarks"
output_kml = os.path.join(output_folder, "promise15sep.kml")

os.makedirs(output_folder, exist_ok=True)

# --- Helper functions ---
def dms_to_dd(values):
    d = float(values[0].num) / float(values[0].den)
    m = float(values[1].num) / float(values[1].den)
    s = float(values[2].num) / float(values[2].den)
    return d + (m / 60.0) + (s / 3600.0)

def decode_xp_field(value):
    """Decode Windows EXIF XP fields (UTF-16LE)"""
    try:
        raw_bytes = bytes(value)
        return raw_bytes.decode('utf-16le').rstrip('\x00').strip()
    except:
        return None

def get_exif_data(image_path):
    try:
        with open(image_path, 'rb') as f:
            tags = exifread.process_file(f, details=False)

        # --- TITLE EXTRACTION (priority order) ---
        title = None

        if "Image XPTitle" in tags:
            title = decode_xp_field(tags["Image XPTitle"].values)
        elif "Image ImageDescription" in tags:
            title = str(tags["Image ImageDescription"]).strip()
        elif "Image XPSubject" in tags:
            title = decode_xp_field(tags["Image XPSubject"].values)
        elif "EXIF UserComment" in tags:
            title = str(tags["EXIF UserComment"]).strip()

        # --- GPS ---
        lat = tags.get("GPS GPSLatitude")
        lat_ref = tags.get("GPS GPSLatitudeRef")
        lon = tags.get("GPS GPSLongitude")
        lon_ref = tags.get("GPS GPSLongitudeRef")

        coords = None
        if lat and lon and lat_ref and lon_ref:
            lat_dd = dms_to_dd(lat.values)
            lon_dd = dms_to_dd(lon.values)

            if lat_ref.values != 'N':
                lat_dd = -lat_dd
            if lon_ref.values != 'E':
                lon_dd = -lon_dd

            coords = (lat_dd, lon_dd)

        return coords, title

    except Exception as e:
        print(f"Error reading {image_path}: {e}")
        return None, None

# --- Create KML ---
kml = simplekml.Kml()

for filename in os.listdir(image_folder):
    if filename.lower().endswith((".jpg", ".jpeg", ".png")):
        filepath = os.path.join(image_folder, filename)

        coords, title = get_exif_data(filepath)

        if coords:
            lat, lon = coords

            # ✅ Use title if available, otherwise filename
            placemark_name = title if title else filename

            pnt = kml.newpoint(name=placemark_name, coords=[(lon, lat)])

            image_path_kml = filepath.replace("\\", "/")

            pnt.description = f"""
            <![CDATA[
                <h3>{placemark_name}</h3>
                <p><b>File:</b> {filename}</p>
                <img src="file:///{image_path_kml}" width="400"/>
            ]]>
            """
        else:
            print(f"No GPS data: {filename}")

# --- Save KML ---
kml.save(output_kml)
print(f"KML created at: {output_kml}")
