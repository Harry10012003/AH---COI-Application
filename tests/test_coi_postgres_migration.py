from __future__ import annotations

from pathlib import Path
import unittest

from backend.engine.coi_postgres import FIELD_SPECS


class CoiPostgresMigrationTests(unittest.TestCase):
    def test_manual_migration_contains_required_contract(self) -> None:
        path = Path("migrations/postgresql/001_create_coi_issue_store.sql")
        sql = path.read_text(encoding="utf-8").lower()
        self.assertIn("create table if not exists ah_app.current_issue", sql)
        self.assertIn("create table if not exists ah_app.current_issue_row", sql)
        self.assertIn("create table if not exists ah_app.issue_audit_log", sql)
        self.assertIn("create or replace view ah_app.v_current_issue_rows", sql)
        self.assertNotIn("source_hash", sql)
        self.assertNotIn("internal_row_id\nfrom", sql)
        for spec in FIELD_SPECS:
            if spec.db_name is not None:
                self.assertIn(spec.db_name, sql)

    def test_ui_contract_has_exactly_28_coi_fields(self) -> None:
        self.assertEqual(len(FIELD_SPECS), 28)


if __name__ == "__main__":
    unittest.main()
