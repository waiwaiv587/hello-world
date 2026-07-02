import unittest
import xml.etree.ElementTree as ET

from polymarket_paper.svgchart import Series, line_chart, nice_ticks


class TestNiceTicks(unittest.TestCase):
    def test_unit_interval(self):
        ticks = nice_ticks(0.0, 1.0)
        self.assertIn(0.0, ticks)
        self.assertIn(1.0, ticks)
        self.assertEqual(ticks, sorted(ticks))

    def test_small_range(self):
        ticks = nice_ticks(0.0, 0.27)
        self.assertTrue(all(0 <= t <= 0.27 + 1e-9 for t in ticks))
        self.assertGreaterEqual(len(ticks), 3)


class TestLineChart(unittest.TestCase):
    def _chart(self) -> str:
        return line_chart(
            [Series("own_prob", [(0.1, 0.05), (0.5, 0.55), (0.9, 0.95)],
                    "--series-1", marker="circle",
                    tooltips=["a", "b", "c"]),
             Series("市场中间价", [(0.1, 0.1), (0.5, 0.5), (0.9, 0.9)],
                    "--series-2", marker="square")],
            title="校准曲线", x_label="预测", y_label="实际",
            x_range=(0, 1), y_range=(0, 1), diagonal=True)

    def test_valid_xml(self):
        root = ET.fromstring(self._chart())
        self.assertTrue(root.tag.endswith("svg"))

    def test_contains_series_and_legend(self):
        svg = self._chart()
        self.assertIn("var(--series-1)", svg)
        self.assertIn("var(--series-2)", svg)
        self.assertIn("own_prob", svg)
        self.assertIn("市场中间价", svg)
        self.assertIn("stroke-dasharray", svg)   # 对角参考线
        self.assertIn("<title>a</title>", svg)   # 悬停提示

    def test_marker_shapes_differ(self):
        svg = self._chart()
        self.assertIn("<circle", svg)
        self.assertIn('width="8" height="8"', svg)   # 方块标记


if __name__ == "__main__":
    unittest.main()
