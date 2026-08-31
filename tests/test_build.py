import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


# Unit tests exercise the parser's pure word-processing behavior.  The real
# PyMuPDF dependency is used by integration builds; a tiny import placeholder
# keeps these tests runnable in environments where it is not installed.
try:
    import fitz  # noqa: F401
except ModuleNotFoundError:
    sys.modules["fitz"] = types.SimpleNamespace()

import build


def subject_words(text: str):
    return [(index, 0, index + 1, 10, char) for index, char in enumerate(text)]


class SubjectDisplayTests(unittest.TestCase):
    def test_115_subject_names_are_shown_as_short_names(self):
        cases = {
            "多采多億英文": ("英語", ["英語"]),
            "多采多億(閱讀)": ("閱讀", ["閱讀"]),
            "多采多億(作文)": ("作文", ["作文"]),
            "多采多億(作閱)": ("作閱", ["作閱"]),
            "英文": ("英語", ["英語"]),
            "自然科學": ("自然", ["自然"]),
            "表演藝術": ("表藝", ["表藝"]),
            "億起創E(電腦)": ("電腦", ["電腦"]),
            "本土語文": ("本土語", ["本土語"]),
            "藝術音樂": ("音樂", ["音樂"]),
            "視覺藝術": ("美勞", ["美勞"]),
            "綜合活動": ("綜合", ["綜合"]),
        }

        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(
                    build.parse_subject(subject_words(source), 0),
                    expected,
                )

    def test_standalone_school_course_name_is_not_mislabeled_as_english(self):
        self.assertEqual(
            build.parse_subject(subject_words("多采多億"), 0),
            ("多采多億", ["多采多億"]),
        )

    def test_compound_curriculum_name_keeps_the_short_primary_subject(self):
        self.assertEqual(
            build.parse_subject(subject_words("健體表演藝術"), 0),
            ("表藝／健體", ["表藝", "健體"]),
        )


class ClassNumberTests(unittest.TestCase):
    def test_teacher_side_class_number_drops_a_leading_zero(self):
        words = [
            (0, 60, 1, 70, "二"),
            (2, 60, 3, 70, "0"),
            (4, 60, 5, 70, "1"),
        ]

        self.assertEqual(build.parse_class_designator(words, 0), "二1")

    def test_class_page_id_and_name_drop_a_leading_zero(self):
        class FakePage:
            def get_text(self, _kind):
                return [
                    (10, 10, 11, 20, "二"),
                    (10, 20, 11, 30, "年"),
                    (10, 30, 11, 40, "0"),
                    (10, 40, 11, 50, "1"),
                    (10, 50, 11, 60, "班"),
                ]

        record = build.parse_class_page(FakePage())

        self.assertEqual(record["id"], "二1")
        self.assertEqual(record["name"], "二年1班")


class TeacherNameTests(unittest.TestCase):
    def test_broken_pdf_text_names_are_corrected_from_the_staff_roster(self):
        cases = {
            "辜?晶": "韋銹晶",
            "徐?慈": "徐彣慈",
            "葉?": "葉珉",
            "郭郁芳": "郭郁芳",
        }

        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(build.normalize_teacher_name(source), expected)


@unittest.skipUnless(hasattr(build.fitz, "open"), "PyMuPDF is required")
class PdfBuildIntegrationTests(unittest.TestCase):
    def test_115_pdfs_build_a_consistent_new_semester_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            stderr = io.StringIO()
            with (
                mock.patch.object(build, "OUT_JSON", output_dir / "data.json"),
                mock.patch.object(build, "OUT_HTML", output_dir / "index.html"),
                contextlib.redirect_stderr(stderr),
            ):
                build.build()

            data = json.loads((output_dir / "data.json").read_text(encoding="utf-8"))
            html = (output_dir / "index.html").read_text(encoding="utf-8")

        self.assertEqual(len(data["classes"]), 64)
        self.assertEqual(len(data["teachers"]), 122)

        teacher_claims = {
            (teacher["name"], slot["class"], day, int(period), slot["subject"])
            for teacher in data["teachers"]
            for day, periods in teacher["schedule"].items()
            for period, slot in periods.items()
        }
        class_claims = {
            (slot["teacher"], class_record["id"], day, int(period), slot["subject"])
            for class_record in data["classes"]
            for day, periods in class_record["schedule"].items()
            for period, slot in periods.items()
        }
        self.assertEqual(len(teacher_claims), 1814)
        self.assertEqual(teacher_claims, class_claims)

        self.assertTrue(all("年0" not in record["name"] for record in data["classes"]))
        homerooms = {record["id"]: record["homeroom_teacher"] for record in data["classes"]}
        self.assertEqual(homerooms["三2"], "韋銹晶")
        self.assertEqual(homerooms["三6"], "徐彣慈")
        self.assertEqual(homerooms["五12"], "葉珉")
        self.assertTrue(all("?" not in teacher["name"] for teacher in data["teachers"]))
        forbidden_long_names = {
            "多采多億英文",
            "多采多億(閱讀)",
            "多采多億(作文)",
            "多采多億(作閱)",
            "自然科學",
            "表演藝術",
            "億起創E(電腦)",
            "本土語文",
            "藝術音樂",
            "視覺藝術",
            "綜合活動",
            "彈性課程社團",
        }
        rendered_subjects = {
            slot["subject"]
            for collection in (data["teachers"], data["classes"])
            for record in collection
            for periods in record["schedule"].values()
            for slot in periods.values()
        }
        self.assertTrue(rendered_subjects.isdisjoint(forbidden_long_names))

        self.assertNotIn("[warn]", stderr.getvalue())
        self.assertNotIn("[check]", stderr.getvalue())
        self.assertIn("warnings: 0", stderr.getvalue())
        self.assertIn("億載國小 115-1 課表", html)
        self.assertIn("資料來源：115學年度第一學期", html)
        self.assertNotIn("114-1", html)


if __name__ == "__main__":
    unittest.main()
