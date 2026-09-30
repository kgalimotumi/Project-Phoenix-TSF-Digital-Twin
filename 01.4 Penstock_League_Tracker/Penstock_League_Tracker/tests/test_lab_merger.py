import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lab_merger import normalize_rn,safe_name

class MergerTests(unittest.TestCase):
    def test_rn_formats(self):
        for text in ("RN67.pdf","RN 67.pdf","RN-067(1).pdf","Result RN_0067"):
            self.assertEqual(normalize_rn(text),"67")
    def test_safe_name(self):
        self.assertEqual(safe_name('RN114 Toe: wall / A16?'),'RN114 Toe_ wall _ A16')

if __name__=='__main__': unittest.main()
