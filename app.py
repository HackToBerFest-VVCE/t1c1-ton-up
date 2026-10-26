"""Kirana Ledger Digitizer - Main Streamlit Application.

Team: Ton Up
Hackathon: Best Use of Gemma 4
Milestone: Milestone 2 - Ledger Image Upload, OpenCV Preprocessing, Gemma 4 Extraction & Data Validation
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import streamlit as st
except ImportError:
    st = None  # Handled gracefully if run outside streamlit runtime

from core.catalog import get_catalog_manager
from core.config import get_config
from core.db import get_db_manager
from core.extractor import get_extractor
from core.i18n import get_language_manager, t
from core.image_preprocessor import get_preprocessor


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
    st.sidebar.subheader("📌 System Status (Milestone 2)")
    st.sidebar.info(
        f"**Active Language**: `{selected_code.upper()}`\n\n"
        f"**Milestone 2**: Vision Preprocessing & Gemma 4 Extraction\n\n"
        f"**Database**: Staging in Memory (No Supabase writes in M2)"
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
    preprocessor = get_preprocessor()
    extractor = get_extractor()

    st.set_page_config(
        page_title="Kirana Ledger Digitizer | Ton Up",
        page_icon="📒",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Initialize session state for selected language and extraction results
    if "selected_language" not in st.session_state:
        st.session_state.selected_language = config.default_language
    if "extraction_result" not in st.session_state:
        st.session_state.extraction_result = None
    if "preprocessed_result" not in st.session_state:
        st.session_state.preprocessed_result = None

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
        f"`Milestone 2: Vision & Extraction Active` &nbsp;|&nbsp; "
        f"**AI Engine**: `{config.gemma_model_name}` &nbsp;|&nbsp; "
        f"**Preprocess**: OpenCV CLAHE & Deskew"
    )

    st.markdown("---")

    # Tabs: Milestone 2 features + Milestone 1 diagnostic tabs
    tab_upload, tab_catalog, tab_readiness, tab_i18n, tab_schema = st.tabs([
        "📸 " + t("nav.upload", lang=selected_lang, default="Upload & Extract"),
        "📦 " + t("nav.catalog", lang=selected_lang, default="Product Catalog"),
        "⚙️ " + t("nav.settings", lang=selected_lang, default="System Readiness"),
        "🌐 " + t("common.language", lang=selected_lang, default="Languages"),
        "🗄️ Database Schema",
    ])

    with tab_upload:
        st.subheader("📸 " + t("nav.upload", lang=selected_lang, default="Ledger Photograph Upload & AI Extraction"))
        st.markdown(
            "Upload a photograph of a physical handwritten credit/debit notebook (*khata book*). "
            "OpenCV automatically normalizes lighting, enhances contrast, and corrects skew before passing to Gemma 4."
        )

        col_input_mode, col_fixture = st.columns([2, 2])
        with col_input_mode:
            use_demo = st.checkbox(
                "🧪 " + t("upload.demo_mode_toggle", lang=selected_lang, default="Demo Mode (Offline Verified Fixtures)"),
                value=not config.is_gemma_configured,
                help="Test the complete extraction and validation pipeline using pre-validated sample ledger sheets without needing a live API key.",
            )

        with col_fixture:
            available_fixtures = extractor.get_available_fixtures()
            chosen_fixture = st.selectbox(
                label=t("upload.select_demo_fixture", lang=selected_lang, default="Select Demo Fixture"),
                options=available_fixtures,
                index=0 if available_fixtures else None,
                disabled=not use_demo,
            )

        # File uploader
        uploaded_file = st.file_uploader(
            label=t("upload.drag_drop", lang=selected_lang, default="Upload handwritten ledger photograph"),
            type=["jpg", "jpeg", "png", "webp"],
            help="Supported formats: JPG, JPEG, PNG, WEBP. Maximum file size: 15 MB.",
        )

        # Optional sample image loader if user has not uploaded a file
        sample_image_path = Path(__file__).resolve().parent / "data" / "sample_images" / "sample_ledger.png"
        use_sample_img = False
        if uploaded_file is None and sample_image_path.exists():
            if st.button("🖼️ Load Sample Notebook Photograph"):
                use_sample_img = True

        image_bytes: Optional[bytes] = None
        if uploaded_file is not None:
            # File size validation
            size_mb = len(uploaded_file.getvalue()) / (1024 * 1024)
            if size_mb > config.max_upload_size_mb:
                st.error(f"File size ({size_mb:.1f} MB) exceeds maximum allowed limit of {config.max_upload_size_mb} MB.")
            else:
                image_bytes = uploaded_file.getvalue()
        elif use_sample_img and sample_image_path.exists():
            image_bytes = sample_image_path.read_bytes()

        # Step 2: OpenCV Preprocessing and Side-by-Side Preview
        if image_bytes is not None:
            with st.spinner("Applying OpenCV CLAHE contrast enhancement and deskewing..."):
                try:
                    proc_result = preprocessor.preprocess(image_bytes)
                    st.session_state.preprocessed_result = proc_result
                except Exception as prep_err:
                    st.error(f"Preprocessing error: {prep_err}")
                    proc_result = None

            if proc_result is not None:
                st.markdown("### 🔍 Image Preprocessing Comparison")
                col_orig, col_proc = st.columns(2)

                with col_orig:
                    st.caption(f"**Original Image** ({proc_result.original_dimensions[0]}×{proc_result.original_dimensions[1]}px)")
                    st.image(proc_result.to_pil_original(), use_container_width=True)

                with col_proc:
                    deskew_note = f", Deskew: {proc_result.skew_angle:+.1f}°" if proc_result.deskewed else ", Skew: 0°"
                    crop_note = ", Margins Cropped" if proc_result.cropped else ""
                    st.caption(f"**OpenCV Enhanced** (CLAHE Contrast{deskew_note}{crop_note})")
                    st.image(proc_result.to_pil_processed(), use_container_width=True)

                # Extraction trigger button
                st.markdown("---")
                extract_col, info_col = st.columns([2, 3])
                with extract_col:
                    btn_label = "🚀 " + t("upload.extract_btn", lang=selected_lang, default="Extract Transactions with Gemma 4")
                    run_extraction = st.button(btn_label, type="primary")

                with info_col:
                    if use_demo:
                        st.info(f"🧪 **Demo Mode Active**: Extraction will load verified offline fixture `{chosen_fixture}`.")
                    elif config.is_gemma_configured:
                        st.success(f"⚡ **Live Mode**: Targeting `{config.gemma_model_name}` with configured API credentials.")
                    else:
                        st.warning("⚠️ Live Gemma API key is not configured. Enable Demo Mode above or configure GEMMA_API_KEY in .env.")

                if run_extraction:
                    with st.spinner("Extracting handwritten ledger rows with Gemma 4..."):
                        processed_bytes = proc_result.to_processed_bytes()
                        result = extractor.extract_from_image(
                            image_bytes=processed_bytes,
                            use_demo=use_demo,
                            demo_fixture_name=chosen_fixture or "kannada_ledger_sample.json",
                        )
                        st.session_state.extraction_result = result

        # Step 3: Display Extraction & Data Validation Results
        ext_result = st.session_state.extraction_result
        if ext_result is not None:
            st.markdown("---")
            if not ext_result.success:
                st.error(f"❌ Extraction Error: {ext_result.error_message}")
            else:
                st.subheader("📋 Extracted Ledger Records & Integrity Validation")

                # Metrics header
                m1, m2, m3, m4 = st.columns(4)
                with m1:
                    st.metric("Total Rows Extracted", ext_result.total_rows)
                with m2:
                    valid_count = ext_result.total_rows - ext_result.rows_needing_review
                    st.metric("Verified Valid", valid_count, delta=f"{valid_count} clean")
                with m3:
                    st.metric("Review Required", ext_result.rows_needing_review, delta=f"-{ext_result.rows_needing_review} flagged" if ext_result.rows_needing_review else "0", delta_color="inverse")
                with m4:
                    st.metric("Extraction Engine", ext_result.model_used)

                st.caption(
                    "🔒 **Milestone 2 Boundary**: Extracted rows are validated in-memory with decimal-safe arithmetic. "
                    "Records are NOT committed to Supabase PostgreSQL in this milestone."
                )

                # Render detailed row cards with validation pills
                for row in ext_result.rows:
                    status_badge = "⚠️ Review Required" if row.needs_review else "✅ Valid"
                    badge_color = "#fff3cd" if row.needs_review else "#d1e7dd"
                    border_color = "#ffeeba" if row.needs_review else "#badbcc"

                    with st.container():
                        st.markdown(
                            f"""
                            <div style="border: 1px solid {border_color}; background-color: {badge_color}; padding: 12px 16px; border-radius: 8px; margin-bottom: 8px;">
                                <strong>Row #{row.row_id}</strong> &nbsp;|&nbsp; 
                                <strong>Customer:</strong> {row.customer or '<em>(Not noted)</em>'} &nbsp;|&nbsp; 
                                <strong>Item:</strong> <code>{row.item}</code> 
                                {'&nbsp;<em>(' + row.standardized_item + ')</em>' if row.standardized_item else ''} &nbsp;|&nbsp; 
                                <strong>Qty:</strong> {row.quantity or '-'} {row.unit or ''} &nbsp;|&nbsp; 
                                <strong>Total:</strong> ₹{row.amount or 0:.2f} &nbsp;|&nbsp; 
                                <strong>Paid:</strong> ₹{row.paid or 0:.2f} &nbsp;|&nbsp; 
                                <strong>Due:</strong> ₹{row.due or 0:.2f} &nbsp;|&nbsp; 
                                <strong>Status:</strong> <strong>{status_badge}</strong>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                        if row.needs_review:
                            for reason in row.review_reasons:
                                st.warning(f"⚠️ {reason}")

                        if row.candidate_readings:
                            st.info(f"💡 Candidate readings detected: {row.candidate_readings}")

                        with st.expander(f"View Raw OCR Line & Confidence Details (Row #{row.row_id})"):
                            st.write(f"**Verbatim Handwriting**: `{row.raw_text}`")
                            st.write(f"**Field Confidences**: `{row.field_confidence}`")
                            st.write(f"**Validation Flags**: `{row.validation_flags}`")

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
                value="Ready" if config.is_gemma_configured else "Key Needed (Demo Mode Available)",
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

    with tab_schema:
        st.subheader("🗄️ PostgreSQL Supabase Schema (Milestone 1 Foundation)")
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
