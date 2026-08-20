from __future__ import annotations

import unittest

from backend.scraper.go_parser import _parse_ship_allowance_token, parse_go_report


class ShipmentAllowanceParserTests(unittest.TestCase):
    def test_supported_allowance_formats(self) -> None:
        cases = {
            "2/2": (2, 2),
            "2% / 3%": (2, 3),
            "+/-2%": (2, 2),
            "+-2%": (2, 2),
            "-/+2%": (2, 2),
            "-+2%": (2, 2),
            "\N{PLUS-MINUS SIGN}2%": (2, 2),
            "SHIPMENT \N{PLUS-MINUS SIGN} 2%": (2, 2),
            "100": (0, 0),
        }

        for token, expected in cases.items():
            with self.subTest(token=token):
                self.assertEqual(_parse_ship_allowance_token(token), expected)

    def test_lot_row_keeps_allowance_without_ppo_mapping(self) -> None:
        html = """
        <table>
          <tr>
            <th>Lot No./JO #</th>
            <th>BPO Date</th>
            <th>Original BPO Date</th>
            <th>PPC Date</th>
            <th>Total Pieces</th>
            <th>Shipment Allowance</th>
          </tr>
          <tr>
            <td>1/S26V06095KR01</td>
            <td>2026-07-01</td>
            <td>2026-06-25</td>
            <td>2026-07-02</td>
            <td>1,200</td>
            <td>+-2%</td>
          </tr>
        </table>
        """

        parsed = parse_go_report(html)

        self.assertEqual(parsed["ppo_mapping"], [])
        self.assertEqual(len(parsed["lot_rows"]), 1)
        self.assertEqual(parsed["lot_rows"][0]["ship_allowance"], "+-2%")
        self.assertEqual(parsed["lot_rows"][0]["minus_pct"], 2)
        self.assertEqual(parsed["lot_rows"][0]["plus_pct"], 2)


if __name__ == "__main__":
    unittest.main()
