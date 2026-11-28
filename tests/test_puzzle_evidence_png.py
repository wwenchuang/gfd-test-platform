import io
import unittest

from unittest.mock import patch

from PIL import Image

from task_server.services.puzzle_evidence_png import render_evidence


class PuzzleEvidencePngTests(unittest.TestCase):
    def test_png_preserves_the_response_index_unicode_name_and_resource(self):
        request = dict(method="GET", url="https://example.test/puzzle/presets", http=200,
                       business_code=0, queried_at="2026-11-28T03:00:00Z", request_id="example-trace", count=6,
                       headers={"Authorization": "private-example"})
        entry = {"name": "太空探险", "imageUrl": "https://example.test/space.png"}
        source = io.BytesIO()
        Image.new("RGB", (180, 160), "#921fe7").save(source, "PNG")
        screenshot = Image.open(io.BytesIO(render_evidence(request, entry, 4, source.getvalue())))
        self.assertEqual(screenshot.format, "PNG")
        self.assertGreaterEqual(screenshot.width, 900)
        self.assertIn((146, 31, 231), screenshot.convert("RGB").getdata())
        excerpt = screenshot.info["Description"]
        for value in ("[4]", "太空探险", "example-trace", "space.png", request["queried_at"]):
            self.assertIn(value, excerpt)
        self.assertNotIn("private-example", excerpt)

    def test_minimal_linux_host_can_render_names_without_cjk_fonts(self):
        with patch("task_server.services.puzzle_evidence_png._font_path", return_value=None):
            raw = render_evidence({"request_id": "trace"}, {"name": "太空探险"}, 0)
        image = Image.open(io.BytesIO(raw))
        self.assertIn("太空探险", image.info["Description"])
        image.verify()

    def test_absent_response_does_not_invent_an_entry(self):
        screenshot = Image.open(io.BytesIO(render_evidence({"method": "GET"}, None, None)))
        self.assertNotIn("imageUrl", screenshot.info["Description"])
        self.assertNotIn("200", screenshot.info["Description"])
        screenshot.verify()
