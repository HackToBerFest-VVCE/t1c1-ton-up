"""Gemma 4 ledger extraction and defensive JSON parsing engine.

Communicates with Google GenAI / Gemma 4 models to extract handwritten ledger lines,
preserves original scripts/names without translation, and falls back to Demo Mode
fixtures when offline or unconfigured.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("kirana.extractor")

from core.catalog import get_catalog_manager
from core.config import get_config
from core.models import ExtractedLedgerRow, ExtractionResult
from core.validator import get_validator

GEMMA_SYSTEM_INSTRUCTION = """
You are an expert handwritten Indian Kirana (grocery store) ledger digitizer.
Analyze the provided photograph of a handwritten kirana credit/debit notebook (khata book).

CRITICAL EXTRACTION RULES:
1. Preserve verbatim customer names, item names, and line text exactly as written.
   DO NOT translate or anglicize vernacular words (e.g. keep Kannada/Hindi script or phonetic spelling as is).
2. For each transaction line, extract:
   - "date": string in YYYY-MM-DD or DD/MM format if present, otherwise null.
   - "customer": verbatim handwritten customer name or null.
   - "customer_phone": phone number if noted, otherwise null.
   - "item": verbatim handwritten item name.
   - "quantity": numerical quantity as float/int or null.
   - "unit": unit of measure (e.g. "kg", "g", "L", "pkt", "bag", "unit") or null.
   - "unit_price": rate per unit if written, otherwise null.
   - "amount": total line transaction amount as float/int or null.
   - "paid": amount paid / received (jama / rokkha) as float/int or null.
   - "due": unpaid / credit amount (udhaar / baaki) as float/int or null.
   - "raw_text": verbatim transcription of the entire handwritten line.
   - "field_confidence": dictionary of confidence scores (0.0 to 1.0) for fields (e.g. {"item": 0.95, "amount": 0.90}).
   - "candidate_readings": dictionary of alternate readings if handwriting is ambiguous, e.g. {"item": ["Toor Dal", "Moong Dal"]}.
3. DO NOT FABRICATE OR GUESS MISSING VALUES. If a column or value is absent, return null.
4. Output strictly a single JSON array of transaction objects. No markdown explanations.
""".strip()


def clean_json_response(raw_text: str) -> str:
    """Strips markdown fences, preambles, and removes trailing commas."""
    if not raw_text:
        return ""

    text = raw_text.strip()

    # Remove markdown code blocks ```json ... ``` or ``` ... ```
    if "```" in text:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if match:
            text = match.group(1).strip()

    # Find boundaries of JSON array or object
    first_bracket = text.find("[")
    first_brace = text.find("{")

    if first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
        last_bracket = text.rfind("]")
        if last_bracket != -1:
            text = text[first_bracket : last_bracket + 1]
    elif first_brace != -1:
        last_brace = text.rfind("}")
        if last_brace != -1:
            text = text[first_brace : last_brace + 1]

    # Remove trailing commas before closing braces/brackets (common LLM JSON flaw)
    text = re.sub(r",\s*([\]}])", r"\1", text)
    return text.strip()


def repair_and_parse_json(cleaned_text: str) -> Tuple[Any, Optional[str]]:
    """Attempts primary JSON parsing and executes a secondary repair pass if needed."""
    first_err_msg = ""
    # First parsing attempt
    try:
        return json.loads(cleaned_text), None
    except Exception as first_err:
        first_err_msg = str(first_err)

    # Secondary repair attempt:
    # 1. Replace Python booleans/None
    repaired = re.sub(r"\bNone\b", "null", cleaned_text)
    repaired = re.sub(r"\bTrue\b", "true", repaired)
    repaired = re.sub(r"\bFalse\b", "false", repaired)

    # 2. Fix single quotes to double quotes for JSON keys and string values
    # Replace single quotes around keys: 'key': -> "key":
    repaired = re.sub(r"\'([a-zA-Z0-9_]+)\'\s*:", r'"\1":', repaired)
    # Replace single quotes around values: : 'value' -> : "value"
    repaired = re.sub(r":\s*\'([^\']*?)\'", r': "\1"', repaired)

    # 3. Strip remaining trailing commas
    repaired = re.sub(r",\s*([\]}])", r"\1", repaired)

    try:
        return json.loads(repaired), None
    except Exception as second_err:
        return None, f"Failed to parse JSON output: {second_err} (Original error: {first_err_msg})"


def raw_dicts_to_extracted_rows(raw_rows: List[Dict[str, Any]]) -> List[ExtractedLedgerRow]:
    """Converts raw model output dictionaries into validated ExtractedLedgerRow models."""
    validator = get_validator()
    catalog_mgr = get_catalog_manager()
    parsed_rows: List[ExtractedLedgerRow] = []

    for idx, item in enumerate(raw_rows, start=1):
        if not isinstance(item, dict):
            continue

        raw_item_name = str(item.get("item") or "").strip()
        field_conf = item.get("field_confidence") or {}
        if not isinstance(field_conf, dict):
            field_conf = {}

        candidate_readings = item.get("candidate_readings") or {}
        if not isinstance(candidate_readings, dict):
            candidate_readings = {}

        # Safe float conversion without inventing default values
        def _to_float(v: Any) -> Optional[float]:
            if v is None:
                return None
            try:
                return float(str(v).replace(",", "").strip())
            except (ValueError, TypeError):
                return None

        row = ExtractedLedgerRow(
            row_id=int(item.get("row_id") or idx),
            date=str(item["date"]).strip() if item.get("date") else None,
            customer=str(item["customer"]).strip() if item.get("customer") else None,
            customer_phone=str(item["customer_phone"]).strip() if item.get("customer_phone") else None,
            item=raw_item_name,
            quantity=_to_float(item.get("quantity")),
            unit=str(item["unit"]).strip() if item.get("unit") else None,
            unit_price=_to_float(item.get("unit_price")),
            amount=_to_float(item.get("amount")),
            paid=_to_float(item.get("paid")),
            due=_to_float(item.get("due")),
            raw_text=str(item.get("raw_text") or "").strip(),
            field_confidence={str(k): float(v) for k, v in field_conf.items() if isinstance(v, (int, float))},
            candidate_readings={str(k): list(v) if isinstance(v, list) else [str(v)] for k, v in candidate_readings.items()},
        )

        # Standard catalog matching (Milestone 1 integration)
        if row.item:
            match = catalog_mgr.find_match(row.item, threshold=75.0)
            if match:
                row.standardized_item = match[0].name_en

        # Financial and data validation (Milestone 2)
        validated_row = validator.validate_row(row)
        parsed_rows.append(validated_row)

    return parsed_rows


class GemmaLedgerExtractor:
    """Extracts structured ledger rows from images using Gemma 4 or Demo Mode fixtures."""

    def __init__(self, fixtures_dir: Optional[Path] = None) -> None:
        if fixtures_dir is None:
            fixtures_dir = Path(__file__).resolve().parent.parent / "data" / "sample_extractions"
        self.fixtures_dir = fixtures_dir
        self.config = get_config()
        self._cache: Dict[str, ExtractionResult] = {}

    def get_available_fixtures(self) -> List[str]:
        """Returns filenames of available cached extraction fixtures."""
        if not self.fixtures_dir.exists():
            return []
        return [f.name for f in sorted(self.fixtures_dir.glob("*.json"))]

    def load_fixture(
        self,
        fixture_name: str,
        preprocessing_time_ms: float = 0.0,
        payload_size_bytes: int = 0,
    ) -> ExtractionResult:
        """Loads and validates a cached demo extraction fixture with timing metrics."""
        fixture_path = self.fixtures_dir / fixture_name
        if not fixture_path.exists():
            return ExtractionResult(
                success=False,
                error_message=f"Demo fixture '{fixture_name}' not found at {fixture_path}",
                is_demo=True,
                payload_size_bytes=payload_size_bytes,
            )

        try:
            t_parse_start = time.perf_counter()
            with open(fixture_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                if isinstance(data, dict) and "rows" in data:
                    data = data["rows"]
                else:
                    return ExtractionResult(
                        success=False,
                        error_message="Fixture must contain a JSON array of ledger rows.",
                        is_demo=True,
                        payload_size_bytes=payload_size_bytes,
                    )

            rows = raw_dicts_to_extracted_rows(data)
            needs_review_count = sum(1 for r in rows if r.needs_review)
            parse_ms = round((time.perf_counter() - t_parse_start) * 1000.0, 2)
            total_ms = round(preprocessing_time_ms + parse_ms, 2)

            return ExtractionResult(
                success=True,
                rows=rows,
                model_used="Cached Fixture (" + fixture_name + ")",
                is_demo=True,
                total_rows=len(rows),
                rows_needing_review=needs_review_count,
                preprocessing_time_ms=preprocessing_time_ms,
                api_latency_ms=0.0,
                parsing_validation_time_ms=parse_ms,
                total_request_time_ms=total_ms,
                payload_size_bytes=payload_size_bytes,
                latency_breakdown={
                    "preprocessing_ms": preprocessing_time_ms,
                    "api_latency_ms": 0.0,
                    "parsing_validation_ms": parse_ms,
                    "total_request_ms": total_ms,
                },
            )
        except Exception as e:
            return ExtractionResult(
                success=False,
                error_message=f"Error reading demo fixture: {e}",
                is_demo=True,
                payload_size_bytes=payload_size_bytes,
            )

    def extract_from_image(
        self,
        image_bytes: bytes,
        use_demo: bool = False,
        demo_fixture_name: str = "kannada_ledger_sample.json",
        preprocessing_time_ms: float = 0.0,
        force_refresh: bool = False,
    ) -> ExtractionResult:
        """Extracts ledger records from preprocessed image bytes with latency instrumentation.

        If use_demo is True, loads from cached demo fixtures.
        Otherwise calls Google GenAI / Gemma 4 model with strict defensive error handling.
        """
        payload_size_bytes = len(image_bytes) if image_bytes else 0

        # Check in-memory extraction cache to prevent redundant API calls
        cache_key = hashlib.sha256(
            (image_bytes or b"") + str(use_demo).encode() + demo_fixture_name.encode()
        ).hexdigest()
        if not force_refresh and cache_key in self._cache:
            cached_res = self._cache[cache_key]
            logger.info("Extraction cache hit (key=%s), returning cached rows", cache_key[:8])
            breakdown = dict(cached_res.latency_breakdown or {})
            breakdown["from_cache"] = True
            return ExtractionResult(
                success=cached_res.success,
                rows=cached_res.rows,
                error_message=cached_res.error_message,
                model_used=cached_res.model_used,
                raw_response_text=cached_res.raw_response_text,
                is_demo=cached_res.is_demo,
                total_rows=cached_res.total_rows,
                rows_needing_review=cached_res.rows_needing_review,
                preprocessing_time_ms=cached_res.preprocessing_time_ms,
                api_latency_ms=0.0,
                parsing_validation_time_ms=cached_res.parsing_validation_time_ms,
                total_request_time_ms=cached_res.total_request_time_ms,
                payload_size_bytes=cached_res.payload_size_bytes,
                latency_breakdown=breakdown,
            )

        if use_demo:
            res = self.load_fixture(
                demo_fixture_name,
                preprocessing_time_ms=preprocessing_time_ms,
                payload_size_bytes=payload_size_bytes,
            )
            self._cache[cache_key] = res
            return res

        # Check API key configuration
        if not self.config.is_gemma_configured:
            return ExtractionResult(
                success=False,
                error_message=(
                    "Gemma API key is not configured. "
                    "Please set GEMMA_API_KEY in your .env file or Streamlit secrets, "
                    "or enable Demo Mode to test with pre-validated sample ledger sheets."
                ),
                is_demo=False,
                preprocessing_time_ms=preprocessing_time_ms,
                payload_size_bytes=payload_size_bytes,
            )

        # Check SDK availability
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return ExtractionResult(
                success=False,
                error_message=(
                    "The 'google-genai' SDK is not installed in the current environment. "
                    "Install dependencies using 'pip install -r requirements.txt', "
                    "or enable Demo Mode to test the extraction workflow."
                ),
                is_demo=False,
                preprocessing_time_ms=preprocessing_time_ms,
                payload_size_bytes=payload_size_bytes,
            )

        # Call live Gemma model
        t_api_start = time.perf_counter()
        model_name = self.config.gemma_model_name or "gemma-4-26b-a4b-it"
        if model_name == "gemma-4":
            model_name = "gemma-4-26b-a4b-it"

        try:
            client = genai.Client(api_key=self.config.gemma_api_key)

            # Auto-detect image MIME type based on byte header
            is_jpeg = image_bytes.startswith(b"\xff\xd8")
            mime_type = "image/jpeg" if is_jpeg else "image/png"

            image_part = types.Part.from_bytes(
                data=image_bytes,
                mime_type=mime_type,
            )

            prompt = (
                f"{GEMMA_SYSTEM_INSTRUCTION}\n\n"
                "Examine this handwritten kirana ledger sheet and extract all transaction rows into a JSON array:"
            )

            # Enforce structured JSON decoding at model level to eliminate preamble tokens
            gen_config = types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
            )

            response = client.models.generate_content(
                model=model_name,
                contents=[image_part, prompt],
                config=gen_config,
            )
            api_latency_ms = round((time.perf_counter() - t_api_start) * 1000.0, 2)

            t_parse_start = time.perf_counter()
            raw_text = response.text or ""
            cleaned = clean_json_response(raw_text)
            parsed_data, parse_err = repair_and_parse_json(cleaned)

            if parse_err or parsed_data is None:
                parse_ms = round((time.perf_counter() - t_parse_start) * 1000.0, 2)
                return ExtractionResult(
                    success=False,
                    error_message=f"Model output parsing error: {parse_err}",
                    model_used=model_name,
                    raw_response_text=raw_text,
                    is_demo=False,
                    preprocessing_time_ms=preprocessing_time_ms,
                    api_latency_ms=api_latency_ms,
                    parsing_validation_time_ms=parse_ms,
                    total_request_time_ms=round(preprocessing_time_ms + api_latency_ms + parse_ms, 2),
                    payload_size_bytes=payload_size_bytes,
                )

            if isinstance(parsed_data, dict):
                # Unwrap dictionary if model wrapped in {"transactions": [...]}
                for key in ("rows", "transactions", "ledger_entries", "items"):
                    if key in parsed_data and isinstance(parsed_data[key], list):
                        parsed_data = parsed_data[key]
                        break
                if isinstance(parsed_data, dict):
                    parsed_data = [parsed_data]

            if not isinstance(parsed_data, list):
                parse_ms = round((time.perf_counter() - t_parse_start) * 1000.0, 2)
                return ExtractionResult(
                    success=False,
                    error_message="Model returned invalid JSON structure (expected an array).",
                    model_used=model_name,
                    raw_response_text=raw_text,
                    is_demo=False,
                    preprocessing_time_ms=preprocessing_time_ms,
                    api_latency_ms=api_latency_ms,
                    parsing_validation_time_ms=parse_ms,
                    total_request_time_ms=round(preprocessing_time_ms + api_latency_ms + parse_ms, 2),
                    payload_size_bytes=payload_size_bytes,
                )

            rows = raw_dicts_to_extracted_rows(parsed_data)
            needs_review_count = sum(1 for r in rows if r.needs_review)
            parse_ms = round((time.perf_counter() - t_parse_start) * 1000.0, 2)
            total_ms = round(preprocessing_time_ms + api_latency_ms + parse_ms, 2)

            res = ExtractionResult(
                success=True,
                rows=rows,
                model_used=model_name,
                raw_response_text=raw_text,
                is_demo=False,
                total_rows=len(rows),
                rows_needing_review=needs_review_count,
                preprocessing_time_ms=preprocessing_time_ms,
                api_latency_ms=api_latency_ms,
                parsing_validation_time_ms=parse_ms,
                total_request_time_ms=total_ms,
                payload_size_bytes=payload_size_bytes,
                latency_breakdown={
                    "preprocessing_ms": preprocessing_time_ms,
                    "api_latency_ms": api_latency_ms,
                    "parsing_validation_ms": parse_ms,
                    "total_request_ms": total_ms,
                },
            )

            # Store in cache
            self._cache[cache_key] = res

            logger.info(
                "Gemma Ledger Extraction: Total=%.1fms (Prep=%.1fms, API=%.1fms, Parse/Val=%.1fms), Rows=%d, Payload=%d bytes",
                total_ms,
                preprocessing_time_ms,
                api_latency_ms,
                parse_ms,
                len(rows),
                payload_size_bytes,
            )

            return res

        except Exception as e:
            api_latency_ms = round((time.perf_counter() - t_api_start) * 1000.0, 2)
            err_msg = str(e)
            err_type = type(e).__name__
            if "API_KEY_INVALID" in err_msg or "401" in err_msg or "invalid api key" in err_msg.lower():
                guidance = f"Invalid Gemma API Key: Please verify that GEMMA_API_KEY is correctly set in your .env or Streamlit secrets. ({err_msg})"
            elif "403" in err_msg or "PERMISSION_DENIED" in err_msg:
                guidance = f"Permission Denied for model '{model_name}': Verify your Google AI Studio project permissions or try a supported model identifier. ({err_msg})"
            elif "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
                guidance = f"API Quota / Rate limit exceeded for '{model_name}'. Please wait and retry. ({err_msg})"
            elif "NOT_FOUND" in err_msg or "404" in err_msg:
                guidance = f"Model '{model_name}' not found or unsupported for multimodal generateContent: Verify GEMMA_MODEL configuration. ({err_msg})"
            elif "timeout" in err_msg.lower() or "deadline" in err_msg.lower():
                guidance = f"Gemma API request timed out ({err_msg}). The system automatically trimmed payload size; please retry."
            else:
                guidance = f"Gemma extraction request failed ({err_type}): {err_msg}"

            logger.error("Gemma extraction error after %.1fms: %s", api_latency_ms, err_msg)

            return ExtractionResult(
                success=False,
                error_message=guidance,
                model_used=model_name,
                is_demo=False,
                preprocessing_time_ms=preprocessing_time_ms,
                api_latency_ms=api_latency_ms,
                total_request_time_ms=round(preprocessing_time_ms + api_latency_ms, 2),
                payload_size_bytes=payload_size_bytes,
            )


_GLOBAL_EXTRACTOR: Optional[GemmaLedgerExtractor] = None


def get_extractor() -> GemmaLedgerExtractor:
    """Returns singleton GemmaLedgerExtractor instance."""
    global _GLOBAL_EXTRACTOR
    if _GLOBAL_EXTRACTOR is None:
        _GLOBAL_EXTRACTOR = GemmaLedgerExtractor()
    return _GLOBAL_EXTRACTOR
