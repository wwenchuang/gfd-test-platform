import io
import unittest

from PIL import Image, ImageDraw

from task_server.services.puzzle_image_evidence import match_preset


def encoded(image, fmt="PNG"):
    data = io.BytesIO()
    image.save(data, fmt)
    return data.getvalue()


def artwork():
    image = Image.new("RGB", (140, 140), "#86cbea")
    draw = ImageDraw.Draw(image)
    draw.ellipse((39, 18, 98, 73), fill="#0e385d")
    draw.polygon([(14, 122), (53, 70), (79, 111), (100, 56), (130, 124)], fill="#fe6530")
    draw.rectangle((44, 38, 59, 55), fill="white")
    return image


def screen(art, locations, side, crop=0):
    image = Image.new("RGB", (300, 560), "#f7f7f7")
    patch = art.crop((crop, crop, art.width-crop, art.height-crop)).resize((side, side))
    for point in locations:
        image.paste(patch, point)
    return image


class PuzzleImageTests(unittest.TestCase):
    def match(self, board, shot, resource):
        return match_preset(encoded(board), encoded(shot, "JPEG"), encoded(resource))

    def test_scaled_moved_and_slightly_cropped(self):
        art = artwork()
        result = self.match(screen(art, [(148, 121)], 58), screen(art, [(22, 347)], 82, 5), art)
        self.assertTrue(result["matched"], result)
        for label, expected in (("board", (148, 121, 206, 179)), ("shot", (22, 347, 104, 429))):
            for actual, target in zip(result[label]["bbox"], expected):
                self.assertLessEqual(abs(actual-target), 6, result)

    def test_duplicates_are_ambiguous(self):
        art = artwork()
        result = self.match(screen(art, [(23, 120), (181, 242)], 67), screen(art, [(20, 292)], 78), art)
        self.assertFalse(result["matched"], result)
        self.assertEqual(result["board"]["status"], "ambiguous")
        self.assertIsNone(result["board"]["bbox"])

    def test_second_matching_artwork_with_a_light_overlay_is_ambiguous(self):
        art = artwork()
        board = screen(art, [(15, 22)], 70)
        tinted = Image.blend(art, Image.new("RGB", art.size, "white"), .09)
        board.paste(tinted.resize((80, 80)), (127, 307))
        result = self.match(board, screen(art, [(126, 200)], 73), art)
        self.assertEqual(result["board"]["status"], "ambiguous", result)

    def test_invalid_image_preserves_the_other_side(self):
        art = artwork()
        result = match_preset(b"invalid file", encoded(screen(art, [(22, 122)], 81)), encoded(art))
        self.assertFalse(result["matched"])
        self.assertEqual(result["board"]["status"], "invalid_image")
        self.assertEqual(result["shot"]["status"], "matched")

    def test_similar_colors_different_arrangement(self):
        art = artwork()
        impostor = art.transpose(Image.Transpose.ROTATE_180)
        result = self.match(screen(art, [(25, 15)], 70), screen(impostor, [(35, 335)], 85), art)
        self.assertEqual(result["shot"]["status"], "no_match", result)
        self.assertFalse(result["matched"])

    def test_smooth_gradient_is_not_distinctive_artwork(self):
        gradient = Image.linear_gradient("L").convert("RGB").resize((120, 120))
        result = self.match(screen(gradient, [(10, 80)], 70), screen(gradient, [(190, 15)], 72), gradient)
        self.assertFalse(result["matched"], result)
        self.assertEqual(result["board"]["status"], "insufficient_detail")

    def test_low_detail_cannot_establish_identity(self):
        flat = Image.new("RGB", (100, 100), "#abcdef")
        result = self.match(screen(flat, [(22, 30)], 82), screen(flat, [(44, 290)], 75), flat)
        self.assertFalse(result["matched"], result)
        self.assertEqual(result["shot"]["status"], "insufficient_detail")


if __name__ == "__main__":
    unittest.main()
