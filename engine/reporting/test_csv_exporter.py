import os
import sqlite3
import csv
import unittest
from engine.reporting.csv_exporter import SQLiteCSVExporter

class TestCSVExporter(unittest.TestCase):
    def setUp(self):
        self.db_path = "test_export.db"
        self.csv_path = "test_export.csv"

        # Setup fake db
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                email TEXT,
                password TEXT,
                checked INTEGER
            )
        """)
        cursor.executemany(
            "INSERT INTO accounts (email, password, checked) VALUES (?, ?, ?)",
            [("test@example.com", "pass123", 1), ("user@domain.com", "secret", 0)]
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        if os.path.exists(self.csv_path):
            os.remove(self.csv_path)

    def test_export(self):
        success = SQLiteCSVExporter.export_table_to_csv(self.db_path, "accounts", self.csv_path)
        self.assertTrue(success)
        self.assertTrue(os.path.exists(self.csv_path))

        with open(self.csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["email"], "test@example.com")
            self.assertEqual(rows[1]["email"], "user@domain.com")

if __name__ == '__main__':
    unittest.main()
