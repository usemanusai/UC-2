import csv
import sqlite3
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

class SQLiteCSVExporter:
    """Exports SQLite databases to CSV format."""

    @staticmethod
    def export_table_to_csv(db_path: str, table_name: str, output_csv_path: str) -> bool:
        """
        Connects to an SQLite DB at db_path, fetches all rows from table_name,
        and writes them to output_csv_path. Returns True if successful.
        """
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(f"SELECT * FROM {table_name}")
            rows = cursor.fetchall()

            if not rows:
                logger.warning(f"No data found in table '{table_name}' in '{db_path}'.")
                return False

            fieldnames = rows[0].keys()

            with open(output_csv_path, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(dict(row))

            logger.info(f"Successfully exported {len(rows)} rows to {output_csv_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to export CSV: {e}")
            return False
        finally:
            if 'conn' in locals():
                conn.close()
