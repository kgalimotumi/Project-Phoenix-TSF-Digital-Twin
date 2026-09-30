import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import ALIGNMENT_LENGTH, SLEEPERS_PLANNED, Store, cube_status, due_dates, lo31_to_wgs84, read_survey_csv, Alignment, working_days, photo_metadata, scan_encasement_source


class CoreTests(unittest.TestCase):
    def test_due_dates(self):
        d = due_dates("2026-09-01")
        self.assertEqual(str(d[7]), "2026-09-08")
        self.assertEqual(str(d[28]), "2026-09-29")
        self.assertEqual(working_days(__import__('datetime').date(2026,9,18),__import__('datetime').date(2026,9,23)),5)

    def test_strength_rules(self):
        self.assertTrue(cube_status(30, seven=18)["seven"][0])
        self.assertFalse(cube_status(30, twenty_eight=29.9)["twenty_eight"][0])

    def test_photo_filename_chainage_fallback(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"CH164.2.jpg"; Image.new("RGB",(8,8)).save(p)
            self.assertEqual(photo_metadata(p)["chainage"],164.2)

    def test_scan_encasement_folders_and_typo(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for folder,chainages in (("RN216",(164.2,170.2,182.2)),("CH280",(160.5,188.2))):
                (root/folder).mkdir()
                for ch in chainages: Image.new("RGB",(4,4)).save(root/folder/f"CH{ch}.jpg")
            rows={r["rn"]:r for r in scan_encasement_source(root)}
            self.assertEqual((rows["RN216"]["start_ch"],rows["RN216"]["end_ch"]),(164.2,182.2))
            self.assertTrue(rows["RN280"]["folder_corrected"])

    def test_sleeper_count(self):
        self.assertEqual(SLEEPERS_PLANNED, 72)

    def test_store_summary(self):
        with tempfile.TemporaryDirectory() as td:
            s=Store(Path(td)/"x.db")
            s.add_pipe(team="A",installed_date="2026-09-01",start_ch=0,end_ch=9.1,length_m=9.1,pipe_type="Straight 9.144 m",quantity=1,rn="",evidence_type="Manual",evidence_path="",notes="")
            self.assertAlmostEqual(s.pipe_summary()[0],9.1)
            rid=s.rows('pipe_installations')[0]['id']
            s.update_pipe(rid,team='A',installed_date='2026-09-10',start_ch=10,end_ch=20,length_m=10,pipe_type='Straight 9.144 m',quantity=1,rn='',evidence_type='Survey CSV',evidence_path='new.csv',notes='')
            self.assertAlmostEqual(s.pipe_summary()[0],10)

    def test_strength_series(self):
        with tempfile.TemporaryDirectory() as td:
            s=Store(Path(td)/"x.db")
            pid=s.add_pour(team="Concrete",cast_date="2026-09-01",element="Pipe encasement",start_ch=0,end_ch=4,volume_m3=3.0,quantity=1,rn="RN1",target_mpa=30,evidence_path="",notes="")
            s.add_strength(pid,7,19,"2026-09-08","LAB1")
            pour,rows=s.strength_series(pid)
            self.assertEqual(pour["target_mpa"],30)
            self.assertEqual(rows[0]["result_mpa"],19)

    def test_lo31_conversion_and_native_csv(self):
        lon,lat=lo31_to_wgs84(89707.050,2754803.631)
        self.assertAlmostEqual(lon,30.1121428,places=5); self.assertAlmostEqual(lat,-24.8957937,places=5)
        root=Path(__file__).resolve().parents[1]
        alignment=Alignment.from_kmz(root/'assets'/'Penstock Pipes.kmz')
        result=read_survey_csv(root/'assets'/'Tweefonteine TSF3A-Penstock-As-built Coordinates-2026-09-09.csv',alignment)
        self.assertEqual(result['coordinate_system'],'Hartebeesthoek94 / Lo31')
        self.assertEqual(result['points'],2)
        self.assertAlmostEqual(result['start_ch'],197.09,places=1)
        p=alignment.point_at_chainage(162); self.assertEqual(len(p),2)
        section=alignment.path_between(143.55,161.61); self.assertGreaterEqual(len(section),2)


if __name__ == "__main__": unittest.main()
