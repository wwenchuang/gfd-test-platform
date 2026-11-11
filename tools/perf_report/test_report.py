"""Small deterministic fixtures for offline report calculations."""
import io
import unittest

from report import monitor_stats, read_jtl, window_stats


class ReportTest(unittest.TestCase):
    def test_jtl_window_and_failure_classes(self):
        data = read_jtl(io.StringIO(
            'timeStamp,elapsed,label,responseCode,success\n'
            '999,99,knowledge-evaluation,200,true\n'
            '1000,10,knowledge-evaluation,200,true\n'
            '1002,20,knowledge-evaluation,200,false\n'
            '1050,40,knowledge-evaluation,503,false\n'
            '1200,30,knowledge-evaluation,Non HTTP response code: timeout,false\n'
            '2000,90,knowledge-evaluation,200,true\n'
        ))
        stats = window_stats(data, 1000, 2000)
        self.assertEqual(stats["samples"], 4)
        self.assertEqual(stats["http_success_rate_pct"], 50)
        self.assertEqual(stats["business_success_rate_pct"], 25)
        self.assertEqual(stats["error_rate_pct"], 75)
        self.assertEqual(stats["error_counts"], {"http": 1, "transport": 1, "business": 1})
        self.assertEqual((stats["p50_ms"], stats["p95_ms"], stats["p99_ms"]), (20, 40, 40))
        self.assertEqual(stats["throughput_rps"], 4)

    def test_empty_window(self):
        stats = window_stats([], 1000, 3000)
        self.assertEqual(stats["samples"], 0)
        self.assertEqual(stats["duration_s"], 2)
        self.assertEqual(stats["throughput_rps"], 0)
        self.assertIsNone(stats["business_success_rate_pct"])
        self.assertIsNone(stats["p95_ms"])

    def test_invalid_jtl_fails(self):
        for row in [
            '1000,-3,knowledge-evaluation,200,true',
            '1000,2,knowledge-evaluation,200,',
            '1000,2,unknown,200,true',
        ]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                read_jtl(io.StringIO('timeStamp,elapsed,label,responseCode,success\n' + row))

    def test_steady_observed_concurrency_and_timestamps(self):
        data = read_jtl(io.StringIO(
            'timeStamp,elapsed,label,responseCode,success,grpThreads\n'
            '1000,5,knowledge-evaluation,200,true,1\n'
            '1070,65,knowledge-evaluation,200,true,2\n'
        ))
        stats = window_stats(data, 1000, 1200)
        self.assertEqual(stats.get("max_threads"), 2)
        self.assertEqual((stats.get("first_sample_start_ms"), stats.get("last_sample_start_ms")), (1000, 1070))

    def test_monitor_window(self):
        rows = [(1000, "server", "rss", "MiB", 10.), (1100, "server", "rss", "MiB", 30.),
                (1200, "server", "rss", "MiB", 90.), (1150, "generator", "cpu", "%", 5.)]
        result = monitor_stats(rows, 1000, 1200)
        self.assertEqual(result[-1], dict(source="server", metric="rss", unit="MiB",
                                          samples=2, min=10., max=30., mean=20.))


if __name__ == "__main__":
    unittest.main()
