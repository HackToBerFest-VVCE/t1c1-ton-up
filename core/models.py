"""Domain data models for Kirana Ledger Digitizer.

Implements typed schemas conforming to Pydantic v2 conventions
matching the Supabase PostgreSQL database tables.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID

# Transaction Types in Kirana shops
class TransactionType(str, Enum):
    CREDIT = "credit"  # Udhaar / give credit to customer
    DEBIT = "debit"    # Jama / payment received from customer
    CASH = "cash"      # Spot cash transaction


# Verification workflow status
class EntryStatus(str, Enum):
    PENDING = "pending"      # Extracted by AI, awaiting human verification
    CONFIRMED = "confirmed"  # Verified and accepted by shopkeeper
    REJECTED = "rejected"    # Marked invalid or duplicate


# Periodic report types
class ReportType(str, Enum):
    DAILY_SUMMARY = "daily_summary"
    OUTSTANDING_DUES = "outstanding_dues"
    PRODUCT_TRENDS = "product_trends"
    CUSTOM = "custom"


try:
    from pydantic import BaseModel, ConfigDict, Field

    class Shop(BaseModel):
        """Kirana store profile."""
        model_config = ConfigDict(from_attributes=True)

        id: Optional[str] = None
        name: str = Field(..., min_length=1, description="Shop or business name")
        owner_name: Optional[str] = None
        phone: Optional[str] = None
        address: Optional[str] = None
        default_language: str = Field(default="kn", max_length=10)

    class LedgerEntry(BaseModel):
        """Single line item transaction from a ledger sheet."""
        model_config = ConfigDict(from_attributes=True)

        id: Optional[str] = None
        shop_id: Optional[str] = None
        customer_name: Optional[str] = None
        customer_phone: Optional[str] = None
        item_name: str = Field(..., min_length=1, description="Raw or localized product name")
        standardized_item_name: Optional[str] = None
        quantity: float = Field(default=1.0, ge=0.0)
        unit: str = Field(default="unit", max_length=30)
        unit_price: float = Field(default=0.0, ge=0.0)
        total_amount: float = Field(default=0.0, ge=0.0)
        transaction_type: TransactionType = TransactionType.CREDIT
        status: EntryStatus = EntryStatus.PENDING
        confidence_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
        raw_ocr_text: Optional[str] = None
        image_url: Optional[str] = None
        entry_date: Optional[str] = None

    class ItemAlias(BaseModel):
        """Mapping between vernacular / handwritten variant to standard catalog item."""
        model_config = ConfigDict(from_attributes=True)

        id: Optional[str] = None
        shop_id: Optional[str] = None
        alias_name: str = Field(..., min_length=1)
        standard_name: str = Field(..., min_length=1)
        language: str = Field(default="en", max_length=10)
        confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    class Correction(BaseModel):
        """Audit record of human corrections applied to an AI extraction."""
        model_config = ConfigDict(from_attributes=True)

        id: Optional[str] = None
        ledger_entry_id: Optional[str] = None
        shop_id: Optional[str] = None
        field_name: str
        original_value: Optional[str] = None
        corrected_value: Optional[str] = None
        corrected_by: str = "owner"

    class Report(BaseModel):
        """Calculated periodic analytics or generated insights summary."""
        model_config = ConfigDict(from_attributes=True)

        id: Optional[str] = None
        shop_id: Optional[str] = None
        report_type: ReportType = ReportType.DAILY_SUMMARY
        period_start: str
        period_end: str
        metrics: Dict[str, Any] = Field(default_factory=dict)
        insights_multilingual: Dict[str, Any] = Field(default_factory=dict)

    class CatalogProduct(BaseModel):
        """Canonical Kirana grocery item from data/items.csv."""
        model_config = ConfigDict(from_attributes=True)

        item_id: int
        name_en: str
        name_kn: str
        name_hi: str
        category: str
        default_unit: str
        standard_price_inr: float

    class ExtractedLedgerRow(BaseModel):
        """Structured single line extracted from a handwritten ledger sheet."""
        model_config = ConfigDict(from_attributes=True)

        row_id: int = 1
        date: Optional[str] = None
        customer: Optional[str] = None
        customer_phone: Optional[str] = None
        item: str = Field(default="", description="Original handwritten item name")
        quantity: Optional[float] = None
        unit: Optional[str] = None
        unit_price: Optional[float] = None
        amount: Optional[float] = None
        paid: Optional[float] = None
        due: Optional[float] = None
        raw_text: str = Field(default="", description="Verbatim handwritten line")
        field_confidence: Dict[str, float] = Field(default_factory=dict)
        candidate_readings: Dict[str, Any] = Field(default_factory=dict)
        validation_flags: list[str] = Field(default_factory=list)
        needs_review: bool = False
        review_reasons: list[str] = Field(default_factory=list)
        standardized_item: Optional[str] = None

    class ExtractionResult(BaseModel):
        """Batch extraction output from Gemma 4 or sample demo fixture."""
        model_config = ConfigDict(from_attributes=True)

        success: bool = True
        rows: list[ExtractedLedgerRow] = Field(default_factory=list)
        error_message: Optional[str] = None
        model_used: str = ""
        is_demo: bool = False
        raw_response_text: Optional[str] = None
        total_rows: int = 0
        rows_needing_review: int = 0

    HAS_PYDANTIC = True

except ImportError:
    from dataclasses import dataclass, field

    @dataclass
    class Shop:  # type: ignore[no-redef]
        name: str
        id: Optional[str] = None
        owner_name: Optional[str] = None
        phone: Optional[str] = None
        address: Optional[str] = None
        default_language: str = "kn"

    @dataclass
    class LedgerEntry:  # type: ignore[no-redef]
        item_name: str
        id: Optional[str] = None
        shop_id: Optional[str] = None
        customer_name: Optional[str] = None
        customer_phone: Optional[str] = None
        standardized_item_name: Optional[str] = None
        quantity: float = 1.0
        unit: str = "unit"
        unit_price: float = 0.0
        total_amount: float = 0.0
        transaction_type: TransactionType = TransactionType.CREDIT
        status: EntryStatus = EntryStatus.PENDING
        confidence_score: Optional[float] = None
        raw_ocr_text: Optional[str] = None
        image_url: Optional[str] = None
        entry_date: Optional[str] = None

    @dataclass
    class ItemAlias:  # type: ignore[no-redef]
        alias_name: str
        standard_name: str
        id: Optional[str] = None
        shop_id: Optional[str] = None
        language: str = "en"
        confidence: float = 1.0

    @dataclass
    class Correction:  # type: ignore[no-redef]
        field_name: str
        id: Optional[str] = None
        ledger_entry_id: Optional[str] = None
        shop_id: Optional[str] = None
        original_value: Optional[str] = None
        corrected_value: Optional[str] = None
        corrected_by: str = "owner"

    @dataclass
    class Report:  # type: ignore[no-redef]
        period_start: str
        period_end: str
        id: Optional[str] = None
        shop_id: Optional[str] = None
        report_type: ReportType = ReportType.DAILY_SUMMARY
        metrics: Dict[str, Any] = field(default_factory=dict)
        insights_multilingual: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class CatalogProduct:  # type: ignore[no-redef]
        item_id: int
        name_en: str
        name_kn: str
        name_hi: str
        category: str
        default_unit: str
        standard_price_inr: float

    @dataclass
    class ExtractedLedgerRow:  # type: ignore[no-redef]
        row_id: int = 1
        date: Optional[str] = None
        customer: Optional[str] = None
        customer_phone: Optional[str] = None
        item: str = ""
        quantity: Optional[float] = None
        unit: Optional[str] = None
        unit_price: Optional[float] = None
        amount: Optional[float] = None
        paid: Optional[float] = None
        due: Optional[float] = None
        raw_text: str = ""
        field_confidence: Dict[str, float] = field(default_factory=dict)
        candidate_readings: Dict[str, Any] = field(default_factory=dict)
        validation_flags: list[str] = field(default_factory=list)
        needs_review: bool = False
        review_reasons: list[str] = field(default_factory=list)
        standardized_item: Optional[str] = None

    @dataclass
    class ExtractionResult:  # type: ignore[no-redef]
        success: bool = True
        rows: list[ExtractedLedgerRow] = field(default_factory=list)
        error_message: Optional[str] = None
        model_used: str = ""
        is_demo: bool = False
        raw_response_text: Optional[str] = None
        total_rows: int = 0
        rows_needing_review: int = 0

    HAS_PYDANTIC = False
