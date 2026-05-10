import csv
import tempfile
import unittest
from io import StringIO
from pathlib import Path

import app


class CompanyAppTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        app.init_db(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_material_entry_calculates_amount_and_summary(self):
        app.add_material_entry(
            {
                "entry_date": "2026-05-10",
                "material": "cement",
                "movement": "inward",
                "quantity": "5",
                "rate": "100.50",
                "note": "first load",
            },
            self.db_path,
        )

        data = app.load_dashboard_data(self.db_path)

        self.assertEqual(len(data["materials"]), 1)
        self.assertEqual(data["materials"][0]["amount"], 502.5)
        self.assertEqual(data["material_summary"][0]["total_quantity"], 5)
        self.assertEqual(data["material_summary"][0]["total_amount"], 502.5)

    def test_employee_entry_calculates_total_salary(self):
        app.add_employee_entry(
            {
                "work_date": "2026-05-10",
                "employee_name": "Loading Team A",
                "workers": "8",
                "salary": "450",
                "employee_note": "day shift",
            },
            self.db_path,
        )

        data = app.load_dashboard_data(self.db_path)

        self.assertEqual(len(data["employees"]), 1)
        self.assertEqual(data["employees"][0]["total_salary"], 3600)
        self.assertEqual(data["employee_summary"][0]["total_workers"], 8)
        self.assertEqual(data["employee_summary"][0]["total_salary"], 3600)

    def test_export_csv_contains_material_and_employee_sections(self):
        app.add_material_entry(
            {
                "entry_date": "2026-05-10",
                "material": "flyash",
                "movement": "outward-loading",
                "quantity": "2",
                "rate": "75",
                "note": "truck 12",
            },
            self.db_path,
        )
        app.add_employee_entry(
            {
                "work_date": "2026-05-10",
                "employee_name": "Worker Group",
                "workers": "3",
                "salary": "500",
                "employee_note": "loading",
            },
            self.db_path,
        )

        payload = app.export_csv(self.db_path).decode("utf-8-sig")
        rows = list(csv.reader(StringIO(payload)))

        self.assertIn(["Material Entries"], rows)
        self.assertIn(["Employee Entries"], rows)
        self.assertIn(["2026-05-10", "flyash", "outward-loading", "2.0", "75.0", "150.0", "truck 12"], rows)
        self.assertIn(["2026-05-10", "Worker Group", "3", "500.0", "1500.0", "loading"], rows)

    def test_rejects_invalid_material(self):
        with self.assertRaises(ValueError):
            app.add_material_entry(
                {
                    "entry_date": "2026-05-10",
                    "material": "sand",
                    "movement": "inward",
                    "quantity": "1",
                    "rate": "1",
                },
                self.db_path,
            )


if __name__ == "__main__":
    unittest.main()
