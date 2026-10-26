"""Kirana Ledger Digitizer - Main Streamlit Application.

Team: Ton Up
Hackathon: Best Use of Gemma 4
Milestone: Milestone 1 - Foundation, Configuration, Multilingual Packs & Database Schema
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

try:
    import streamlit as st
except ImportError:
    st = None  # Handled gracefully if run outside streamlit runtime

from core.catalog import get_catalog_manager
from core.config import get_config
from core.db import get_db_manager
from core.i18n import get_language_manager, t


def render_sidebar(lang_mgr: Any, current_lang: str) -> str:
    """Renders the language picker and milestone badge in the Streamlit sidebar."""
    st.sidebar.title("📒 Ton Up")
    st.sidebar.caption("Kirana Ledger Digitizer")
    st.sidebar.markdown("---")

    # Language selection
    available_languages = lang_mgr.get_available_languages()
    lang_options = {}
    default_index = 0

    for idx, meta in enumerate(available_languages):
        code = meta["code"]
        name = meta["name"]
        native = meta["native_name"]
        review_flag = " ⚠️ (Draft/Review)" if meta.get("needs_native_review") else ""
        label = f"{native} ({name}){review_flag}"
        lang_options[code] = label
        if code == current_lang:
            default_index = idx

    codes = list(lang_options.keys())
    selected_code = st.sidebar.selectbox(
        label="🌐 " + t("common.language", lang=current_lang, default="Language"),
        options=codes,
        index=default_index,
        format_func=lambda c: lang_options[c],
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("📌 Navigation (Milestone 1)")
    st.sidebar.info(
        f"**Active Language**: `{selected_code.upper()}`\n\n"
        f"**Status**: Foundation Initialized\n\n"
        f"**Next**: Milestone 2 (OCR & Gemma 4)"
    )

    st.sidebar.markdown("---")
    st.sidebar.caption("Best Use of Gemma 4 Hackathon • 2026")
    return selected_code


def render_app() -> None:
    """Main Streamlit rendering loop."""
    if st is None:
        print("Streamlit is not installed in the current environment.")
        print("Please install requirements using: pip install -r requirements.txt")
        print("Then run: streamlit run app.py")
        sys.exit(1)

    config = get_config()
    lang_mgr = get_language_manager()

    st.set_page_config(
        page_title="Kirana Ledger Digitizer | Ton Up",
        page_icon="📒",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Initialize session state for selected language
    if "selected_language" not in st.session_state:
        st.session_state.selected_language = config.default_language

    selected_lang = render_sidebar(lang_mgr, st.session_state.selected_language)
    st.session_state.selected_language = selected_lang

    # Warning banner if language pack needs native review (e.g. Telugu or Tamil)
    if lang_mgr.needs_native_review(selected_lang):
        review_msg = t("review_banner.notice", lang=selected_lang)
        st.warning(f"⚠️ **Native Review Needed**: {review_msg}")

    # Header section
    app_title = t("app.title", lang=selected_lang)
    app_subtitle = t("app.subtitle", lang=selected_lang)
    st.title(f"📒 {app_title}")
    st.markdown(f"*{app_subtitle}*")

    st.markdown(
        f"`{t('app.milestone_badge', lang=selected_lang)}` &nbsp;|&nbsp; "
        f"**AI Engine**: `{config.gemma_model_name}` &nbsp;|&nbsp; "
        f"**Storage**: Supabase PostgreSQL"
    )

    st.markdown("---")

    # Milestone 1 Dashboard Tabs
    tab_readiness, tab_i18n, tab_catalog, tab_schema = st.tabs([
        "⚙️ " + t("nav.settings", lang=selected_lang, default="System Readiness"),
        "🌐 " + t("common.language", lang=selected_lang, default="Languages"),
        "📦 " + t("nav.catalog", lang=selected_lang, default="Product Catalog"),
        "🗄️ Database Schema",
    ])

    with tab_readiness:
        st.subheader("System Readiness & Configuration Diagnostics")
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric(
                label="Environment Mode",
                value=config.app_env.title(),
                delta="Debug Active" if config.debug else "Production",
            )
        with col2:
            st.metric(
                label="Gemma 4 Configuration",
                value="Ready" if config.is_gemma_configured else "Key Needed",
                delta=config.gemma_model_name,
            )
        with col3:
            db_mgr = get_db_manager()
            db_status = db_mgr.get_status()
            st.metric(
                label="Supabase Database",
                value=db_status["status"].replace("_", " ").title(),
                delta="Credentials Present" if db_status["is_configured"] else "Awaiting Setup",
            )

        st.markdown("#### Safe Configuration Summary (Sanitized)")
        st.json(config.get_safe_summary())

        st.info(
            "🔒 **Security Verification**: API keys and database credentials are strictly "
            "read from environment variables. Secrets are masked and `.env` is ignored by Git."
        )

    with tab_i18n:
        st.subheader("Multilingual Packs & Review Status")
        st.markdown(
            "The Kirana Ledger Digitizer supports 5 languages tailored for South Asian retail. "
            "Telugu and Tamil packs are flagged for native-speaker review."
        )

        lang_rows = []
        for meta in lang_mgr.get_available_languages():
            code = meta["code"]
            lang_rows.append({
                "Code": code,
                "Name": meta["name"],
                "Native Script": meta["native_name"],
                "Review Status": "⚠️ Needs Native Review" if meta.get("needs_native_review") else "✅ Verified",
                "App Title Translation": t("app.title", lang=code),
                "Credit Term": t("common.credit", lang=code),
                "Debit Term": t("common.debit", lang=code),
            })

        st.table(lang_rows)

    with tab_catalog:
        st.subheader(f"📦 {t('nav.catalog', lang=selected_lang)} (50 Common Kirana Products)")
        st.markdown(
            "Pre-seeded canonical items used for fuzzy-matching handwritten abbreviations "
            "against standardized retail goods."
        )

        catalog_mgr = get_catalog_manager()
        items = catalog_mgr.get_all_items()
        catalog_data = [
            {
                "ID": item.item_id,
                "English": item.name_en,
                "ಕನ್ನಡ (Kannada)": item.name_kn,
                "हिन्दी (Hindi)": item.name_hi,
                "Category": item.category,
                "Unit": item.default_unit,
                "Standard Price (₹)": f"₹{item.standard_price_inr:.2f}",
            }
            for item in items
        ]
        st.dataframe(catalog_data, use_container_width=True)

    with tab_schema:
        st.subheader("🗄️ PostgreSQL Supabase Schema (Milestone 1)")
        st.markdown(
            "Initial database tables configured for shops, ledger records, item aliases, "
            "human corrections, and analytics reports."
        )

        schema_file = Path(__file__).resolve().parent / "sql" / "schema.sql"
        if schema_file.exists():
            schema_sql = schema_file.read_text(encoding="utf-8")
            st.code(schema_sql, language="sql")
        else:
            st.error("Schema file sql/schema.sql was not found.")


if __name__ == "__main__":
    if st is not None:
        render_app()
    else:
        print("Streamlit is required to launch the interactive UI.")
        print("Run: pip install -r requirements.txt && streamlit run app.py")
