from __future__ import annotations

import csv
import json
import math
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import Counter

from pypdf import PdfReader


PIPE_TYPES = {
    "Straight 9.144 m": {"length": 9.144, "planned": 25},
    "Straight 6.258 m": {"length": 6.258, "planned": 1},
    "Straight 3.360 m": {"length": 3.360, "planned": 1},
    "Straight 1.300 m": {"length": 1.300, "planned": 1},
    "24 degree bend": {"length": 0.240, "planned": 1},
    "5 m flanged pipe": {"length": 5.000, "planned": 1},
    "Small equal tee": {"length": 0.710, "planned": 1},
    "Large equal tee": {"length": 1.420, "planned": 2},
    "80 degree bend": {"length": 1.300, "planned": 1},
}

CONCRETE_ELEMENTS = {
    "Pipe encasement": {"planned_m3": 182.0295, "target_mpa": 15.0, "chainage_based": True},
    "Flange encasement": {"planned_m3": 3.12, "target_mpa": 35.0, "chainage_based": False},
    "Tee encasement": {"planned_m3": 4.2432, "target_mpa": 35.0, "chainage_based": False},
    "Sleeper": {"planned_m3": 2.88, "target_mpa": 25.0, "chainage_based": False},
}

ALIGNMENT_LENGTH = 244.0
SLEEPER_VOLUME = 0.2 * 0.2 * 1.0
SLEEPERS_PLANNED = round(CONCRETE_ELEMENTS["Sleeper"]["planned_m3"] / SLEEPER_VOLUME)


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.asin(math.sqrt(h))


def _centroid(points):
    return (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))


@dataclass
class Alignment:
    points: list[tuple[float, float]]
    raw_distances: list[float]
    components: list[dict]

    @classmethod
    def from_kmz(cls, path: str | Path) -> "Alignment":
        with zipfile.ZipFile(path) as zf:
            root = ET.fromstring(zf.read("doc.kml"))
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        pipe_segments = []
        components = []
        anchor = None
        for mark in root.findall(".//k:Placemark", ns):
            name = mark.findtext("k:name", default="", namespaces=ns)
            coords = []
            for node in mark.findall(".//k:coordinates", ns):
                for item in (node.text or "").split():
                    lon, lat, *_ = item.split(",")
                    coords.append((float(lon), float(lat)))
            if name == "Final Penstock TEE 2" and coords:
                anchor = _centroid(coords)
            if "Pipe" in name and len(coords) >= 2:
                pipe_segments.append((name, coords[0], coords[-1]))
            if name and len(coords) >= 2:
                components.append({"name":name,"points":coords})
        if not pipe_segments or anchor is None:
            raise ValueError("The KMZ does not contain the expected penstock pipe geometry.")

        unused = pipe_segments[:]
        current = anchor
        points = [anchor]
        while unused:
            best = min(unused, key=lambda s: min(haversine_m(current, s[1]), haversine_m(current, s[2])))
            unused.remove(best)
            a, b = best[1], best[2]
            if haversine_m(current, b) < haversine_m(current, a):
                a, b = b, a
            if haversine_m(points[-1], a) > 0.02:
                points.append(a)
            points.append(b)
            current = b

        dists = [0.0]
        for p1, p2 in zip(points, points[1:]):
            dists.append(dists[-1] + haversine_m(p1, p2))
        alignment=cls(points,dists,components)
        for item in components:
            chainages=[alignment.chainage(lon,lat)[0] for lon,lat in item["points"]]
            item["start_ch"],item["end_ch"]=min(chainages),max(chainages)
            item["pipe_type"]=kmz_pipe_type(item["name"])
        # Use the bill-of-quantities piece lengths, normalized to the surveyed
        # CH0–CH244 alignment. This includes the small connection gaps between
        # separate KMZ objects and guarantees that all objects ON equals 244 m.
        nominal=sum(PIPE_TYPES[x["pipe_type"]]["length"] for x in components)
        factor=ALIGNMENT_LENGTH/nominal
        for item in components:
            item["length_m"]=PIPE_TYPES[item["pipe_type"]]["length"]*factor
        return alignment

    @property
    def raw_length(self):
        return self.raw_distances[-1]

    def chainage(self, lon: float, lat: float) -> tuple[float, float]:
        """Return scaled chainage and perpendicular offset in metres."""
        ref_lat = math.radians(lat)
        sx = 111320.0 * math.cos(ref_lat)
        sy = 110540.0
        px, py = lon * sx, lat * sy
        best = (float("inf"), 0.0)
        for i, (a, b) in enumerate(zip(self.points, self.points[1:])):
            ax, ay = a[0] * sx, a[1] * sy
            bx, by = b[0] * sx, b[1] * sy
            vx, vy = bx - ax, by - ay
            denom = vx * vx + vy * vy
            t = 0.0 if denom == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / denom))
            qx, qy = ax + t * vx, ay + t * vy
            offset = math.hypot(px - qx, py - qy)
            along = self.raw_distances[i] + t * haversine_m(a, b)
            if offset < best[0]:
                best = (offset, along)
        return round(best[1] * ALIGNMENT_LENGTH / self.raw_length, 2), round(best[0], 2)

    def point_at_chainage(self, chainage: float) -> tuple[float, float]:
        raw=max(0.0,min(ALIGNMENT_LENGTH,float(chainage)))*self.raw_length/ALIGNMENT_LENGTH
        for i,(a,b) in enumerate(zip(self.points,self.points[1:])):
            d0,d1=self.raw_distances[i],self.raw_distances[i+1]
            if raw<=d1 or i==len(self.points)-2:
                t=0.0 if d1==d0 else max(0.0,min(1.0,(raw-d0)/(d1-d0)))
                return a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])
        return self.points[-1]

    def path_between(self, start_ch: float, end_ch: float) -> list[tuple[float,float]]:
        a=max(0.0,min(ALIGNMENT_LENGTH,float(start_ch))); b=max(0.0,min(ALIGNMENT_LENGTH,float(end_ch))); start,end=sorted((a,b))
        raw_start=start*self.raw_length/ALIGNMENT_LENGTH; raw_end=end*self.raw_length/ALIGNMENT_LENGTH
        out=[self.point_at_chainage(start)]
        for p,d in zip(self.points[1:-1],self.raw_distances[1:-1]):
            if raw_start<d<raw_end: out.append(p)
        out.append(self.point_at_chainage(end))
        return out


def kmz_pipe_type(name: str) -> str:
    n=name.lower()
    if "9.1m pipe" in n: return "Straight 9.144 m"
    if "6.3m pipe" in n: return "Straight 6.258 m"
    if "3.3m pipe" in n: return "Straight 3.360 m"
    if "1.3m pipe" in n: return "Straight 1.300 m"
    if "5m pipe" in n: return "5 m flanged pipe"
    if "80 deg" in n: return "80 degree bend"
    if "119 deg" in n: return "24 degree bend"
    if "temporary" in n and "tee" in n: return "Small equal tee"
    if "tee" in n: return "Large equal tee"
    return "Other"


def export_rn_picture(path: str | Path, alignment: Alignment, kmz_rows, structure_rows,
                      selected_rns, source_kind="pipe") -> None:
    """Render high-resolution checklist Picture 1.

    source_kind='concrete' highlights concrete encasement chainages for the
    selected RN. The RN is the checklist identity and output filename.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Picture export requires Pillow. Run: pip install Pillow") from exc

    selected={normalize_rn(x) or str(x).strip().upper() for x in selected_rns if str(x).strip()}
    if not selected:
        raise ValueError("Select at least one RN to export.")

    chosen=[
        r for r in structure_rows
        if (normalize_rn(r["rn"]) or str(r["rn"] or "").strip().upper()) in selected
        and r["start_ch"] is not None and r["end_ch"] is not None
    ]
    if source_kind=="concrete":
        chosen=[r for r in chosen if "encasement" in str(r["element"] or "").lower()]
    if not chosen:
        raise ValueError("No saved encasement chainages match the selected RN(s).")

    width,height=1920,1080
    image=Image.new("RGB",(width,height),"#0f172a")
    draw=ImageDraw.Draw(image)
    try:
        regular=ImageFont.truetype("arial.ttf",26)
        small=ImageFont.truetype("arial.ttf",21)
        title=ImageFont.truetype("arialbd.ttf",42)
    except OSError:
        regular=small=title=ImageFont.load_default()

    margin=115; top=150; bottom=height-145
    ref_lat=sum(p[1] for p in alignment.points)/len(alignment.points)
    sx=math.cos(math.radians(ref_lat))
    model=[(p[0]*sx,p[1]) for p in alignment.points]
    xs=[p[0] for p in model]; ys=[p[1] for p in model]
    scale=min(
        (width-2*margin)/((max(xs)-min(xs)) or 1),
        (bottom-top)/((max(ys)-min(ys)) or 1)
    )
    ox=(width-(max(xs)+min(xs))*scale)/2
    oy=(top+bottom+(max(ys)+min(ys))*scale)/2

    def cv(points):
        return [(int(lon*sx*scale+ox),int(oy-lat*scale)) for lon,lat in points]

    heading="PENSTOCK CONCRETE ENCASEMENT — CHECKLIST PICTURE 1" if source_kind=="concrete" else "PENSTOCK PIPE LAY — CHECKLIST PICTURE 1"
    draw.text((55,40),heading,font=title,fill="white")
    draw.text(
        (55,98),
        f"RN: {', '.join(sorted(selected))}    Exported: {date.today().isoformat()}",
        font=regular,fill="#cbd5e1"
    )

    status={r["name"]:r for r in kmz_rows}
    for c in alignment.components:
        s=status.get(c["name"])
        laid=bool(s and s["laid"]); missing=bool(s and s["survey_missing"])
        colour="#ef4444" if missing else ("#38bdf8" if laid else "#64748b")
        draw.line(cv(c["points"]),fill=colour,width=16,joint="curve")

    # Highlight every chainage segment belonging to the selected RN.
    for r in chosen:
        pts=cv(alignment.path_between(r["start_ch"],r["end_ch"]))
        draw.line(pts,fill="#fde047",width=18,joint="curve")
        mid=pts[len(pts)//2]
        rn=normalize_rn(r["rn"]) or str(r["rn"])
        label=f"{rn}  CH{float(r['start_ch']):.1f}–CH{float(r['end_ch']):.1f}"
        bbox=draw.textbbox((0,0),label,font=small)
        tw=bbox[2]-bbox[0]; th=bbox[3]-bbox[1]
        x=max(10,min(width-tw-24,mid[0]-tw//2))
        y=max(120,mid[1]-55)
        draw.rounded_rectangle((x-10,y-8,x+tw+10,y+th+8),8,fill="#0f172a",outline="#fde047",width=2)
        draw.text((x,y),label,font=small,fill="#fde047")

    for ch,label in ((0,"CH0"),(162,"CH162"),(244,"CH244")):
        x,y=cv([alignment.point_at_chainage(ch)])[0]
        draw.ellipse((x-8,y-8,x+8,y+8),fill="white")
        draw.text((x+12,y-14),label,font=small,fill="white")

    # Bottom list makes multiple encasements under one RN auditable in Picture 1.
    ranges=" | ".join(
        f"CH{float(r['start_ch']):.1f}–CH{float(r['end_ch']):.1f}"
        for r in sorted(chosen,key=lambda r:float(r["start_ch"]))
    )
    draw.text((55,height-118),f"Highlighted encasement(s): {ranges}",font=small,fill="#fde047")

    legend=(("#64748b","Not laid"),("#38bdf8","Laid + surveyed"),("#ef4444","As-built outstanding"),("#fde047","Selected encasement"))
    x,y=55,height-60
    for colour,text in legend:
        draw.line((x,y,x+42,y),fill=colour,width=9)
        draw.text((x+52,y-13),text,font=small,fill="#e2e8f0")
        x+=320

    Path(path).parent.mkdir(parents=True,exist_ok=True)
    image.save(path,"PNG")

def _gps_decimal(values, ref):
    parts = [float(x) for x in values]
    value = parts[0] + parts[1] / 60 + parts[2] / 3600
    return -value if ref in ("S", "W") else value


def photo_metadata(path: str | Path) -> dict:
    try:
        from PIL import Image, ExifTags
    except ImportError as exc:
        raise RuntimeError("Photo reading requires Pillow. Run: pip install Pillow") from exc
    with Image.open(path) as image:
        exif = image.getexif()
        named = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        try:
            exif_ifd=exif.get_ifd(ExifTags.IFD.Exif)
            named.update({ExifTags.TAGS.get(k,k):v for k,v in exif_ifd.items()})
        except Exception: pass
        try: gps_raw=exif.get_ifd(ExifTags.IFD.GPSInfo)
        except Exception:
            candidate=named.get("GPSInfo"); gps_raw=candidate if hasattr(candidate,"items") else {}
        gps = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_raw.items()} if hasattr(gps_raw,"items") else {}
        result = {"path": str(path), "timestamp": named.get("DateTimeOriginal") or named.get("DateTime")}
        if gps.get("GPSLatitude") and gps.get("GPSLongitude"):
            result["lat"] = _gps_decimal(gps["GPSLatitude"], gps.get("GPSLatitudeRef", "N"))
            result["lon"] = _gps_decimal(gps["GPSLongitude"], gps.get("GPSLongitudeRef", "E"))
        match=re.search(r"(?i)(?:^|[^A-Z])CH\s*[-_ ]?\s*(\d+(?:\.\d+)?)",Path(path).stem)
        if match:
            result["chainage"]=float(match.group(1)); result["chainage_source"]="filename"
        return result


def scan_encasement_source(path: str | Path) -> list[dict]:
    """Group chainage-labelled photos by RN folder in a directory or ZIP."""
    source = Path(path)
    image_suffixes = {".jpg", ".jpeg", ".png"}
    grouped: dict[str, list[dict]] = {}

    def accept(name: str, timestamp=None):
        item = Path(name.replace("\\", "/"))
        if item.suffix.lower() not in image_suffixes or len(item.parts) < 2:
            return
        folder = item.parts[-2]
        rn_match = re.fullmatch(r"(?i)RN\s*[-_ ]?\s*(\d+)", folder)
        corrected = False
        if not rn_match:
            # A common site typo is CH280 for the RN280 evidence folder.
            rn_match = re.fullmatch(r"(?i)CH\s*[-_ ]?\s*(\d+)", folder)
            corrected = bool(rn_match)
        ch_match = re.search(r"(?i)(?:^|[^A-Z])CH\s*[-_ ]?\s*(\d+(?:\.\d+)?)", item.stem)
        if not rn_match or not ch_match:
            return
        rn = f"RN{rn_match.group(1)}"
        grouped.setdefault(rn, []).append({
            "chainage": float(ch_match.group(1)), "name": name,
            "timestamp": timestamp, "corrected_folder": corrected,
        })

    if source.is_dir():
        for photo in source.rglob("*"):
            if photo.is_file():
                accept(str(photo), datetime.fromtimestamp(photo.stat().st_mtime))
    elif source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            for info in zf.infolist():
                if not info.is_dir():
                    accept(info.filename, datetime(*info.date_time))
    else:
        raise ValueError("Select the Chainage Pics folder or a ZIP containing RN folders.")

    results = []
    for rn, photos in sorted(grouped.items(), key=lambda x: int(re.sub(r"\D", "", x[0]))):
        photos.sort(key=lambda x: (x["chainage"], x["name"]))
        unique = sorted({p["chainage"] for p in photos})
        if len(unique) < 2:
            continue
        times = [p["timestamp"] for p in photos if p["timestamp"]]
        results.append({
            "rn": rn, "start_ch": unique[0], "end_ch": unique[-1],
            "photo_count": len(photos), "photos": photos,
            "evidence_date": min(times).date().isoformat() if times else "",
            "folder_corrected": any(p["corrected_folder"] for p in photos),
        })
    if not results:
        raise ValueError("No RN folders with at least two CH-labelled photos were found.")
    return results


def lo31_to_wgs84(y_west: float, x_south: float) -> tuple[float, float]:
    """Hartebeesthoek94 / Lo31 (EPSG:2053 axis convention) to lon/lat."""
    a=6378137.0; f=1/298.257223563; e2=f*(2-f); ep2=e2/(1-e2)
    x=-float(y_west); y=-float(x_south)
    m=y; mu=m/(a*(1-e2/4-3*e2**2/64-5*e2**3/256))
    e1=(1-math.sqrt(1-e2))/(1+math.sqrt(1-e2))
    fp=(mu+(3*e1/2-27*e1**3/32)*math.sin(2*mu)
        +(21*e1**2/16-55*e1**4/32)*math.sin(4*mu)
        +(151*e1**3/96)*math.sin(6*mu)+(1097*e1**4/512)*math.sin(8*mu))
    c1=ep2*math.cos(fp)**2; t1=math.tan(fp)**2
    n1=a/math.sqrt(1-e2*math.sin(fp)**2); r1=a*(1-e2)/(1-e2*math.sin(fp)**2)**1.5; d=x/n1
    lat=fp-(n1*math.tan(fp)/r1)*(d**2/2-(5+3*t1+10*c1-4*c1**2-9*ep2)*d**4/24+(61+90*t1+298*c1+45*t1**2-252*ep2-3*c1**2)*d**6/720)
    lon=math.radians(31)+(d-(1+2*t1+c1)*d**3/6+(5-2*c1+28*t1-3*c1**2+8*ep2+24*t1**2)*d**5/120)/math.cos(fp)
    return math.degrees(lon),math.degrees(lat)


def read_survey_csv(path: str | Path, alignment: Alignment) -> dict:
    with open(path, newline="", encoding="utf-8-sig") as f:
        raw_rows = [r for r in csv.reader(f) if any(str(x).strip() for x in r)]
    if not raw_rows:
        raise ValueError("The survey CSV contains no data rows.")
    # Native survey export: Point ID, Y westing, X southing, elevation (no header).
    native=[]
    for row in raw_rows:
        if len(row)<3: continue
        try: y_west=float(row[1]); x_south=float(row[2])
        except ValueError: continue
        if 10000<abs(y_west)<500000 and 1_000_000<abs(x_south)<4_000_000:
            lon,lat=lo31_to_wgs84(y_west,x_south); ch,offset=alignment.chainage(lon,lat)
            native.append({"point":row[0].strip(),"y_west":y_west,"x_south":x_south,"elevation":float(row[3]) if len(row)>3 and row[3].strip() else None,"lon":lon,"lat":lat,"chainage":ch,"offset":offset})
    if native:
        values=[p["chainage"] for p in native]
        return {"start_ch":min(values),"end_ch":max(values),"points":len(values),"coordinate_system":"Hartebeesthoek94 / Lo31","details":native,"max_offset":max(p["offset"] for p in native)}
    header=raw_rows[0]; rows=[dict(zip(header,row)) for row in raw_rows[1:]]
    norm = [{str(k).strip().lower(): v for k, v in row.items()} for row in rows]
    chain_keys = ("chainage", "ch", "station", "chainage_m")
    values = []
    for row in norm:
        key = next((k for k in chain_keys if row.get(k) not in (None, "")), None)
        if key:
            values.append(float(str(row[key]).upper().replace("CH", "").replace("+", "")))
            continue
        lat_key = next((k for k in ("latitude", "lat", "y") if row.get(k)), None)
        lon_key = next((k for k in ("longitude", "lon", "long", "x") if row.get(k)), None)
        if lat_key and lon_key:
            values.append(alignment.chainage(float(row[lon_key]), float(row[lat_key]))[0])
    if not values:
        raise ValueError("Use a Chainage column, or Latitude/Longitude columns in the survey CSV.")
    return {"start_ch": min(values), "end_ch": max(values), "points": len(values), "coordinate_system":"Chainage / WGS84","details":[]}


def normalize_rn(value: str) -> str:
    """Return a consistent RN### key.

    Accepts RN268, RN 268, RN-268 and legacy numeric-only values such as 268.
    For lab imports, the PDF filename RN remains authoritative.
    """
    raw = str(value or "").strip()
    m = re.search(r"(?i)\bRN\s*[-_ ]?\s*(\d+)\b", raw)
    if m:
        return f"RN{int(m.group(1))}"
    if re.fullmatch(r"\d+", raw):
        return f"RN{int(raw)}"
    return ""


def parse_concrete_lab_pdf(pdf_path):
    """Extract the filename RN and practical cube-result fields from a lab PDF.

    The RN in the PDF body is retained only as an audit note.  Matching always
    uses the filename RN, per the site workflow.
    """
    path = Path(pdf_path)
    rn = normalize_rn(path.stem)
    if not rn:
        raise ValueError("PDF filename must contain an RN, e.g. '6261 - RN268.pdf'.")
    reader = PdfReader(str(path))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    body_rns = sorted(set(re.findall(r"(?i)\bRN\s*[-_ ]?\s*(\d+)\b", text)))
    body_rns = [f"RN{int(x)}" for x in body_rns]

    # Date pairs in these reports repeatedly contain Date Cast then Date Tested.
    dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    pair_counts = Counter()
    for a,b in zip(dates, dates[1:]):
        try:
            da=datetime.strptime(a,"%Y-%m-%d").date(); db=datetime.strptime(b,"%Y-%m-%d").date()
            age=(db-da).days
            if age in (7,14,28): pair_counts[(a,b,age)] += 1
        except ValueError: pass
    cast_date=result_date=None; age_days=None
    if pair_counts:
        (cast_date,result_date,age_days),_ = pair_counts.most_common(1)[0]

    # Prefer the report's Average row.  Take the final plausible MPa decimal.
    average = None
    for m in re.finditer(r"(?is)\bAverage\b(.{0,120})", text):
        nums=[float(x) for x in re.findall(r"\b\d{1,3}\.\d+\b", m.group(1))]
        plausible=[x for x in nums if 0 < x < 100]
        if plausible:
            average=plausible[-1]; break
    if average is None:
        raise ValueError("Could not confidently find the average MPa in this PDF. Use manual entry as fallback.")

    req=None
    m=re.search(r"(?i)Required\s+Strength\s*\(MPa\).*?\b(\d+(?:\.\d+)?)\s*MPa", text, re.S)
    if not m:
        m=re.search(r"(?i)\b(\d+(?:\.\d+)?)\s*MPa\b", text)
    if m: req=float(m.group(1))

    return {"rn":rn,"body_rns":body_rns,"cast_date":cast_date,"result_date":result_date,
            "age_days":age_days,"average_mpa":average,"required_mpa":req,
            "filename":path.name,"path":str(path),"text":text}


def cube_status(target: float, seven=None, twenty_eight=None):
    result = {}
    if seven is not None:
        threshold = 0.60 * target
        result["seven"] = (seven >= threshold, threshold, seven - threshold)
    if twenty_eight is not None:
        result["twenty_eight"] = (twenty_eight >= target, target, twenty_eight - target)
    return result


def due_dates(cast_date: str):
    d = datetime.strptime(cast_date, "%Y-%m-%d").date()
    return {7: d + timedelta(days=7), 14: d + timedelta(days=14), 28: d + timedelta(days=28)}


SCHEMA = """
CREATE TABLE IF NOT EXISTS pipe_installations(
 id INTEGER PRIMARY KEY, team TEXT NOT NULL, installed_date TEXT NOT NULL,
 start_ch REAL NOT NULL, end_ch REAL NOT NULL, length_m REAL NOT NULL,
 pipe_type TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, rn TEXT,
 evidence_type TEXT, evidence_path TEXT, notes TEXT, laid INTEGER NOT NULL DEFAULT 1,
 survey_missing INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS concrete_pours(
 id INTEGER PRIMARY KEY, team TEXT NOT NULL, cast_date TEXT NOT NULL,
 element TEXT NOT NULL, start_ch REAL, end_ch REAL, volume_m3 REAL NOT NULL,
 quantity INTEGER NOT NULL DEFAULT 1, rn TEXT, target_mpa REAL NOT NULL,
 evidence_path TEXT, notes TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS strength_results(
 id INTEGER PRIMARY KEY, pour_id INTEGER NOT NULL, age_days INTEGER NOT NULL,
 result_mpa REAL NOT NULL, result_date TEXT NOT NULL, lab_ref TEXT,
 FOREIGN KEY(pour_id) REFERENCES concrete_pours(id));
CREATE TABLE IF NOT EXISTS targets(
 id INTEGER PRIMARY KEY, team TEXT NOT NULL, discipline TEXT NOT NULL,
 period_start TEXT NOT NULL, period_end TEXT NOT NULL, target REAL NOT NULL);
CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lab_documents(
 id INTEGER PRIMARY KEY, rn TEXT NOT NULL, cast_date TEXT, age_days INTEGER, result_mpa REAL,
 result_date TEXT, filename TEXT NOT NULL, file_path TEXT, body_rn TEXT, imported_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kmz_pipes(
 name TEXT PRIMARY KEY, pipe_type TEXT NOT NULL, start_ch REAL NOT NULL,
 end_ch REAL NOT NULL, length_m REAL NOT NULL, laid INTEGER NOT NULL DEFAULT 0,
 survey_missing INTEGER NOT NULL DEFAULT 0);
"""


def working_days(start: date, end: date) -> int:
    """Inclusive working days with Sundays excluded (site production convention)."""
    if end < start: return 0
    return sum(1 for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday()!=6)


class Store:
    def __init__(self, path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        columns={r[1] for r in self.db.execute("PRAGMA table_info(pipe_installations)")}
        if "laid" not in columns:
            self.db.execute("ALTER TABLE pipe_installations ADD COLUMN laid INTEGER NOT NULL DEFAULT 1")
        if "survey_missing" not in columns:
            self.db.execute("ALTER TABLE pipe_installations ADD COLUMN survey_missing INTEGER NOT NULL DEFAULT 0")
        self.db.commit()

    def add_pipe(self, **data):
        fields = "team,installed_date,start_ch,end_ch,length_m,pipe_type,quantity,rn,evidence_type,evidence_path,notes,laid,survey_missing,created_at"
        data.setdefault("laid",1); data.setdefault("survey_missing",0)
        vals = [data.get(x) for x in fields.split(",")[:-1]] + [datetime.now().isoformat(timespec="seconds")]
        with self.db:
            self.db.execute(f"INSERT INTO pipe_installations({fields}) VALUES ({','.join('?'*14)})", vals)

    def update_pipe(self, record_id, **data):
        fields = "team,installed_date,start_ch,end_ch,length_m,pipe_type,quantity,rn,evidence_type,evidence_path,notes,laid,survey_missing"
        current=self.db.execute("SELECT laid,survey_missing FROM pipe_installations WHERE id=?",(int(record_id),)).fetchone()
        if current:
            data.setdefault("laid",current["laid"]); data.setdefault("survey_missing",current["survey_missing"])
        names=fields.split(","); vals=[data.get(x) for x in names]+[int(record_id)]
        with self.db:
            cur=self.db.execute("UPDATE pipe_installations SET "+",".join(f"{x}=?" for x in names)+" WHERE id=?",vals)
        if cur.rowcount!=1: raise ValueError("The selected installation record no longer exists.")

    def delete_pipe(self, record_id):
        with self.db:
            self.db.execute("DELETE FROM pipe_installations WHERE id=?",(int(record_id),))

    def toggle_pipe_laid(self, record_id):
        with self.db:
            self.db.execute("UPDATE pipe_installations SET laid=CASE WHEN laid=1 THEN 0 ELSE 1 END WHERE id=?",(int(record_id),))

    def sync_kmz_pipes(self, components):
        with self.db:
            for c in components:
                self.db.execute("""INSERT INTO kmz_pipes(name,pipe_type,start_ch,end_ch,length_m)
                    VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET pipe_type=excluded.pipe_type,
                    start_ch=excluded.start_ch,end_ch=excluded.end_ch,length_m=excluded.length_m""",
                    (c["name"],c["pipe_type"],c["start_ch"],c["end_ch"],c["length_m"]))

    def toggle_kmz_pipe(self, name, field="laid"):
        if field not in ("laid","survey_missing"): raise ValueError("Invalid pipe status field")
        with self.db:
            self.db.execute(f"UPDATE kmz_pipes SET {field}=CASE WHEN {field}=1 THEN 0 ELSE 1 END WHERE name=?",(name,))

    def kmz_status(self):
        return {r["name"]:r for r in self.rows("kmz_pipes","start_ch")}

    def add_pour(self, **data):
        fields = "team,cast_date,element,start_ch,end_ch,volume_m3,quantity,rn,target_mpa,evidence_path,notes,created_at"
        data["rn"] = normalize_rn(data.get("rn")) or str(data.get("rn") or "").strip()
        vals = [data.get(x) for x in fields.split(",")[:-1]] + [datetime.now().isoformat(timespec="seconds")]
        with self.db:
            cur = self.db.execute(f"INSERT INTO concrete_pours({fields}) VALUES ({','.join('?'*12)})", vals)
        return cur.lastrowid

    def add_pour_batch(self, shared, structures):
        ids=[]
        with self.db:
            for item in structures:
                data=dict(shared); data.update(item); data["rn"]=normalize_rn(data.get("rn")) or str(data.get("rn") or "").strip()
                fields="team,cast_date,element,start_ch,end_ch,volume_m3,quantity,rn,target_mpa,evidence_path,notes,created_at"
                vals=[data.get(x) for x in fields.split(",")[:-1]]+[datetime.now().isoformat(timespec="seconds")]
                cur=self.db.execute(f"INSERT INTO concrete_pours({fields}) VALUES ({','.join('?'*12)})",vals); ids.append(cur.lastrowid)
        return ids

    def add_strength(self, pour_id, age, result, result_date, lab_ref=""):
        with self.db:
            # One result per structure/age/date; re-upload updates rather than duplicates.
            row=self.db.execute("SELECT id FROM strength_results WHERE pour_id=? AND age_days=?",(pour_id,age)).fetchone()
            if row:
                self.db.execute("UPDATE strength_results SET result_mpa=?,result_date=?,lab_ref=? WHERE id=?",(result,result_date,lab_ref,row["id"]))
            else:
                self.db.execute("INSERT INTO strength_results(pour_id,age_days,result_mpa,result_date,lab_ref) VALUES (?,?,?,?,?)",(pour_id,age,result,result_date,lab_ref))

    def pours_for_rn(self, rn, cast_date=None):
        """Find structures by normalized RN, including legacy numeric-only RN values."""
        rn = normalize_rn(rn)
        if not rn:
            return []
        rows = self.db.execute(
            "SELECT * FROM concrete_pours" + (" WHERE cast_date=?" if cast_date else "") + " ORDER BY id",
            ([cast_date] if cast_date else [])
        ).fetchall()
        return [r for r in rows if normalize_rn(r["rn"]) == rn]

    def resolve_lab_structures(self, rn, cast_date=None):
        """Resolve structures for an uploaded lab PDF.

        MASTER LINKING RULE:
        The RN extracted from the concrete-result PDF FILENAME is authoritative.

        1) Use filename RN + cast date when those structures already carry it.
        2) Otherwise, the cast date identifies the concrete event and ALL structures
           in that event are reassigned to the filename RN.
        3) Existing/non-corresponding structure RNs and any RN printed inside the
           PDF MUST NOT block or override the filename RN.
        """
        rn = normalize_rn(rn)
        direct = self.pours_for_rn(rn, cast_date)
        if direct:
            return direct, "rn"

        if cast_date:
            same_day = self.db.execute(
                "SELECT * FROM concrete_pours WHERE cast_date=? ORDER BY id",
                (cast_date,)
            ).fetchall()
            if same_day:
                return same_day, "cast_date"

        # Last fallback: RN regardless of date.
        direct = self.pours_for_rn(rn)
        return direct, ("rn" if direct else "none")

    def add_strength_for_rn(self, rn, cast_date, age, result, result_date,
                            lab_ref, pdf_path="", body_rn="", required_mpa=None):
        rn = normalize_rn(rn)
        pours, match_mode = self.resolve_lab_structures(rn, cast_date)
        if not pours:
            raise ValueError(
                f"No concrete structures found for {rn}"
                + (f" or cast on {cast_date}." if cast_date else ".")
            )

        ids = [int(p["id"]) for p in pours]

        with self.db:
            # MASTER RN RULE: the concrete-result filename RN always wins.
            # Overwrite every matched structure's existing RN, including a
            # non-corresponding RN imported/entered previously or printed in the PDF.
            marks = ",".join("?" * len(ids))
            self.db.execute(
                f"UPDATE concrete_pours SET rn=? WHERE id IN ({marks})",
                [rn] + ids
            )

            # The laboratory Required Strength is also authoritative when available.
            if required_mpa is not None:
                self.db.execute(
                    f"UPDATE concrete_pours SET target_mpa=? WHERE id IN ({marks})",
                    [float(required_mpa)] + ids
                )

        # Re-read after RN/target updates so the UI immediately sees the corrected event.
        pours = self.db.execute(
            f"SELECT * FROM concrete_pours WHERE id IN ({','.join('?' * len(ids))}) ORDER BY id",
            ids
        ).fetchall()

        for p in pours:
            self.add_strength(p["id"], age, result, result_date, lab_ref)

        with self.db:
            self.db.execute(
                """INSERT INTO lab_documents
                   (rn,cast_date,age_days,result_mpa,result_date,filename,file_path,body_rn,imported_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (rn, cast_date, age, result, result_date,
                 Path(pdf_path).name if pdf_path else lab_ref,
                 pdf_path, body_rn, datetime.now().isoformat(timespec="seconds"))
            )
        return pours

    def rows(self, table, order="id DESC"):
        return self.db.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()

    def pipe_summary(self):
        if self.db.execute("SELECT COUNT(*) FROM kmz_pipes").fetchone()[0]:
            total = self.db.execute("SELECT COALESCE(SUM(length_m),0) FROM kmz_pipes WHERE laid=1").fetchone()[0]
            counts = dict(self.db.execute("SELECT pipe_type,COUNT(*) FROM kmz_pipes WHERE laid=1 GROUP BY pipe_type"))
        else:
            total = self.db.execute("SELECT COALESCE(SUM(length_m),0) FROM pipe_installations WHERE laid=1").fetchone()[0]
            counts = dict(self.db.execute("SELECT pipe_type,COALESCE(SUM(quantity),0) FROM pipe_installations WHERE laid=1 GROUP BY pipe_type"))
        return float(total), counts

    def concrete_summary(self):
        return dict(self.db.execute("SELECT element,COALESCE(SUM(volume_m3),0) FROM concrete_pours GROUP BY element"))

    def open_pour_schedule(self, today=None):
        today = today or date.today()
        rows = []
        for pour in self.rows("concrete_pours", "cast_date"):
            dates = due_dates(pour["cast_date"])
            got = {r[0] for r in self.db.execute("SELECT age_days FROM strength_results WHERE pour_id=?", (pour["id"],))}
            for age in (7, 14, 28):
                if age not in got:
                    delta = (dates[age] - today).days
                    rows.append((pour, age, dates[age], delta))
        return rows

    def strength_series(self, pour_id):
        pour = self.db.execute("SELECT * FROM concrete_pours WHERE id=?", (pour_id,)).fetchone()
        if not pour:
            return None, []
        results = self.db.execute(
            "SELECT * FROM strength_results WHERE pour_id=? ORDER BY age_days,result_date,id", (pour_id,)
        ).fetchall()
        return pour, results

    def all_strength_results(self):
        return self.db.execute(
            """SELECT r.*,p.element,p.cast_date,p.target_mpa,p.team
               FROM strength_results r JOIN concrete_pours p ON p.id=r.pour_id
               ORDER BY r.result_date DESC,r.id DESC"""
        ).fetchall()

    def set_target(self, team, discipline, start, end, target):
        with self.db:
            self.db.execute("INSERT INTO targets(team,discipline,period_start,period_end,target) VALUES (?,?,?,?,?)",
                            (team, discipline, start, end, target))

    def standings(self, start, end):
        targets = self.db.execute("SELECT * FROM targets WHERE period_start=? AND period_end=?", (start, end)).fetchall()
        result = []
        for t in targets:
            if t["discipline"] == "Pipeline":
                actual = self.db.execute("SELECT COALESCE(SUM(length_m),0) FROM pipe_installations WHERE team=? AND installed_date BETWEEN ? AND ?",
                                         (t["team"], start, end)).fetchone()[0]
                unit = "m"
            else:
                actual = self.db.execute("SELECT COALESCE(SUM(volume_m3),0) FROM concrete_pours WHERE team=? AND cast_date BETWEEN ? AND ?",
                                         (t["team"], start, end)).fetchone()[0]
                unit = "m³"
            result.append({"team": t["team"], "discipline": t["discipline"], "target": t["target"],
                           "actual": actual, "score": (actual / t["target"] * 100 if t["target"] else 0), "unit": unit})
        return sorted(result, key=lambda x: x["score"], reverse=True)

    def export_csvs(self, folder):
        folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
        for table in ("pipe_installations", "concrete_pours", "strength_results", "targets"):
            rows = self.rows(table, "id")
            if not rows:
                continue
            with open(folder / f"{table}.csv", "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f); writer.writerow(rows[0].keys()); writer.writerows([tuple(r) for r in rows])

    def get_setting(self, key, default=""):
        row=self.db.execute("SELECT value FROM app_settings WHERE key=?",(key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,str(value)))
