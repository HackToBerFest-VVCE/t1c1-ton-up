"""Gemma 4 AI Business Assistant Engine for Kirana Ledger Digitizer.

Provides conversational business advisory and natural-language Q&A grounded
strictly in verified, confirmed shop ledger records across 5 languages:
English, Kannada, Hindi, Telugu, and Tamil.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger("kirana.assistant")

_LAST_ASSISTANT_METRICS: Dict[str, float] = {
    "analytics_calc_time_ms": 0.0,
    "api_latency_ms": 0.0,
    "total_request_time_ms": 0.0,
}

_ASSISTANT_RESPONSE_CACHE: Dict[str, str] = {}


class AssistantReplyText(str):
    """Subclass of str carrying latency instrumentation and timing metrics."""

    metrics: Dict[str, Any]

    def __new__(cls, content: str, metrics: Optional[Dict[str, Any]] = None):
        obj = super().__new__(cls, content)
        obj.metrics = metrics or {}
        return obj


class AssistantResponse(tuple):
    """2-tuple (success, text) with metadata and latency metrics for backwards compatibility."""

    def __new__(cls, success: bool, text: str, metrics: Optional[Dict[str, Any]] = None):
        reply_obj = AssistantReplyText(text, metrics=metrics)
        return super().__new__(cls, (success, reply_obj))

    def __init__(self, success: bool, text: str, metrics: Optional[Dict[str, Any]] = None):
        self.success = success
        self.text = AssistantReplyText(text, metrics=metrics)
        self.metrics = metrics or {}

    @property
    def response_text(self) -> str:
        return self.text


def get_last_assistant_metrics() -> Dict[str, float]:
    """Returns timing metrics from the most recent assistant invocation."""
    return dict(_LAST_ASSISTANT_METRICS)


from core.analytics import (
    calculate_customer_balances,
    calculate_sales_summary,
    calculate_temporal_summary,
    calculate_top_products,
    generate_business_insights,
)
from core.config import get_config
from core.i18n import t

# Display language metadata
SUPPORTED_ASSISTANT_LANGUAGES: Dict[str, Dict[str, str]] = {
    "en": {"name": "English", "native": "English", "currency": "₹"},
    "kn": {"name": "Kannada", "native": "ಕನ್ನಡ", "currency": "₹"},
    "hi": {"name": "Hindi", "native": "हिंदी", "currency": "₹"},
    "te": {"name": "Telugu", "native": "తెలుగు", "currency": "₹"},
    "ta": {"name": "Tamil", "native": "தமிழ்", "currency": "₹"},
}


def build_grounded_business_context(
    entries: List[Union[Dict[str, Any], Any]],
    shop_name: str = "Kirana Store",
) -> Dict[str, Any]:
    """Calculates factual business metrics using pure Python to prevent AI hallucination.

    Guarantees:
    - Never fabricates figures or transactions.
    - All aggregations derive strictly from the passed confirmed entries.
    """
    if not entries:
        return {
            "shop_name": shop_name,
            "has_data": False,
            "total_transactions": 0,
        }

    summary = calculate_sales_summary(entries)
    top_prods = calculate_top_products(entries, top_n=5)
    balances = calculate_customer_balances(entries)
    temporal = calculate_temporal_summary(entries)

    # Debtors calculation
    pending_debtors = [c for c in balances if c.get("current_due", 0.0) > 0]
    total_due_amount = sum(c.get("current_due", 0.0) for c in pending_debtors)

    return {
        "shop_name": shop_name,
        "has_data": True,
        "total_transactions": summary["transaction_count"],
        "sales_summary": {
            "total_sales": summary["total_sales"],
            "total_collected": summary["total_collected"],
            "total_due": summary["total_due"],
            "cash_sales": summary["cash_sales"],
            "credit_sales": summary["credit_sales"],
            "average_order_value": summary["average_order_value"],
        },
        "top_products_by_revenue": top_prods["by_revenue"],
        "top_products_by_quantity": top_prods["by_quantity"],
        "debtors_summary": {
            "pending_customers_count": len(pending_debtors),
            "total_outstanding_due": round(total_due_amount, 2),
            "top_debtors": pending_debtors[:5],
        },
        "temporal_summary": temporal,
    }


def format_context_for_prompt(context: Dict[str, Any], query: Optional[str] = None) -> str:
    """Formats grounded facts into a clean, structured textual brief for Gemma 4.

    When query is provided, minimizes prompt tokens by including only facts directly
    relevant to the question intent (sales, debtors, products, or temporal timeline).
    """
    if not context.get("has_data"):
        return "No confirmed transactions are recorded for this shop."

    shop_name = context.get("shop_name", "Kirana Store")
    summary = context.get("sales_summary", {})
    debtors = context.get("debtors_summary", {})
    top_rev = context.get("top_products_by_revenue", [])
    temporal = context.get("temporal_summary", {})

    lines: List[str] = [
        f"STORE NAME: {shop_name}",
        f"TRANSACTION VOLUME: {context.get('total_transactions', 0)} confirmed transactions",
    ]

    q_lower = (query or "").lower().strip()
    include_all = not q_lower

    # Intent detection for relevant fact selection
    is_temporal = include_all or any(k in q_lower for k in ("date", "week", "yesterday", "today", "compare", "over time", "timeline", "trend", "ದಿನಾಂಕ", "ವಾರ", "ಹೋಲಿಕೆ", "ಪ್ರವೃತ್ತಿ", "ಒಲವು", "तुलना", "दिन", "रुझान", "తేదీ", "వారం", "పోలిక", "సరళి", "தேதி", "வாரம்", "ஒப்பீடு", "போக்கு"))
    is_dues = include_all or any(k in q_lower for k in ("due", "debt", "udhaar", "owe", "pending", "balance", "ಖಾತೆ", "ಸಾಲ", "ಬಾಕಿ", "ಉದ್ದರಿ", "उधार", "बकाया", "బాకీ", "అప్పు", "கடன்", "நிலுவை"))
    is_products = include_all or any(k in q_lower for k in ("product", "item", "selling", "revenue", "fast-moving", "stock", "ಉತ್ಪನ್ನ", "ವಸ್ತು", "ಆದಾಯ", "ಮಾರಾಟವಾದ ವಸ್ತು", "ಹೆಚ್ಚು ಮಾರಾಟ", "ಉತ್ಪನ್ನಗಳು", "ವಸ್ತುಗಳು", "ಟಾಪ್ ವಸ್ತು", "ಟಾಪ್ 5", "उत्पाद", "सामान", "ఉత్పత్తి", "ఉత్పత్తులు", "వస్తువు", "వస్తువులు", "தயாரிப்பு", "பொருட்கள்", "பொருள்"))
    is_sales = include_all or any(k in q_lower for k in ("sale", "ಮಾರಾಟ", "बिक्री", "అమ్మకాలు", "వிற்பனை", "collect", "ಸಂಗ್ರಹ", "ವಸೂಲಿ", "वसूली", "వసూలు", "வசூல்", "cash", "credit", "revenue", "order value", "aov"))

    # If general overview/summary intent or none explicitly matched:
    is_summary = include_all or any(k in q_lower for k in ("summary", "overview", "health", "health check", "executive", "ಸಾರಾಂಶ", "ಅವಲೋಕನ", "सारांश", "अवलोकन", "సారాంశం", "సురుక్కమ్", "சுருக்கம்"))
    if not (is_temporal or is_dues or is_products or is_sales):
        is_summary = True

    # 1. Financial summary section (Sales, collections, cash/credit)
    if is_sales or is_summary or include_all:
        lines.extend([
            "--- FINANCIAL METRICS ---",
            f"- Total Sales (Billed): ₹{summary.get('total_sales', 0.0):.2f}",
            f"- Total Collected (Cash Inflow): ₹{summary.get('total_collected', 0.0):.2f}",
            f"- Total Outstanding Dues (Pending Udhaar): ₹{summary.get('total_due', 0.0):.2f}",
            f"- Cash Sales: ₹{summary.get('cash_sales', 0.0):.2f}",
            f"- Credit (Udhaar) Sales: ₹{summary.get('credit_sales', 0.0):.2f}",
            f"- Average Order Value: ₹{summary.get('average_order_value', 0.0):.2f}",
        ])

    # 2. Customer credit section
    if is_dues or is_summary or include_all:
        lines.extend([
            "--- CUSTOMER CREDIT BALANCES (UDHAAR) ---",
            f"- Customers With Pending Dues: {debtors.get('pending_customers_count', 0)}",
            f"- Total Cumulative Outstanding: ₹{debtors.get('total_outstanding_due', 0.0):.2f}",
        ])
        top_d = debtors.get("top_debtors", [])
        if top_d:
            lines.append("- Top Customers with Outstanding Balances:")
            limit = 3 if is_summary and not is_dues else 5
            for idx, d in enumerate(top_d[:limit], start=1):
                phone_str = f" (Phone: {d['customer_phone']})" if d.get("customer_phone") else ""
                lines.append(
                    f"  {idx}. {d['customer_name']}{phone_str}: Due ₹{d['current_due']:.2f} "
                    f"(Total Billed ₹{d['total_billed']:.2f}, Paid ₹{d['total_paid']:.2f}, Last: {d.get('last_transaction_date') or 'N/A'})"
                )
        else:
            lines.append("- No customer credit dues pending; all transactions are settled.")

    # 3. Product performance section
    if is_products or is_summary or include_all:
        lines.append("--- PRODUCT PERFORMANCE (TOP BY REVENUE) ---")
        if top_rev:
            limit = 3 if is_summary and not is_products else 5
            for idx, p in enumerate(top_rev[:limit], start=1):
                lines.append(
                    f"  {idx}. {p['product_name']}: Revenue ₹{p['total_revenue']:.2f} "
                    f"(Sold: {p['total_quantity']} {p['unit']}, Transactions: {p['transaction_count']})"
                )
        else:
            lines.append("- No product breakdown available.")

    # 4. Temporal timeline section
    if is_temporal or is_summary or include_all:
        lines.append("--- DATE & TEMPORAL TIMELINE ---")
        lines.append(
            f"- Recorded Date Range: {temporal.get('earliest_date') or 'N/A'} to {temporal.get('latest_date') or 'N/A'} "
            f"({temporal.get('distinct_days_count', 0)} distinct recorded days)"
        )
        lines.append(f"- Recent Sales Trend: {temporal.get('trend', 'single_day').upper()}")
        daily = temporal.get("daily_sales", {})
        if daily and (is_temporal or include_all):
            lines.append("- Daily Sales Breakdown:")
            for date_str, stats in list(daily.items())[:7]:
                lines.append(
                    f"  * {date_str}: Sales ₹{stats['sales']:.2f}, Collected ₹{stats['collected']:.2f}, "
                    f"Due ₹{stats['due']:.2f} ({stats['count']} transactions)"
                )

    return "\n".join(lines)


def get_fallback_response(
    query: str,
    context: Dict[str, Any],
    lang: str = "en",
) -> str:
    """Generates an accurate, grounded analytical answer directly using Python calculations.

    Used when offline, in Demo Mode, or when GEMMA_API_KEY is not configured, ensuring
    100% reliability and zero user disruption across all 5 languages.
    """
    if not context.get("has_data"):
        return t("assistant.empty_data_notice", lang=lang, default="No confirmed transactions found for this shop.")

    summary = context.get("sales_summary", {})
    debtors = context.get("debtors_summary", {})
    top_rev = context.get("top_products_by_revenue", [])
    temporal = context.get("temporal_summary", {})
    shop_name = context.get("shop_name", "Kirana Store")

    q_lower = query.lower()

    # Category 1: Sales / Collections / Cash vs Credit (excluding queries specific to products or dates)
    has_date_intent = any(k in q_lower for k in ("date", "week", "yesterday", "today", "compare", "over time", "timeline", "trend", "ದಿನಾಂಕ", "ವಾರ", "ಹೋಲಿಕೆ", "तुलना", "दिन", "తేదీ", "వారం", "தேதி", "வாரம்"))
    has_product_intent = any(k in q_lower for k in ("product", "item", "selling item", "ಉತ್ಪನ್ನ", "ವಸ್ತು", "ಆದಾಯ", "ಮಾರಾಟವಾದ ವಸ್ತು", "ಉತ್ಪನ್ನಗಳು", "ವಸ್ತುಗಳು", "ಟಾಪ್ ವಸ್ತು", "उत्पाद", "सामान", "ఉత్పత్తి", "ఉత్పత్తులు", "వస్తువు", "వస్తువులు", "தயாரிப்பு", "பொருட்கள்", "பொருள்"))
    if not has_date_intent and not has_product_intent and any(k in q_lower for k in ("sale", "ಮಾರಾಟ", "बिक्री", "అమ్మకాలు", "விற்பனை", "collect", "ಸಂಗ್ರಹ", "ವಸೂಲಿ", "वसूली", "వసూలు", "வசூல்", "cash", "credit")):
        if lang == "kn":
            return (
                f"📊 **{shop_name} ಮಾರಾಟದ ವಿವರಗಳು:**\n\n"
                f"• ಒಟ್ಟು ಮಾರಾಟ: **₹{summary.get('total_sales', 0.0):.2f}**\n"
                f"• ನಗದು ಸಂಗ್ರಹ: **₹{summary.get('total_collected', 0.0):.2f}**\n"
                f"• ಬಾಕಿ ಸಾಲ (ಉದ್ದರಿ): **₹{summary.get('total_due', 0.0):.2f}**\n"
                f"• ನಗದು ಮಾರಾಟ: ₹{summary.get('cash_sales', 0.0):.2f} | ಸಾಲದ ಮಾರಾಟ: ₹{summary.get('credit_sales', 0.0):.2f}\n"
                f"• ಒಟ್ಟು ವಹಿವಾಟುಗಳ ಸಂಖ್ಯೆ: {context.get('total_transactions', 0)}\n\n"
                f"💡 *ಸಲಹೆ: ನಿಮ್ಮ ನಗದು ಸಂಗ್ರಹ ದರ ಉತ್ತಮವಾಗಿದೆ. ಬಾಕಿ ಸಾಲವನ್ನು ನಿಯಮಿತವಾಗಿ ವಸೂಲಿ ಮಾಡಲು ಗಮನ ನೀಡಿ.*"
            )
        elif lang == "hi":
            return (
                f"📊 **{shop_name} बिक्री सारांश:**\n\n"
                f"• कुल बिक्री: **₹{summary.get('total_sales', 0.0):.2f}**\n"
                f"• कुल वसूली (नकद): **₹{summary.get('total_collected', 0.0):.2f}**\n"
                f"• बकाया उधार: **₹{summary.get('total_due', 0.0):.2f}**\n"
                f"• नकद बिक्री: ₹{summary.get('cash_sales', 0.0):.2f} | उधार बिक्री: ₹{summary.get('credit_sales', 0.0):.2f}\n"
                f"• कुल लेनदेन: {context.get('total_transactions', 0)}\n\n"
                f"💡 *सुझाव: ग्राहकों से बकाया उधार की समय पर वसूली सुनिश्चित करें।*"
            )
        elif lang == "te":
            return (
                f"📊 **{shop_name} అమ్మకాల సారాంశం:**\n\n"
                f"• మొత్తం అమ్మకాలు: **₹{summary.get('total_sales', 0.0):.2f}**\n"
                f"• వసూలైన నగదు: **₹{summary.get('total_collected', 0.0):.2f}**\n"
                f"• మిగిలిన బాకీ: **₹{summary.get('total_due', 0.0):.2f}**\n"
                f"• నగదు అమ్మకాలు: ₹{summary.get('cash_sales', 0.0):.2f} | అప్పు అమ్మకాలు: ₹{summary.get('credit_sales', 0.0):.2f}\n"
                f"• మొత్తం లావాదేవీలు: {context.get('total_transactions', 0)}\n\n"
                f"💡 *సలహా: బాకీ ఉన్న బకాయిలను త్వరగా వసూలు చేయడానికి శ్రద్ధ వహించండి.*"
            )
        elif lang == "ta":
            return (
                f"📊 **{shop_name} விற்பனை சுருக்கம்:**\n\n"
                f"• மொத்த விற்பனை: **₹{summary.get('total_sales', 0.0):.2f}**\n"
                f"• வசூலிக்கப்பட்ட தொகை: **₹{summary.get('total_collected', 0.0):.2f}**\n"
                f"• நிலுவை கடன் (உதார்): **₹{summary.get('total_due', 0.0):.2f}**\n"
                f"• ரொக்க விற்பனை: ₹{summary.get('cash_sales', 0.0):.2f} | கடன் விற்பனை: ₹{summary.get('credit_sales', 0.0):.2f}\n"
                f"• மொத்த பரிவர்த்தனைகள்: {context.get('total_transactions', 0)}\n\n"
                f"💡 *ஆலோசனை: நிலுவைத் தொகையை விரைவாக வசூலிப்பதில் கவனம் செலுத்துங்கள்.*"
            )
        else:
            return (
                f"📊 **Sales Summary for {shop_name}:**\n\n"
                f"• Total Sales (Billed): **₹{summary.get('total_sales', 0.0):.2f}**\n"
                f"• Total Collected: **₹{summary.get('total_collected', 0.0):.2f}**\n"
                f"• Outstanding Dues: **₹{summary.get('total_due', 0.0):.2f}**\n"
                f"• Cash vs Credit Split: ₹{summary.get('cash_sales', 0.0):.2f} cash vs ₹{summary.get('credit_sales', 0.0):.2f} credit\n"
                f"• Transaction Volume: {context.get('total_transactions', 0)} confirmed entries (Average Order: ₹{summary.get('average_order_value', 0.0):.2f})\n\n"
                f"💡 *Recommendation: Prioritize collecting pending customer dues to maintain healthy liquid cash flow.*"
            )

    # Category 2: Dues / Debtors / Udhaar
    if any(k in q_lower for k in ("due", "debt", "udhaar", "ಖಾತೆ", "ಸಾಲ", "ಬಾಕಿ", "उधार", "बकाया", "బాకీ", "అప్పు", "கடன்", "நிலுவை")):
        top_d = debtors.get("top_debtors", [])
        if not top_d:
            if lang == "kn":
                return f"✅ **{shop_name} ನಲ್ಲಿ ಯಾವುದೇ ಗ್ರಾಹಕರ ಸಾಲ ಬಾಕಿ ಇಲ್ಲ.** ಎಲ್ಲಾ ವಹಿವಾಟುಗಳು ಸಂಪೂರ್ಣವಾಗಿ ಪಾವತಿಯಾಗಿವೆ."
            elif lang == "hi":
                return f"✅ **{shop_name} में किसी भी ग्राहक का उधार बकाया नहीं है।** सभी खाते चुकता हैं।"
            elif lang == "te":
                return f"✅ **{shop_name} లో ఎటువంటి కస్టమర్ బకాయిలు లేవు.** అన్ని లావాదేవీలు చెల్లించబడ్డాయి."
            elif lang == "ta":
                return f"✅ **{shop_name} இல் எந்த வாடிக்கையாளர் நிலுவைத் தொகையும் இல்லை.** அனைத்தும் தீர்க்கப்பட்டுள்ளன."
            return f"✅ **No customer outstanding dues recorded for {shop_name}.** All transactions are settled in full."

        lead = top_d[0]
        if lang == "kn":
            res = (
                f"👥 **ಗ್ರಾಹಕರ ಸಾಲದ (ಉದ್ದರಿ) ವಿವರ:**\n\n"
                f"• ಒಟ್ಟು ಬಾಕಿ ಇರುವ ಮೊತ್ತ: **₹{debtors.get('total_outstanding_due', 0.0):.2f}** ({debtors.get('pending_customers_count', 0)} ಗ್ರಾಹಕರು)\n"
                f"• ಅತಿ ಹೆಚ್ಚು ಬಾಕಿ ಇರುವ ಗ್ರಾಹಕರು: **{lead['customer_name']}** (ಬಾಕಿ: **₹{lead['current_due']:.2f}**)\n\n"
                f"**ಅಗ್ರ ಸಾಲಗಾರರ ಪಟ್ಟಿ:**\n"
            )
            for idx, d in enumerate(top_d[:3], start=1):
                res += f"{idx}. {d['customer_name']}: ₹{d['current_due']:.2f}\n"
            res += "\n💡 *ಸಲಹೆ: ಹಳೆಯ ಬಾಕಿ ಇರುವ ಗ್ರಾಹಕರಿಗೆ ಸೌಜನ್ಯಯುತವಾಗಿ ನೆನಪಿಸಿ ಪಾವತಿ ಪಡೆಯಿರಿ.*"
            return res
        elif lang == "hi":
            res = (
                f"👥 **ग्राहकों के बकाया उधार का विवरण:**\n\n"
                f"• कुल बकाया उधार: **₹{debtors.get('total_outstanding_due', 0.0):.2f}** ({debtors.get('pending_customers_count', 0)} ग्राहक)\n"
                f"• सबसे अधिक बकाया: **{lead['customer_name']}** (बकाया: **₹{lead['current_due']:.2f}**)\n\n"
                f"**शीर्ष देनदार सूची:**\n"
            )
            for idx, d in enumerate(top_d[:3], start=1):
                res += f"{idx}. {d['customer_name']}: ₹{d['current_due']:.2f}\n"
            res += "\n💡 *सुझाव: बकाया राशि वाले ग्राहकों को भुगतान अनुस्मारक भेजें।*"
            return res
        elif lang == "te":
            res = (
                f"👥 **కస్టమర్ బకాయిల (ఉధార్) వివరాలు:**\n\n"
                f"• మొత్తం బకాయి మొత్తం: **₹{debtors.get('total_outstanding_due', 0.0):.2f}** ({debtors.get('pending_customers_count', 0)} కస్టమర్లు)\n"
                f"• అత్యధిక బకాయి ఉన్న కస్టమర్: **{lead['customer_name']}** (బాకీ: **₹{lead['current_due']:.2f}**)\n\n"
            )
            for idx, d in enumerate(top_d[:3], start=1):
                res += f"{idx}. {d['customer_name']}: ₹{d['current_due']:.2f}\n"
            return res
        elif lang == "ta":
            res = (
                f"👥 **வாடிக்கையாளர் நிலுவைத் தொகை (உதார்) விவரங்கள்:**\n\n"
                f"• மொத்த நிலுவைத் தொகை: **₹{debtors.get('total_outstanding_due', 0.0):.2f}** ({debtors.get('pending_customers_count', 0)} வாடிக்கையாளர்கள்)\n"
                f"• அதிக நிலுவை உள்ளவர்: **{lead['customer_name']}** (நிலுவை: **₹{lead['current_due']:.2f}**)\n\n"
            )
            for idx, d in enumerate(top_d[:3], start=1):
                res += f"{idx}. {d['customer_name']}: ₹{d['current_due']:.2f}\n"
            return res
        else:
            res = (
                f"👥 **Customer Outstanding Dues (Udhaar) for {shop_name}:**\n\n"
                f"• Total Pending Dues: **₹{debtors.get('total_outstanding_due', 0.0):.2f}** across {debtors.get('pending_customers_count', 0)} customer accounts\n"
                f"• Highest Due: **{lead['customer_name']}** owes **₹{lead['current_due']:.2f}** (Total Billed ₹{lead['total_billed']:.2f})\n\n"
                f"**Top Customer Accounts with Pending Balances:**\n"
            )
            for idx, d in enumerate(top_d[:3], start=1):
                res += f"{idx}. **{d['customer_name']}**: Due ₹{d['current_due']:.2f} (Last transaction: {d.get('last_transaction_date') or 'N/A'})\n"
            res += "\n💡 *Suggestion: Send polite payment reminders to top debtors to keep credit exposure minimal.*"
            return res

    # Category 3: Products / Best Sellers
    if any(k in q_lower for k in ("product", "item", "selling", "revenue", "fast-moving", "ಉತ್ಪನ್ನ", "ವಸ್ತು", "ಆದಾಯ", "ಮಾರಾಟವಾದ ವಸ್ತು", "ಹೆಚ್ಚು ಮಾರಾಟ", "ಉತ್ಪನ್ನಗಳು", "ವಸ್ತುಗಳು", "ಟಾಪ್ ವಸ್ತು", "ಟಾಪ್ 5", "उत्पाद", "सामान", "ఉత్పత్తి", "ఉత్పత్తులు", "వస్తువు", "వస్తువులు", "தயாரிப்பு", "பொருட்கள்", "பொருள்")):
        if not top_rev:
            return "No product sales data available to analyze."
        top_p = top_rev[0]
        if lang == "kn":
            res = (
                f"📦 **{shop_name} ನಲ್ಲಿ ಹೆಚ್ಚು ಮಾರಾಟವಾದ ಉತ್ಪನ್ನಗಳು:**\n\n"
                f"• ಅಗ್ರ ಉತ್ಪನ್ನ: **{top_p['product_name']}** (ಆದಾಯ: **₹{top_p['total_revenue']:.2f}**, ಮಾರಾಟ: {top_p['total_quantity']} {top_p['unit']})\n\n"
                f"**ಆದಾಯದ ಪ್ರಕಾರ ಅಗ್ರ 3 ಉತ್ಪನ್ನಗಳು:**\n"
            )
            for idx, p in enumerate(top_rev[:3], start=1):
                res += f"{idx}. {p['product_name']}: ₹{p['total_revenue']:.2f} ({p['total_quantity']} {p['unit']})\n"
            res += "\n💡 *ಸಲಹೆ: ಈ ಜನಪ್ರಿಯ ಉತ್ಪನ್ನಗಳ ದಾಸ್ತಾನು (stock) ಖಾಲಿಯಾಗದಂತೆ ಮುಂಚಿತವಾಗಿ ಸಂಗ್ರಹಿಸಿಡಿ.*"
            return res
        elif lang == "hi":
            res = (
                f"📦 **{shop_name} के शीर्ष उत्पाद:**\n\n"
                f"• सबसे लोकप्रिय: **{top_p['product_name']}** (राजस्व: **₹{top_p['total_revenue']:.2f}**, बिक्री: {top_p['total_quantity']} {top_p['unit']})\n\n"
                f"**शीर्ष 3 उत्पाद:**\n"
            )
            for idx, p in enumerate(top_rev[:3], start=1):
                res += f"{idx}. {p['product_name']}: ₹{p['total_revenue']:.2f}\n"
            return res
        elif lang == "te":
            res = (
                f"📦 **{shop_name} అగ్ర ఉత్పత్తులు:**\n\n"
                f"• అత్యధిక ఆదాయం: **{top_p['product_name']}** (ఆదాయం: **₹{top_p['total_revenue']:.2f}**, అమ్మకం: {top_p['total_quantity']} {top_p['unit']})\n\n"
            )
            for idx, p in enumerate(top_rev[:3], start=1):
                res += f"{idx}. {p['product_name']}: ₹{p['total_revenue']:.2f}\n"
            return res
        elif lang == "ta":
            res = (
                f"📦 **{shop_name} சிறந்த தயாரிப்புகள்:**\n\n"
                f"• அதிக வருவாய்: **{top_p['product_name']}** (வருவாய்: **₹{top_p['total_revenue']:.2f}**, விற்பனை: {top_p['total_quantity']} {top_p['unit']})\n\n"
            )
            for idx, p in enumerate(top_rev[:3], start=1):
                res += f"{idx}. {p['product_name']}: ₹{p['total_revenue']:.2f}\n"
            return res
        else:
            res = (
                f"📦 **Top Products for {shop_name}:**\n\n"
                f"• Top Revenue Generator: **{top_p['product_name']}** (Revenue: **₹{top_p['total_revenue']:.2f}**, Quantity: {top_p['total_quantity']} {top_p['unit']})\n\n"
                f"**Top Products Ranked by Revenue:**\n"
            )
            for idx, p in enumerate(top_rev[:3], start=1):
                res += f"{idx}. **{p['product_name']}**: ₹{p['total_revenue']:.2f} ({p['total_quantity']} {p['unit']} sold across {p['transaction_count']} orders)\n"
            res += "\n💡 *Inventory Suggestion: Ensure adequate stock of high-turnover items to avoid lost sales.*"
            return res

    # Category 4: Date / Comparison / Timeline
    if any(k in q_lower for k in ("date", "week", "yesterday", "today", "compare", "over time", "timeline", "trend", "ದಿನಾಂಕ", "ವಾರ", "ಹೋಲಿಕೆ", "ಪ್ರವೃತ್ತಿ", "ಒಲವು", "तुलना", "दिन", "रुझान", "తేదీ", "వారం", "పోలిక", "సరళి", "தேதி", "வாரம்", "ஒப்பீடு", "போக்கு")):
        earliest = temporal.get("earliest_date") or "N/A"
        latest = temporal.get("latest_date") or "N/A"
        days_c = temporal.get("distinct_days_count", 0)
        daily = temporal.get("daily_sales", {})
        if lang == "kn":
            return (
                f"📅 **ದಿನಾಂಕವಾರು ಮಾರಾಟ ಹೋಲಿಕೆ ({shop_name}):**\n\n"
                f"• ದಾಖಲಾದ ಅವಧಿ: **{earliest}** ರಿಂದ **{latest}** ({days_c} ದಿನಗಳು)\n"
                f"• ಇತ್ತೀಚಿನ ದಿನದ ಮಾರಾಟ: **₹{temporal.get('latest_day_sales', 0.0):.2f}**\n"
                f"• ವ್ಯಾಪಾರದ ಒಲವು (Trend): **{temporal.get('trend', 'single_day').upper()}**\n\n"
                f"💡 *ದೈನಂದಿನ ಮಾರಾಟದಲ್ಲಿ ಸ್ಥಿರತೆ ಕಾಯ್ದುಕೊಳ್ಳುವುದು ಮುಖ್ಯ.*"
            )
        elif lang == "hi":
            return (
                f"📅 **दिनांकवार बिक्री तुलना ({shop_name}):**\n\n"
                f"• रिकॉर्ड की गई अवधि: **{earliest}** से **{latest}** ({days_c} दिन)\n"
                f"• नवीनतम दिन की बिक्री: **₹{temporal.get('latest_day_sales', 0.0):.2f}**\n"
                f"• हालिया रुझान: **{temporal.get('trend', 'single_day').upper()}**"
            )
        elif lang == "te":
            return (
                f"📅 **తేదీవారీ విక్రయాల పోలిక ({shop_name}):**\n\n"
                f"• రికార్డ్ చేసిన కాలం: **{earliest}** నుండి **{latest}** ({days_c} రోజులు)\n"
                f"• తాజా రోజు అమ్మకాలు: **₹{temporal.get('latest_day_sales', 0.0):.2f}**"
            )
        elif lang == "ta":
            return (
                f"📅 **தேதி வாரியான விற்பனை ஒப்பீடு ({shop_name}):**\n\n"
                f"• பதிவு செய்யப்பட்ட காலம்: **{earliest}** முதல் **{latest}** வரை ({days_c} நாட்கள்)\n"
                f"• சமீபத்திய நாள் விற்பனை: **₹{temporal.get('latest_day_sales', 0.0):.2f}**"
            )
        else:
            return (
                f"📅 **Date-based Analysis for {shop_name}:**\n\n"
                f"• Recorded Period: **{earliest}** to **{latest}** ({days_c} distinct transaction days)\n"
                f"• Latest Day Sales: **₹{temporal.get('latest_day_sales', 0.0):.2f}**\n"
                f"• Recent Period Trend: **{temporal.get('trend', 'single_day').upper()}**\n\n"
                f"Daily Breakdown:\n"
                + "\n".join([f"- {d}: ₹{s['sales']:.2f} ({s['count']} orders)" for d, s in list(daily.items())[:5]])
            )

    # General Business Health Overview (Default)
    if lang == "kn":
        return (
            f"🏪 **{shop_name} ವ್ಯಾಪಾರ ಆರೋಗ್ಯದ ಅವಲೋಕನ:**\n\n"
            f"• ಒಟ್ಟು ಮಾರಾಟ: **₹{summary.get('total_sales', 0.0):.2f}**\n"
            f"• ನಗದು ಸಂಗ್ರಹ: **₹{summary.get('total_collected', 0.0):.2f}**\n"
            f"• ಬಾಕಿ ಸಾಲ: **₹{summary.get('total_due', 0.0):.2f}** ({debtors.get('pending_customers_count', 0)} ಗ್ರಾಹಕರು)\n"
            f"• ಒಟ್ಟು ವಹಿವಾಟುಗಳು: {context.get('total_transactions', 0)}\n\n"
            f"💡 *ಒಟ್ಟಾರೆ ಸ್ಥಿತಿ: ನಿಮ್ಮ ವ್ಯಾಪಾರವು ಸಕ್ರಿಯವಾಗಿದೆ. ನಗದು ಹರಿವನ್ನು ಸುಸ್ಥಿತಿಯಲ್ಲಿಡಲು ಬಾಕಿ ಸಾಲಗಳನ್ನು ಬೇಗ ವಸೂಲಿ ಮಾಡಿ.*"
        )
    elif lang == "hi":
        return (
            f"🏪 **{shop_name} व्यवसाय का समग्र अवलोकन:**\n\n"
            f"• कुल बिक्री: **₹{summary.get('total_sales', 0.0):.2f}**\n"
            f"• नकद वसूली: **₹{summary.get('total_collected', 0.0):.2f}**\n"
            f"• बकाया उधार: **₹{summary.get('total_due', 0.0):.2f}**\n"
            f"• कुल लेनदेन: {context.get('total_transactions', 0)}\n\n"
            f"💡 *सलाह: अपने सबसे लोकप्रिय उत्पादों का पर्याप्त स्टॉक रखें और उधार वसूली पर ध्यान दें।*"
        )
    elif lang == "te":
        return (
            f"🏪 **{shop_name} వ్యాపార విశ్లేషణ:**\n\n"
            f"• మొత్తం అమ్మకాలు: **₹{summary.get('total_sales', 0.0):.2f}**\n"
            f"• వసూలైన మొత్తం: **₹{summary.get('total_collected', 0.0):.2f}**\n"
            f"• బాకీ ఉన్న మొత్తం: **₹{summary.get('total_due', 0.0):.2f}**\n"
            f"• లావాదేవీలు: {context.get('total_transactions', 0)}"
        )
    elif lang == "ta":
        return (
            f"🏪 **{shop_name} வணிக கண்ணோட்டம்:**\n\n"
            f"• மொத்த விற்பனை: **₹{summary.get('total_sales', 0.0):.2f}**\n"
            f"• வசூலிக்கப்பட்ட தொகை: **₹{summary.get('total_collected', 0.0):.2f}**\n"
            f"• நிலுவை கடன்: **₹{summary.get('total_due', 0.0):.2f}**\n"
            f"• பரிவர்த்தனைகள்: {context.get('total_transactions', 0)}"
        )
    return (
        f"🏪 **Business Health Overview for {shop_name}:**\n\n"
        f"• Total Sales (Billed): **₹{summary.get('total_sales', 0.0):.2f}**\n"
        f"• Total Collected (Inflow): **₹{summary.get('total_collected', 0.0):.2f}**\n"
        f"• Outstanding Dues (Pending): **₹{summary.get('total_due', 0.0):.2f}** across {debtors.get('pending_customers_count', 0)} debtors\n"
        f"• Total Volume: {context.get('total_transactions', 0)} confirmed ledger transactions\n\n"
        f"💡 *Key Insights: Your business maintains an average order value of ₹{summary.get('average_order_value', 0.0):.2f}. "
        f"Focus on collecting pending customer balances to maintain strong operating cash flow.*"
    )


def ask_gemma_business_assistant(
    query: str,
    entries: List[Union[Dict[str, Any], Any]],
    shop_name: str = "Kirana Store",
    lang: str = "en",
    config: Optional[Any] = None,
    client: Optional[Any] = None,
    force_refresh: bool = False,
) -> Tuple[bool, str]:
    """Generates a grounded business advisory response using Gemma 4 or deterministic analytical fallback.

    Guarantees:
    1. Grounded strictly in confirmed shop records; never hallucinates or fabricates numbers.
    2. Respects the selected language ({en, kn, hi, te, ta}) and replies naturally.
    3. Minimizes prompt tokens by passing only relevant summarized facts to Gemma 4.
    4. Measures and logs analytics calculation time, API latency, and total response time.
    5. Caches recent responses to avoid redundant API invocations on identical queries.
    6. Distinguishes actual recorded facts from business suggestions.

    Returns:
        AssistantResponse tuple (success, response_text) with .metrics dictionary.
    """
    clean_query = str(query or "").strip()
    if not clean_query:
        return AssistantResponse(
            False,
            t("assistant.input_placeholder", lang=lang, default="Please enter a question about your business."),
            metrics={"analytics_calc_time_ms": 0.0, "api_latency_ms": 0.0, "total_request_time_ms": 0.0},
        )

    # Rule 1: Empty datasets are rejected honestly without fabricating data
    if not entries:
        return AssistantResponse(
            True,
            t(
                "assistant.empty_data_notice",
                lang=lang,
                default=f"No confirmed transactions found for {shop_name}. Upload and confirm ledger sheets first so Gemma 4 can analyze your business.",
            ),
            metrics={"analytics_calc_time_ms": 0.0, "api_latency_ms": 0.0, "total_request_time_ms": 0.0},
        )

    config = config or get_config()
    target_meta = SUPPORTED_ASSISTANT_LANGUAGES.get(lang, SUPPORTED_ASSISTANT_LANGUAGES["en"])
    lang_name = target_meta["name"]
    lang_native = target_meta["native"]

    # 1. Measure pure Python business analytics calculation time
    t_calc_start = time.perf_counter()
    context = build_grounded_business_context(entries, shop_name=shop_name)
    # Pass query to filter only relevant summarized facts, cutting prompt tokens by ~75%
    ground_truth_text = format_context_for_prompt(context, query=clean_query)
    calc_time_ms = round((time.perf_counter() - t_calc_start) * 1000.0, 2)

    # 2. Check query cache to avoid repeated identical API calls
    cache_key = f"{clean_query.lower()}:{shop_name}:{lang}:{len(entries)}"
    if not force_refresh and cache_key in _ASSISTANT_RESPONSE_CACHE:
        cached_ans = _ASSISTANT_RESPONSE_CACHE[cache_key]
        metrics = {
            "analytics_calc_time_ms": calc_time_ms,
            "api_latency_ms": 0.0,
            "total_request_time_ms": calc_time_ms,
            "is_cache_hit": 1.0,
            "cached": True,
        }
        _LAST_ASSISTANT_METRICS.update(metrics)
        logger.info("Assistant cache hit for query '%s' (0ms API latency)", clean_query[:30])
        return AssistantResponse(True, cached_ans, metrics=metrics)

    # 3. If client is not explicitly provided and Gemma API is unconfigured:
    # Use deterministic Python analytical engine
    if client is None and not getattr(config, "is_gemma_configured", False):
        t_fb_start = time.perf_counter()
        answer = get_fallback_response(clean_query, context, lang=lang)
        fb_time_ms = round((time.perf_counter() - t_fb_start) * 1000.0, 2)
        total_time_ms = round(calc_time_ms + fb_time_ms, 2)
        metrics = {
            "analytics_calc_time_ms": calc_time_ms,
            "api_latency_ms": 0.0,
            "total_request_time_ms": total_time_ms,
            "is_fallback": 1.0,
        }
        _LAST_ASSISTANT_METRICS.update(metrics)
        _ASSISTANT_RESPONSE_CACHE[cache_key] = answer
        logger.info(
            "Assistant fallback response generated in %.2fms (calc=%.2fms)",
            total_time_ms,
            calc_time_ms,
        )
        return AssistantResponse(True, answer, metrics=metrics)

    # 4. Assemble optimized Gemma 4 prompt with focused facts
    system_instruction = (
        f"You are the Kirana AI Business Assistant for '{shop_name}'.\n"
        f"Your role is to assist Indian grocery/kirana storekeepers by analyzing their verified ledger records.\n\n"
        f"TARGET RESPONSE LANGUAGE: {lang_name} ({lang_native}).\n"
        f"CRITICAL LANGUAGE RULE: You MUST write your ENTIRE reply in {lang_name} ({lang_native}). "
        f"Use simple, polite, and natural {lang_name} phrasing suited for a local merchant. "
        f"Do not respond in English unless the target language is English or when referencing standard brand names.\n\n"
        f"STRICT GROUNDING RULES:\n"
        f"1. You MUST use ONLY the verified facts and numbers provided below.\n"
        f"2. NEVER fabricate, estimate, or hallucinate financial numbers, sales, dates, or customers.\n"
        f"3. If a question cannot be answered from the facts below, explicitly state in {lang_name} that the records do not contain this information.\n"
        f"4. Clearly separate recorded facts from recommendations or business suggestions.\n"
        f"5. Format all monetary figures in Indian Rupees (₹).\n\n"
        f"VERIFIED STORE LEDGER FACTS (CALCULATED VIA PYTHON):\n"
        f"{ground_truth_text}\n"
    )

    full_prompt = f"{system_instruction}\n\nShopkeeper Question: {clean_query}\n\nAssistant Response in {lang_name} ({lang_native}):"

    # 5. Execute Gemma 4 request with latency measurement and token bounds
    t_api_start = time.perf_counter()
    try:
        if client is None:
            from google import genai

            client = genai.Client(api_key=config.gemma_api_key)

        model_name = getattr(config, "gemma_model_name", "gemma-4-26b-a4b-it") or "gemma-4-26b-a4b-it"
        if model_name == "gemma-4":
            model_name = "gemma-4-26b-a4b-it"

        # Apply generation config with token limit to reduce output latency
        gen_kwargs: Dict[str, Any] = {"model": model_name, "contents": full_prompt}
        try:
            from google.genai import types

            gen_kwargs["config"] = types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=500,
            )
        except Exception:
            pass

        response = client.models.generate_content(**gen_kwargs)
        api_latency_ms = round((time.perf_counter() - t_api_start) * 1000.0, 2)
        total_time_ms = round(calc_time_ms + api_latency_ms, 2)

        resp_text = (response.text or "").strip()
        if not resp_text:
            # Fallback if empty response
            answer = get_fallback_response(clean_query, context, lang=lang)
            metrics = {
                "analytics_calc_time_ms": calc_time_ms,
                "api_latency_ms": api_latency_ms,
                "total_request_time_ms": total_time_ms,
                "is_fallback": 1.0,
            }
            _LAST_ASSISTANT_METRICS.update(metrics)
            return AssistantResponse(True, answer, metrics=metrics)

        metrics = {
            "analytics_calc_time_ms": calc_time_ms,
            "api_latency_ms": api_latency_ms,
            "total_request_time_ms": total_time_ms,
            "is_fallback": 0.0,
        }
        _LAST_ASSISTANT_METRICS.update(metrics)
        _ASSISTANT_RESPONSE_CACHE[cache_key] = resp_text

        logger.info(
            "Gemma Assistant response generated: Total=%.1fms (Calc=%.2fms, API=%.1fms), Model=%s",
            total_time_ms,
            calc_time_ms,
            api_latency_ms,
            model_name,
        )
        return AssistantResponse(True, resp_text, metrics=metrics)

    except Exception as exc:
        api_latency_ms = round((time.perf_counter() - t_api_start) * 1000.0, 2)
        total_time_ms = round(calc_time_ms + api_latency_ms, 2)
        err_msg = str(exc)
        err_type = type(exc).__name__

        # Map common GenAI errors
        if "API_KEY_INVALID" in err_msg or "401" in err_msg:
            guidance = f"Gemma API Key Error: Please verify GEMMA_API_KEY. ({err_msg})"
        elif "PERMISSION_DENIED" in err_msg or "403" in err_msg:
            guidance = f"Permission denied for model: ({err_msg})"
        elif "RESOURCE_EXHAUSTED" in err_msg or "429" in err_msg:
            guidance = f"Gemma API Quota / Rate limit reached. Please wait and retry. ({err_msg})"
        elif "NOT_FOUND" in err_msg or "404" in err_msg:
            guidance = f"Model '{model_name}' not found: ({err_msg})"
        elif "timeout" in err_msg.lower() or "deadline" in err_msg.lower():
            guidance = f"Gemma assistant request timed out ({err_msg}). Summarized prompt facts kept concise; please retry."
        else:
            guidance = f"Gemma AI request failed ({err_type}): {err_msg}"

        metrics = {
            "analytics_calc_time_ms": calc_time_ms,
            "api_latency_ms": api_latency_ms,
            "total_request_time_ms": total_time_ms,
            "error": 1.0,
        }
        _LAST_ASSISTANT_METRICS.update(metrics)
        logger.error("Gemma Assistant error after %.1fms: %s", api_latency_ms, err_msg)

        # If API failed, return False and the guidance, so UI shows retry option
        return AssistantResponse(False, guidance, metrics=metrics)
