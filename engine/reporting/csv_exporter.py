import csv
import re
import sqlite3
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# Only allow alphanumeric + underscore table names (prevents SQL injection)
_SAFE_TABLE_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class SQLiteCSVExporter:
    """Exports SQLite tables to CSV format."""

    @staticmethod
    def export_table_to_csv(db_path: str, table_name: str, output_csv_path: str) -> bool:
        """
        Connects to an SQLite DB at db_path, fetches all rows from table_name,
        and writes them to output_csv_path. Returns True if successful.

        Parameters
        ----------
        db_path : str
            Path to the SQLite database file.
        table_name : str
            Name of the table to export. Must match ``[A-Za-z_][A-Za-z0-9_]*``
            to prevent SQL injection.
        output_csv_path : str
            Destination CSV file path.

        Raises
        ------
        ValueError
            If ``table_name`` contains characters that are not alphanumeric or
            underscore (SQL injection guard).
        """
        if not _SAFE_TABLE_RE.match(table_name):
            raise ValueError(
                f"Invalid table name {table_name!r}. "
                "Table names must match [A-Za-z_][A-Za-z0-9_]*."
            )

        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # table_name is validated against the allowlist regex above
            cursor.execute(f"SELECT * FROM {table_name}")  # nosec B608
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
            if conn is not None:
                conn.close()
