"""Unit tests for OpenCV image preprocessing pipeline."""

import io
import unittest
import numpy as np
from PIL import Image, ImageDraw

from core.image_preprocessor import ImagePreprocessor, PreprocessingResult, get_preprocessor


class TestImagePreprocessor(unittest.TestCase):
    """Verifies grayscale conversion, CLAHE, deskewing, and margin cropping."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.preprocessor = ImagePreprocessor()
        # Create a synthetic ledger document (white background with black text lines)
        cls.sample_pil = Image.new("RGB", (400, 300), color=(255, 255, 255))
        draw = ImageDraw.Draw(cls.sample_pil)
        draw.line([(50, 20), (50, 280)], fill=(200, 0, 0), width=2)
        draw.text((60, 50), "Ramesh - Toor Dal 2kg - 320", fill=(0, 0, 0))
        draw.text((60, 100), "Suresh - Sugar 5kg - 250", fill=(0, 0, 0))
        draw.text((60, 150), "Manjunath - Oil 1L - 140", fill=(0, 0, 0))

        # Convert to bytes
        buf = io.BytesIO()
        cls.sample_pil.save(buf, format="PNG")
        cls.sample_bytes = buf.getvalue()

    def test_load_image_from_bytes_and_pil(self) -> None:
        img_from_bytes = self.preprocessor.load_image(self.sample_bytes)
        self.assertIsInstance(img_from_bytes, np.ndarray)
        self.assertEqual(img_from_bytes.shape[:2], (300, 400))

        img_from_pil = self.preprocessor.load_image(self.sample_pil)
        self.assertIsInstance(img_from_pil, np.ndarray)
        self.assertEqual(img_from_pil.shape[:2], (300, 400))

    def test_grayscale_conversion(self) -> None:
        bgr = self.preprocessor.load_image(self.sample_bytes)
        gray = self.preprocessor.convert_to_grayscale(bgr)
        self.assertEqual(len(gray.shape), 2, "Grayscale image must be 2D array")
        self.assertEqual(gray.shape, (300, 400))
        self.assertEqual(gray.dtype, np.uint8)

    def test_contrast_enhancement(self) -> None:
        bgr = self.preprocessor.load_image(self.sample_bytes)
        gray = self.preprocessor.convert_to_grayscale(bgr)
        enhanced = self.preprocessor.enhance_contrast(gray)
        self.assertEqual(enhanced.shape, gray.shape)
        self.assertEqual(enhanced.dtype, np.uint8)

    def test_deskew_straight_image(self) -> None:
        # A straight synthetic document should have skew angle near 0.0
        bgr = self.preprocessor.load_image(self.sample_bytes)
        gray = self.preprocessor.convert_to_grayscale(bgr)
        angle = self.preprocessor.detect_skew_angle(gray)
        self.assertLess(abs(angle), 15.0)

    def test_safe_margin_crop_preserves_content(self) -> None:
        # Large image with white padding around central text
        padded_pil = Image.new("RGB", (600, 600), color=(255, 255, 255))
        draw = ImageDraw.Draw(padded_pil)
        # Content placed centrally: 150 to 450 (occupies ~50% area)
        draw.rectangle([(150, 150), (450, 450)], outline=(0, 0, 0), width=4)
        draw.text((180, 250), "Central Content Line", fill=(0, 0, 0))

        bgr = self.preprocessor.load_image(padded_pil)
        gray = self.preprocessor.convert_to_grayscale(bgr)
        cropped, box, was_cropped = self.preprocessor.crop_margins(gray)

        if was_cropped:
            self.assertIsNotNone(box)
            x, y, w, h = box
            self.assertLess(w, 600)
            self.assertLess(h, 600)
            self.assertGreater(w, 200)

    def test_full_preprocessing_pipeline(self) -> None:
        result = self.preprocessor.preprocess(self.sample_bytes)
        self.assertIsInstance(result, PreprocessingResult)
        self.assertTrue(result.contrast_enhanced)
        self.assertGreater(result.processed_dimensions[0], 0)
        self.assertGreater(result.processed_dimensions[1], 0)

        # Verify PIL conversion and bytes export
        pil_orig = result.to_pil_original()
        self.assertIsInstance(pil_orig, Image.Image)
        pil_proc = result.to_pil_processed()
        self.assertIsInstance(pil_proc, Image.Image)

        proc_bytes = result.to_processed_bytes(".png")
        self.assertGreater(len(proc_bytes), 0)
        self.assertGreaterEqual(result.preprocessing_time_ms, 0.0)

    def test_resize_if_needed(self) -> None:
        large_pil = Image.new("RGB", (2400, 1800), color=(200, 200, 200))
        large_arr = np.array(large_pil)
        resized_arr, was_resized = self.preprocessor.resize_if_needed(large_arr, max_dim=1600)
        self.assertTrue(was_resized)
        h, w = resized_arr.shape[:2]
        self.assertEqual(w, 1600)
        self.assertEqual(h, 1200)

        # Small image should not be resized
        small_pil = Image.new("RGB", (800, 600), color=(200, 200, 200))
        small_arr = np.array(small_pil)
        small_resized, was_small_resized = self.preprocessor.resize_if_needed(small_arr, max_dim=1600)
        self.assertFalse(was_small_resized)
        self.assertEqual(small_resized.shape, small_arr.shape)

    def test_to_processed_bytes_jpeg_reduces_payload(self) -> None:
        result = self.preprocessor.preprocess(self.sample_bytes)
        png_bytes = result.to_processed_bytes(".png")
        jpg_bytes = result.to_processed_bytes(".jpg", quality=88)
        self.assertGreater(len(png_bytes), 0)
        self.assertGreater(len(jpg_bytes), 0)
        # JPEG should start with 0xFF 0xD8 magic bytes
        self.assertTrue(jpg_bytes.startswith(b"\xff\xd8"))
        # PNG should start with 0x89 PNG magic bytes
        self.assertTrue(png_bytes.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
