from __future__ import annotations

import time
import unittest

from src import monitoring


class MonitoringTest(unittest.TestCase):
    def test_monitor_state_tracks_score_only_run(self) -> None:
        monitoring.init_monitor_state(total_rows=10, target_rows=5, completed_before_run=0, start_time=time.monotonic())

        snapshot = monitoring.monitor_snapshot()

        self.assertEqual(snapshot["run_status"], "running")
        self.assertEqual(snapshot["target_rows"], 5)
        self.assertEqual(snapshot["processed"], 0)
        self.assertEqual(snapshot["recent_scores"], [])
        self.assertEqual(snapshot["recent_validations"], [])

    def test_dashboard_html_references_external_assets(self) -> None:
        html = monitoring.dashboard_html()

        self.assertIn('href="/dashboard.css"', html)
        self.assertIn('src="/dashboard.js"', html)
        self.assertNotIn("<style>", html)
        self.assertNotIn("<script>", html)

    def test_dashboard_assets_are_available(self) -> None:
        css = monitoring.read_dashboard_asset("dashboard.css")
        javascript = monitoring.read_dashboard_asset("dashboard.js")

        self.assertIn(".dashboard", css)
        self.assertIn('fetch("/status"', javascript)


if __name__ == "__main__":
    unittest.main()
