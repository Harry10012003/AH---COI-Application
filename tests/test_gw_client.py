from __future__ import annotations

import unittest
from unittest import mock

from backend.scraper import gw_client


def _valid_parsed_go_report() -> dict:
    return {
        "header": {
            "style_no": "TEST-STYLE",
            "style_desc": "",
            "customer_style": "",
            "customer_name_code": "",
            "brand_name_code": "",
            "customer_label": "",
            "garment_type": "",
            "season": "",
            "buyer": "",
        },
        "ppo_mapping": [],
        "ppo_refs": [],
        "color_summary": [],
        "lot_rows": [
            {
                "lot": "1",
                "job_order_no": "S26V06095KR01",
                "ship_allowance": "2/2",
                "minus_pct": 2,
                "plus_pct": 2,
            }
        ],
        "knit_bom_rows": [],
        "color_breakdown_rows": [],
        "table_count": 1,
    }


class GoReportCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        with gw_client._cache_lock:
            self._mapping_cache = dict(gw_client._go_mapping_cache)
            self._cache_only_failure_cache = dict(gw_client._go_cache_only_failure_cache)
            gw_client._go_mapping_cache.clear()
            gw_client._go_cache_only_failure_cache.clear()

    def tearDown(self) -> None:
        with gw_client._cache_lock:
            gw_client._go_mapping_cache.clear()
            gw_client._go_mapping_cache.update(self._mapping_cache)
            gw_client._go_cache_only_failure_cache.clear()
            gw_client._go_cache_only_failure_cache.update(self._cache_only_failure_cache)

    @mock.patch.object(gw_client, "_save_go_detail_disk_cache")
    @mock.patch.object(gw_client, "parse_go_report", return_value=_valid_parsed_go_report())
    @mock.patch.object(gw_client, "_fetch_text", return_value=("<html>fresh</html>", mock.Mock()))
    @mock.patch.object(gw_client, "_load_go_detail_disk_cache", return_value=None)
    def test_live_fetch_bypasses_prior_cache_only_failure(
        self,
        _load_disk: mock.Mock,
        fetch_text: mock.Mock,
        _parse_report: mock.Mock,
        save_disk: mock.Mock,
    ) -> None:
        go = "S26V06095"

        cache_only = gw_client._fetch_go_report_detail(go, allow_live_fetch=False)
        self.assertFalse(cache_only["ok"])
        self.assertEqual(cache_only["error"], "GO report cache unavailable")
        fetch_text.assert_not_called()

        live = gw_client._fetch_go_report_detail(go, allow_live_fetch=True)

        self.assertTrue(live["ok"])
        self.assertEqual(live["go"], go)
        self.assertEqual(live["lot_rows"][0]["minus_pct"], 2)
        self.assertEqual(live["lot_rows"][0]["plus_pct"], 2)
        fetch_text.assert_called_once()
        _parse_report.assert_called_once_with("<html>fresh</html>")
        save_disk.assert_called_once()

    @mock.patch.object(gw_client, "_fetch_text", side_effect=RuntimeError("source unavailable"))
    @mock.patch.object(gw_client, "_load_go_detail_disk_cache", return_value=None)
    def test_live_failure_keeps_its_own_short_lived_negative_cache(
        self,
        _load_disk: mock.Mock,
        fetch_text: mock.Mock,
    ) -> None:
        go = "S26V06096"

        first = gw_client._fetch_go_report_detail(go, allow_live_fetch=True)
        second = gw_client._fetch_go_report_detail(go, allow_live_fetch=True)

        self.assertFalse(first["ok"])
        self.assertEqual(second, first)
        fetch_text.assert_called_once()


if __name__ == "__main__":
    unittest.main()
