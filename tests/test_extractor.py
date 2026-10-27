"""Unit tests for Gemma 4 ledger extraction, JSON defense, and Demo Mode."""

import json
import unittest
from unittest.mock import MagicMock, patch

from core.config import AppConfig
from core.extractor import (
    GemmaLedgerExtractor,
    clean_json_response,
    get_extractor,
    repair_and_parse_json,
)
from core.models import ExtractionResult


class TestGemmaExtractor(unittest.TestCase):
    """Verifies JSON sanitization, Demo Mode fixtures, and mocked Gemma 4 API calls."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.extractor = get_extractor()

    def test_clean_json_response_with_markdown_fences(self) -> None:
        raw_llm_output = """
        Here is the extracted transaction list:
        ```json
        [
            {"item": "Toor Dal", "amount": 320.0},
            {"item": "Sugar", "amount": 250.0}
        ]
        ```
        Hope this helps!
        """
        cleaned = clean_json_response(raw_llm_output)
        self.assertTrue(cleaned.startswith("["))
        self.assertTrue(cleaned.endswith("]"))
        parsed = json.loads(cleaned)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["item"], "Toor Dal")

    def test_clean_json_removes_trailing_commas(self) -> None:
        malformed_json_str = """
        [
            {"item": "Toor Dal", "amount": 320.0, },
            {"item": "Sugar", "amount": 250.0, },
        ]
        """
        cleaned = clean_json_response(malformed_json_str)
        parsed, err = repair_and_parse_json(cleaned)
        self.assertIsNone(err)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed), 2)

    def test_repair_single_quotes_and_literals(self) -> None:
        python_style_str = "[{'item': 'Rice', 'amount': 60.0, 'paid': None}]"
        parsed, err = repair_and_parse_json(python_style_str)
        self.assertIsNone(err)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0]["item"], "Rice")
        self.assertIsNone(parsed[0]["paid"])

    def test_unparseable_output_returns_error_cleanly(self) -> None:
        garbage = "Sorry, I cannot read this image because it is completely blurred."
        parsed, err = repair_and_parse_json(garbage)
        self.assertIsNone(parsed)
        self.assertIsNotNone(err)
        self.assertIn("Failed to parse", err)

    def test_demo_mode_loads_kannada_fixture(self) -> None:
        result = self.extractor.extract_from_image(
            image_bytes=b"dummy_bytes",
            use_demo=True,
            demo_fixture_name="kannada_ledger_sample.json",
        )
        self.assertTrue(result.success)
        self.assertTrue(result.is_demo)
        self.assertEqual(result.total_rows, 5)
        # Verify original script is preserved (not translated)
        self.assertEqual(result.rows[0].customer, "ರಮೇಶ್")
        self.assertEqual(result.rows[0].item, "ತೊಗರಿ ಬೇಳೆ")
        # Row 2 has 100+100 != 250
        self.assertTrue(result.rows[1].needs_review)
        # Row 3 has 24/10 date (year inferred)
        self.assertTrue(result.rows[2].needs_review)
        # Row 4 has low confidence (0.52)
        self.assertTrue(result.rows[3].needs_review)

    def test_demo_mode_loads_hindi_fixture(self) -> None:
        result = self.extractor.extract_from_image(
            image_bytes=b"dummy_bytes",
            use_demo=True,
            demo_fixture_name="hindi_mixed_ledger_sample.json",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.total_rows, 4)
        self.assertEqual(result.rows[0].customer, "राकेश जी")

    def test_missing_api_configuration_returns_helpful_error(self) -> None:
        # Test with empty API key
        with patch.object(self.extractor.config, "gemma_api_key", ""):
            result = self.extractor.extract_from_image(
                image_bytes=b"fake_image_bytes",
                use_demo=False,
            )
            self.assertFalse(result.success)
            self.assertIn("Gemma API key is not configured", result.error_message)

    @patch("core.extractor.get_config")
    def test_mocked_live_gemma_api_call(self, mock_get_cfg: MagicMock) -> None:
        """Simulates successful Gemma 4 multimodal API call via mocked google.genai."""
        mock_cfg = MagicMock()
        mock_cfg.is_gemma_configured = True
        mock_cfg.gemma_api_key = "test_valid_key_12345"
        mock_cfg.gemma_model_name = "gemma-4-26b-a4b-it"
        mock_get_cfg.return_value = mock_cfg

        mock_response = MagicMock()
        mock_response.text = json.dumps([
            {
                "row_id": 1,
                "date": "2026-10-24",
                "customer": "Suresh",
                "item": "Toor Dal",
                "quantity": 1.0,
                "unit": "kg",
                "amount": 160.0,
                "paid": 160.0,
                "due": 0.0,
                "raw_text": "Suresh Toor Dal 1kg 160 paid",
                "field_confidence": {"item": 0.98, "amount": 0.99},
            }
        ])

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        mock_google = MagicMock()
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_google.genai = mock_genai
        mock_genai.Client.return_value = mock_client
        mock_part = MagicMock()
        mock_types.Part.from_bytes.return_value = mock_part
        mock_genai.types = mock_types

        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai, "google.genai.types": mock_types}):
            extractor = GemmaLedgerExtractor()
            extractor.config = mock_cfg
            test_image_bytes = b"preprocessed_png_bytes_12345"

            # Demo Mode OFF -> Live API invocation
            result = extractor.extract_from_image(
                image_bytes=test_image_bytes,
                use_demo=False,
            )

            # 1. Verify Client initialized with configured GEMMA_API_KEY
            mock_genai.Client.assert_called_once_with(api_key="test_valid_key_12345")

            # 2. Verify Part.from_bytes called with uploaded preprocessed image bytes
            mock_types.Part.from_bytes.assert_called_once_with(
                data=test_image_bytes,
                mime_type="image/png",
            )

            # 3. Verify generate_content called with model 'gemma-4-26b-a4b-it'
            mock_client.models.generate_content.assert_called_once()
            call_kwargs = mock_client.models.generate_content.call_args.kwargs
            self.assertEqual(call_kwargs.get("model"), "gemma-4-26b-a4b-it")

            # 4. Verify results
            self.assertTrue(result.success, f"Extraction failed: {result.error_message}")
            self.assertEqual(result.model_used, "gemma-4-26b-a4b-it")
            self.assertFalse(result.is_demo)
            self.assertEqual(result.total_rows, 1)
            self.assertEqual(result.rows[0].item, "Toor Dal")
            self.assertEqual(result.rows[0].amount, 160.0)

    @patch("core.extractor.get_config")
    def test_mocked_live_gemma_api_error_clarity(self, mock_get_cfg: MagicMock) -> None:
        """Verifies that API errors during live extraction are caught and presented clearly."""
        mock_cfg = MagicMock()
        mock_cfg.is_gemma_configured = True
        mock_cfg.gemma_api_key = "test_valid_key_12345"
        mock_cfg.gemma_model_name = "gemma-4-26b-a4b-it"
        mock_get_cfg.return_value = mock_cfg

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("403 PERMISSION_DENIED: User not allowed to access model")

        mock_google = MagicMock()
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_google.genai = mock_genai
        mock_genai.Client.return_value = mock_client
        mock_genai.types = mock_types

        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai, "google.genai.types": mock_types}):
            extractor = GemmaLedgerExtractor()
            extractor.config = mock_cfg

            # Demo Mode OFF -> Live API call fails
            result = extractor.extract_from_image(
                image_bytes=b"sample_bytes",
                use_demo=False,
            )

            self.assertFalse(result.success)
            self.assertFalse(result.is_demo)
            self.assertIn("Permission Denied for model 'gemma-4-26b-a4b-it'", result.error_message)
            self.assertEqual(result.model_used, "gemma-4-26b-a4b-it")

    def test_extraction_result_latency_fields(self) -> None:
        """Verifies that latency breakdown and timing fields are populated on ExtractionResult."""
        extractor = GemmaLedgerExtractor()
        test_bytes = b"sample_bytes_for_latency_test"
        result = extractor.extract_from_image(
            image_bytes=test_bytes,
            use_demo=True,
            demo_fixture_name="kannada_ledger_sample.json",
            preprocessing_time_ms=15.4,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.preprocessing_time_ms, 15.4)
        self.assertGreater(result.total_request_time_ms, 0)
        self.assertIn("preprocessing_ms", result.latency_breakdown)
        self.assertIn("api_latency_ms", result.latency_breakdown)
        self.assertIn("parsing_validation_ms", result.latency_breakdown)
        self.assertIn("total_request_ms", result.latency_breakdown)
        self.assertEqual(result.payload_size_bytes, len(test_bytes))

    def test_extraction_in_memory_cache(self) -> None:
        """Verifies that duplicate extractions for the exact same image bytes hit the cache."""
        extractor = GemmaLedgerExtractor()
        test_bytes = b"unique_image_bytes_cache_test_99"
        res1 = extractor.extract_from_image(
            image_bytes=test_bytes,
            use_demo=True,
            demo_fixture_name="kannada_ledger_sample.json",
            preprocessing_time_ms=10.0,
        )
        self.assertTrue(res1.success)
        self.assertFalse(res1.latency_breakdown.get("from_cache", False))

        res2 = extractor.extract_from_image(
            image_bytes=test_bytes,
            use_demo=True,
            demo_fixture_name="kannada_ledger_sample.json",
            preprocessing_time_ms=10.0,
        )
        self.assertTrue(res2.success)
        self.assertTrue(res2.latency_breakdown.get("from_cache", False))
        self.assertEqual(res2.total_rows, res1.total_rows)

    @patch("core.extractor.get_config")
    def test_mocked_live_jpeg_mime_detection(self, mock_get_cfg: MagicMock) -> None:
        """Verifies that JPEG magic bytes b'\\xff\\xd8' are mapped to image/jpeg in Gemma API calls."""
        mock_cfg = MagicMock()
        mock_cfg.is_gemma_configured = True
        mock_cfg.gemma_api_key = "test_valid_key_12345"
        mock_cfg.gemma_model_name = "gemma-4-26b-a4b-it"
        mock_get_cfg.return_value = mock_cfg

        mock_response = MagicMock()
        mock_response.text = json.dumps([{"row_id": 1, "item": "Rice", "amount": 100.0}])

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        mock_google = MagicMock()
        mock_genai = MagicMock()
        mock_types = MagicMock()
        mock_google.genai = mock_genai
        mock_genai.Client.return_value = mock_client
        mock_genai.types = mock_types

        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai, "google.genai.types": mock_types}):
            extractor = GemmaLedgerExtractor()
            extractor.config = mock_cfg
            jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"

            result = extractor.extract_from_image(
                image_bytes=jpeg_bytes,
                use_demo=False,
            )
            self.assertTrue(result.success)
            mock_types.Part.from_bytes.assert_called_once_with(
                data=jpeg_bytes,
                mime_type="image/jpeg",
            )


if __name__ == "__main__":
    unittest.main()
