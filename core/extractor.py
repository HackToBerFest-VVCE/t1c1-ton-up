"""Gemma 4 ledger extraction and defensive JSON parsing engine.

Communicates with Google GenAI / Gemma 4 models to extract handwritten ledger lines,
preserves original scripts/names without translation, and falls back to Demo Mode
fixtures when offline or unconfigured.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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

    def get_available_fixtures(self) -> List[str]:
        """Returns filenames of available cached extraction fixtures."""
        if not self.fixtures_dir.exists():
            return []
        return [f.name for f in sorted(self.fixtures_dir.glob("*.json"))]

    def load_fixture(self, fixture_name: str) -> ExtractionResult:
        """Loads and validates a cached demo extraction fixture."""
        fixture_path = self.fixtures_dir / fixture_name
        if not fixture_path.exists():
            return ExtractionResult(
                success=False,
                error_message=f"Demo fixture '{fixture_name}' not found at {fixture_path}",
                is_demo=True,
            )

        try:
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
                    )

            rows = raw_dicts_to_extracted_rows(data)
            needs_review_count = sum(1 for r in rows if r.needs_review)

            return ExtractionResult(
                success=True,
                rows=rows,
                model_used="Cached Fixture (" + fixture_name + ")",
                is_demo=True,
                total_rows=len(rows),
                rows_needing_review=needs_review_count,
            )
        except Exception as e:
            return ExtractionResult(
                success=False,
                error_message=f"Error reading demo fixture: {e}",
                is_demo=True,
            )

    def extract_from_image(
        self,
        image_bytes: bytes,
        use_demo: bool = False,
        demo_fixture_name: str = "kannada_ledger_sample.json",
    ) -> ExtractionResult:
        """Extracts ledger records from preprocessed image bytes.

        If use_demo is True, loads from cached demo fixtures.
        Otherwise calls Google GenAI / Gemma 4 model with strict defensive error handling.
        """
        if use_demo:
            return self.load_fixture(demo_fixture_name)

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
            )

        # Call live Gemma model
        try:
            client = genai.Client(api_key=self.config.gemma_api_key)
            model_name = self.config.gemma_model_name or "gemma-4-26b-a4b-it"
            if model_name == "gemma-4":
                model_name = "gemma-4-26b-a4b-it"

            image_part = types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/png",
            )

            prompt = (
                f"{GEMMA_SYSTEM_INSTRUCTION}\n\n"
                "Examine this handwritten kirana ledger sheet and extract all transaction rows into a JSON array:"
            )

            response = client.models.generate_content(
                model=model_name,
                contents=[image_part, prompt],
            )

            raw_text = response.text or ""
            cleaned = clean_json_response(raw_text)
            parsed_data, parse_err = repair_and_parse_json(cleaned)

            if parse_err or parsed_data is None:
                return ExtractionResult(
                    success=False,
                    error_message=f"Model output parsing error: {parse_err}",
                    model_used=model_name,
                    raw_response_text=raw_text,
                    is_demo=False,
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
                return ExtractionResult(
                    success=False,
                    error_message="Model returned invalid JSON structure (expected an array).",
                    model_used=model_name,
                    raw_response_text=raw_text,
                    is_demo=False,
                )

            rows = raw_dicts_to_extracted_rows(parsed_data)
            needs_review_count = sum(1 for r in rows if r.needs_review)

            return ExtractionResult(
                success=True,
                rows=rows,
                model_used=model_name,
                raw_response_text=raw_text,
                is_demo=False,
                total_rows=len(rows),
                rows_needing_review=needs_review_count,
            )

        except Exception as e:
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
            else:
                guidance = f"Gemma extraction request failed ({err_type}): {err_msg}"

            return ExtractionResult(
                success=False,
                error_message=guidance,
                model_used=model_name,
                is_demo=False,
            )


_GLOBAL_EXTRACTOR: Optional[GemmaLedgerExtractor] = None


def get_extractor() -> GemmaLedgerExtractor:
    """Returns singleton GemmaLedgerExtractor instance."""
    global _GLOBAL_EXTRACTOR
    if _GLOBAL_EXTRACTOR is None:
        _GLOBAL_EXTRACTOR = GemmaLedgerExtractor()
    return _GLOBAL_EXTRACTOR
