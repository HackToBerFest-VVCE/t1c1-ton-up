"""Kirana Ledger Digitizer - Main Streamlit Application.

Team: Ton Up
Hackathon: Best Use of Gemma 4
Milestone: Milestone 2 - Ledger Image Upload, OpenCV Preprocessing, Gemma 4 Extraction & Data Validation
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import streamlit as st
except ImportError:
    st = None  # Handled gracefully if run outside streamlit runtime

from core.auth import admin_list_shopkeepers, is_admin_user, sign_in, sign_out, sign_up
from core.catalog import get_catalog_manager
from core.config import get_config
from core.db import (
    admin_assign_shop_by_email,
    assign_shop_owner,
    check_batch_exists,
    create_shop,
    fetch_available_shops,
    get_db_manager,
    provision_shop_with_owner,
    save_confirmed_entries,
)
from core.extractor import get_extractor
from core.i18n import get_language_manager, t
from core.image_preprocessor import get_preprocessor
from core.models import EntryStatus, ExtractedLedgerRow, LedgerEntry
from core.validator import revalidate_row


def clear_user_session() -> None:
    """Signs out of Supabase Auth and clears session state completely."""
    try:
        sign_out()
    except Exception:
        pass
    if st is not None:
        if "auth_user" in st.session_state:
            st.session_state.auth_user = None
        if "auth_token" in st.session_state:
            st.session_state.auth_token = None
        if "selected_shop_id" in st.session_state:
            st.session_state.selected_shop_id = None
        if "extraction_result" in st.session_state:
            st.session_state.extraction_result = None
        if "preprocessed_result" in st.session_state:
            st.session_state.preprocessed_result = None


def is_user_authenticated(session_state_dict: Optional[Dict[str, Any]] = None) -> bool:
    """Checks whether there is an active authenticated user in session state."""
    state = (
        session_state_dict
        if session_state_dict is not None
        else (st.session_state if st is not None else {})
    )
    user = state.get("auth_user")
    return bool(user and isinstance(user, dict) and user.get("id"))


def should_show_admin_panel(user: Optional[Dict[str, Any]]) -> bool:
    """Determines whether the admin provisioning panel should be shown."""
    return is_admin_user(user) if user else False


def should_show_main_app(user: Optional[Dict[str, Any]]) -> bool:
    """Determines whether the main application (tabs, upload, dashboard) should be shown."""
    return bool(user and isinstance(user, dict) and user.get("id"))


def _render_login_form(role_key: str, current_lang: str) -> None:
    """Unified login form for both shopkeepers and administrators."""
    with st.form(f"login_form_{role_key}"):
        email = st.text_input(
            label=t("auth.email", lang=current_lang, default="Email Address"),
            key=f"input_email_{role_key}",
        )
        password = st.text_input(
            label=t("auth.password", lang=current_lang, default="Password"),
            type="password",
            key=f"input_pwd_{role_key}",
        )
        btn_label = (
            t("auth.login_btn_shopkeeper", lang=current_lang, default="Sign In as Shopkeeper")
            if role_key == "shopkeeper"
            else t("auth.login_btn_admin", lang=current_lang, default="Sign In as Administrator")
        )
        submitted = st.form_submit_button(btn_label, use_container_width=True)

        if submitted:
            if not email.strip() or not password:
                st.error(
                    t(
                        "auth.credentials_required",
                        lang=current_lang,
                        default="Please enter both email and password.",
                    )
                )
                return

            with st.spinner("Authenticating with Supabase Auth..."):
                ok, res, err = sign_in(email.strip(), password)

            if ok and res:
                session = getattr(res, "session", None)
                user = getattr(res, "user", None)
                user_dict = {
                    "id": getattr(user, "id", None) if user else None,
                    "email": getattr(user, "email", None) if user else email.strip(),
                    "app_metadata": getattr(user, "app_metadata", {}) if user else {},
                }
                st.session_state.auth_user = user_dict
                st.session_state.auth_token = (
                    getattr(session, "access_token", None) if session else None
                )
                st.session_state.selected_shop_id = None
                st.success(
                    t("auth.login_success", lang=current_lang, default="Signed in successfully!")
                )
                st.rerun()
            else:
                st.error(err or "Login failed. Please verify your credentials.")


def render_login_screen(current_lang: str) -> None:
    """Renders the standalone login screen when the user is unauthenticated.

    Hides all tabs, upload forms, shop profiles, and dashboard content until
    authentication succeeds. Provides clear, distinct labels for Shopkeeper
    Login and Admin Login, backed by unified Supabase Auth.
    """
    st.title("📒 " + t("app.title", lang=current_lang, default="Kirana Ledger Digitizer"))
    st.markdown(
        f"*{t('app.subtitle', lang=current_lang, default='Digitize handwritten Indian kirana ledgers with Google Gemma 4')}*"
    )
    st.markdown("---")

    col_l, col_center, col_r = st.columns([1, 2, 1])
    with col_center:
        st.subheader("🔐 " + t("auth.section_title", lang=current_lang, default="Account & Login"))
        st.info(
            "ℹ️ "
            + t(
                "auth.login_description",
                lang=current_lang,
                default="Sign in to access your kirana ledger and manage transactions.",
            )
        )

        tab_shopkeeper, tab_admin = st.tabs([
            "👤 " + t("auth.shopkeeper_login", lang=current_lang, default="Shopkeeper Login"),
            "🛡️ " + t("auth.admin_login", lang=current_lang, default="Admin Login"),
        ])

        with tab_shopkeeper:
            st.caption(
                "ℹ️ "
                + t(
                    "auth.shopkeeper_login_desc",
                    lang=current_lang,
                    default="Log in with your shopkeeper credentials to access your store's ledger.",
                )
            )
            _render_login_form(role_key="shopkeeper", current_lang=current_lang)

        with tab_admin:
            st.caption(
                "🛡️ "
                + t(
                    "auth.admin_login_desc",
                    lang=current_lang,
                    default="Log in with administrator credentials to manage shops and accounts.",
                )
            )
            _render_login_form(role_key="admin", current_lang=current_lang)

        st.markdown("---")
        st.caption(
            "ℹ️ "
            + t(
                "auth.provisioning_notice",
                lang=current_lang,
                default="Accounts are provisioned by your platform administrator via Supabase Dashboard. Public sign-up is disabled.",
            )
        )


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
        key="app_language_selector",
    )

    # Immediately synchronize current_lang and session state with the chosen language
    current_lang = selected_code
    st.session_state.selected_language = selected_code

    # -------------------------------------------------------------------------
    # Authentication & Shopkeeper Account (Milestone 4)
    # -------------------------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔐 " + t("auth.section_title", lang=current_lang, default="Account & Login"))

    auth_user = st.session_state.get("auth_user")
    auth_token = st.session_state.get("auth_token")
    is_admin = is_admin_user(auth_user) if auth_user else False

    if auth_user:
        user_email = auth_user.get("email") or "Shopkeeper"
        if is_admin:
            role_label = t("auth.admin_badge", lang=current_lang, default="Administrator")
            st.sidebar.success(f"🛡️ **{role_label}**: `{user_email}`")
        else:
            role_label = t("auth.shopkeeper_badge", lang=current_lang, default="Shopkeeper")
            st.sidebar.info(f"👤 **{role_label}**: `{user_email}`")

        if st.sidebar.button("🚪 " + t("auth.logout_btn", lang=current_lang, default="Sign Out"), key="btn_logout"):
            clear_user_session()
            st.rerun()
    else:
        st.sidebar.info("🔒 " + t("auth.login_description", lang=current_lang, default="Please sign in to access your ledger."))
        st.sidebar.caption("ℹ️ " + t("auth.provisioning_notice", lang=current_lang, default="Accounts are provisioned by your platform administrator via Supabase Dashboard."))
        st.sidebar.markdown("---")
        st.sidebar.caption("Best Use of Gemma 4 Hackathon • 2026")
        return current_lang

    # -------------------------------------------------------------------------
    # Shop Selection & Management
    # -------------------------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("🏪 " + t("shop.title", lang=current_lang, default="Shop Profile"))

    config = get_config()
    user_id = auth_user.get("id") if auth_user else None

    # Administrators see all shops; shopkeepers only see their authorized shop
    available_shops = fetch_available_shops(
        owner_id=user_id, access_token=auth_token, is_admin=is_admin
    )
    shop_options: Dict[str, str] = {}
    active_shop_id = st.session_state.get("selected_shop_id") or config.shop_id

    unnamed_shop_label = t("shop.unnamed_shop", lang=current_lang, default="Unnamed Shop")
    for s in available_shops:
        s_id = str(s.get("id"))
        s_name = str(s.get("name") or unnamed_shop_label)
        s_owner = f" ({s.get('owner_name')})" if s.get("owner_name") else ""
        shop_options[s_id] = f"{s_name}{s_owner}"

    if shop_options:
        shop_ids = list(shop_options.keys())
        default_shop_idx = shop_ids.index(active_shop_id) if active_shop_id in shop_ids else 0
        chosen_shop_id = st.sidebar.selectbox(
            label=t("shop.select_label", lang=current_lang, default="Active Shop"),
            options=shop_ids,
            index=default_shop_idx,
            format_func=lambda sid: shop_options[sid],
        )
        st.session_state.selected_shop_id = chosen_shop_id
    else:
        if auth_user and not is_admin:
            st.sidebar.warning(t("auth.no_owned_shops", lang=current_lang, default="No shops registered for this account."))
        else:
            st.sidebar.info(t("shop.no_shops", lang=current_lang, default="No shops registered yet."))
        st.session_state.selected_shop_id = None

    # -------------------------------------------------------------------------
    # Administrator Shop Provisioning Panel (Visible ONLY to Admin)
    # -------------------------------------------------------------------------
    if is_admin:
        admin_panel_title = t("auth.admin_panel_title", lang=current_lang, default="Admin: Shop Management & Provisioning")
        with st.sidebar.expander(f"🛡️ {admin_panel_title}"):
            st.info(t("auth.admin_notice", lang=current_lang))

            # Sub-form 1: Automated Provisioning (Create Shop & Shopkeeper Account)
            st.markdown(
                f"**➕ {t('shop.create_and_provision_btn', lang=current_lang, default='Create Shop & Shopkeeper Account')}**"
            )
            if config.is_supabase_admin_configured:
                st.caption("🔒 " + t("shop.service_role_notice", lang=current_lang))

            with st.form("admin_provision_form", clear_on_submit=True):
                prov_name = st.text_input(
                    t("shop.name", lang=current_lang, default="Shop Name *"),
                    key="prov_shop_name",
                )
                prov_owner = st.text_input(
                    t("shop.owner", lang=current_lang, default="Owner Name"),
                    key="prov_shop_owner",
                )
                prov_email = st.text_input(
                    t("shop.shopkeeper_email", lang=current_lang, default="Shopkeeper Email *"),
                    key="prov_shop_email",
                )
                prov_pwd = st.text_input(
                    t(
                        "shop.shopkeeper_password",
                        lang=current_lang,
                        default="Initial Password (min 6 chars) *",
                    ),
                    type="password",
                    key="prov_shop_pwd",
                )
                prov_phone = st.text_input(
                    t("shop.phone", lang=current_lang, default="Phone Number"),
                    key="prov_shop_phone",
                )
                prov_address = st.text_input(
                    t("shop.address", lang=current_lang, default="Address"),
                    key="prov_shop_address",
                )
                prov_lang = st.selectbox(
                    t("shop.language", lang=current_lang, default="Default Language"),
                    options=codes,
                    index=codes.index(current_lang) if current_lang in codes else default_index,
                    format_func=lambda c: lang_options[c],
                    key="prov_shop_lang",
                )
                submit_prov = st.form_submit_button(
                    t(
                        "shop.create_and_provision_btn",
                        lang=current_lang,
                        default="Create Shop & Shopkeeper Account",
                    )
                )

                if submit_prov:
                    if not prov_name.strip():
                        st.error(
                            t(
                                "shop.error_name_required",
                                lang=current_lang,
                                default="Shop name is required.",
                            )
                        )
                    elif not prov_email.strip() or "@" not in prov_email:
                        st.error("A valid shopkeeper email address is required.")
                    elif not prov_pwd or len(prov_pwd) < 6:
                        st.error("Password must be at least 6 characters long.")
                    else:
                        with st.spinner("Provisioning shopkeeper account and shop record..."):
                            prov_ok, created_sid, created_uid, prov_err = (
                                provision_shop_with_owner(
                                    shop_data={
                                        "name": prov_name.strip(),
                                        "owner_name": prov_owner.strip() or None,
                                        "phone": prov_phone.strip() or None,
                                        "address": prov_address.strip() or None,
                                        "default_language": prov_lang,
                                    },
                                    owner_email=prov_email.strip(),
                                    owner_password=prov_pwd,
                                    current_admin_user=auth_user,
                                    access_token=auth_token,
                                )
                            )
                        if prov_ok and created_sid:
                            st.session_state.selected_shop_id = created_sid
                            st.success(
                                t(
                                    "shop.created_with_account_success",
                                    lang=current_lang,
                                    name=prov_name.strip(),
                                    email=prov_email.strip(),
                                    default=f"Shop '{prov_name.strip()}' and shopkeeper account '{prov_email.strip()}' created successfully!",
                                )
                            )
                            st.rerun()
                        else:
                            st.error(prov_err or "Failed to provision shop and account.")

            # Admin-Only Reassignment Tool (Email-Based, No UUIDs)
            if available_shops:
                st.markdown("---")
                st.markdown(
                    f"**🔄 {t('auth.reassign_tool_title', lang=current_lang, default='Reassign Shop to Shopkeeper')}**"
                )
                with st.form("admin_reassign_by_email_form"):
                    reassign_shop_id = st.selectbox(
                        t("shop.select_label", lang=current_lang, default="Active Shop"),
                        options=list(shop_options.keys()),
                        format_func=lambda sid: shop_options[sid],
                        key="reassign_shop_select",
                    )
                    shopkeepers = admin_list_shopkeepers(auth_user)
                    if shopkeepers:
                        sk_emails = [s["email"] for s in shopkeepers]
                        sk_label_map = {
                            s["email"]: (
                                f"{s['name']} ({s['email']})"
                                if s.get("name") and s["name"] != s["email"]
                                else s["email"]
                            )
                            for s in shopkeepers
                        }
                        chosen_email = st.selectbox(
                            t(
                                "auth.select_shopkeeper_label",
                                lang=current_lang,
                                default="Select Shopkeeper (by Email)",
                            ),
                            options=sk_emails,
                            format_func=lambda em: sk_label_map.get(em, em),
                            key="reassign_shopkeeper_select",
                        )
                    else:
                        chosen_email = st.text_input(
                            t(
                                "auth.shopkeeper_email_label",
                                lang=current_lang,
                                default="Shopkeeper Email Address",
                            ),
                            placeholder="e.g. shopkeeper@example.com",
                            key="reassign_shopkeeper_input",
                        )

                    submit_reassign = st.form_submit_button(
                        t("auth.reassign_btn", lang=current_lang, default="Assign Shop")
                    )
                    if submit_reassign:
                        clean_chosen_email = (chosen_email or "").strip().lower()
                        if not clean_chosen_email or "@" not in clean_chosen_email:
                            st.error(
                                t(
                                    "auth.credentials_required",
                                    lang=current_lang,
                                    default="Please enter a valid shopkeeper email address.",
                                )
                            )
                        else:
                            ok, err = admin_assign_shop_by_email(
                                shop_id=reassign_shop_id,
                                shopkeeper_email=clean_chosen_email,
                                current_admin_user=auth_user,
                                access_token=auth_token,
                            )
                            if ok:
                                st.success(
                                    t(
                                        "auth.reassign_by_email_success",
                                        lang=current_lang,
                                        email=clean_chosen_email,
                                        default=f"Shop reassigned successfully to '{clean_chosen_email}'!",
                                    )
                                )
                                st.rerun()
                            else:
                                st.error(err or "Failed to reassign shop.")

    st.sidebar.markdown("---")
    status_label_str = t("common.status", lang=current_lang, default="System Status")
    st.sidebar.subheader(f"📌 {status_label_str} (Milestone 4)")
    none_selected_str = t("shop.none_selected", lang=current_lang, default="None Selected")
    active_shop_display = (
        shop_options.get(st.session_state.selected_shop_id, none_selected_str)
        if st.session_state.selected_shop_id
        else f"⚠️ {none_selected_str}"
    )
    lang_label_str = t("common.language", lang=current_lang, default="Language")
    active_shop_label_str = t("shop.select_label", lang=current_lang, default="Active Shop")
    user_status_display = auth_user.get("email") if auth_user else "Guest / Offline"
    st.sidebar.info(
        f"**{lang_label_str}**: `{current_lang.upper()}`\n\n"
        f"**User**: `{user_status_display}`\n\n"
        f"**{active_shop_label_str}**: {active_shop_display}\n\n"
        f"**Milestone 4**: Supabase Auth & RLS Multi-Tenant Security"
    )

    st.sidebar.markdown("---")
    st.sidebar.caption("Best Use of Gemma 4 Hackathon • 2026")
    return current_lang


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
    if "app_language_selector" in st.session_state and st.session_state.app_language_selector:
        st.session_state.selected_language = st.session_state.app_language_selector
    elif "selected_language" not in st.session_state:
        st.session_state.selected_language = config.default_language
    if "extraction_result" not in st.session_state:
        st.session_state.extraction_result = None
    if "preprocessed_result" not in st.session_state:
        st.session_state.preprocessed_result = None

    selected_lang = render_sidebar(lang_mgr, st.session_state.selected_language)
    st.session_state.selected_language = selected_lang

    auth_user = st.session_state.get("auth_user")
    if not auth_user:
        render_login_screen(selected_lang)
        return

    # Warning banner if language pack needs native review (e.g. Telugu or Tamil)
    if lang_mgr.needs_native_review(selected_lang):
        review_msg = t("review_banner.notice", lang=selected_lang)
        st.warning(f"⚠️ **Native Review Needed**: {review_msg}")

    # Header section with visible Sign Out button and role badge
    app_title = t("app.title", lang=selected_lang)
    app_subtitle = t("app.subtitle", lang=selected_lang)
    is_admin = is_admin_user(auth_user)
    user_email = auth_user.get("email") or "User"

    col_header, col_user_box = st.columns([3, 1])
    with col_header:
        st.title(f"📒 {app_title}")
        st.markdown(f"*{app_subtitle}*")
    with col_user_box:
        if is_admin:
            role_label = t("auth.admin_badge", lang=selected_lang, default="Administrator")
            st.success(f"🛡️ **{role_label}**\n\n`{user_email}`")
        else:
            role_label = t("auth.shopkeeper_badge", lang=selected_lang, default="Shopkeeper")
            st.info(f"👤 **{role_label}**\n\n`{user_email}`")
        if st.button("🚪 " + t("auth.logout_btn", lang=selected_lang, default="Sign Out"), key="header_sign_out"):
            clear_user_session()
            st.rerun()

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
                        # Initialize editable rows in session state for Milestone 3 spreadsheet
                        st.session_state.edited_rows = [r for r in result.rows]
                        st.session_state.force_duplicate_save = False

        # Step 3: Milestone 3 Editable Spreadsheet & Supabase Confirmation Flow
        ext_result = st.session_state.extraction_result
        if ext_result is not None:
            st.markdown("---")
            if not ext_result.success:
                st.error(f"❌ Extraction Error: {ext_result.error_message}")
            else:
                st.subheader("📋 " + t("editor.title", lang=selected_lang, default="Transaction Review & Edit Table"))
                st.caption(t("editor.help", lang=selected_lang, default="Edit extracted values directly in the spreadsheet below. Add rows with '+' or delete rows by selecting them."))

                # Ensure edited_rows exists in session state
                if "edited_rows" not in st.session_state or st.session_state.edited_rows is None:
                    st.session_state.edited_rows = [r for r in ext_result.rows]

                # Convert current edited rows to DataFrame
                df_rows = []
                for r in st.session_state.edited_rows:
                    df_rows.append({
                        "row_id": int(r.row_id),
                        "date": r.date or "",
                        "customer": r.customer or "",
                        "customer_phone": r.customer_phone or "",
                        "item": r.item or "",
                        "quantity": float(r.quantity) if r.quantity is not None else 1.0,
                        "unit": r.unit or "unit",
                        "unit_price": float(r.unit_price) if r.unit_price is not None else 0.0,
                        "amount": float(r.amount) if r.amount is not None else 0.0,
                        "paid": float(r.paid) if r.paid is not None else None,
                        "due": float(r.due) if r.due is not None else None,
                        "status": "confirmed" if not r.needs_review else "pending",
                        "flags": ", ".join(r.validation_flags) if r.validation_flags else "CLEAN",
                    })

                df_current = pd.DataFrame(df_rows) if pd is not None else None

                if df_current is not None:
                    col_config = {
                        "row_id": st.column_config.NumberColumn("ID", disabled=True),
                        "date": st.column_config.TextColumn(t("editor.col_date", lang=selected_lang, default="Date")),
                        "customer": st.column_config.TextColumn(t("editor.col_customer", lang=selected_lang, default="Customer")),
                        "customer_phone": st.column_config.TextColumn(t("editor.col_phone", lang=selected_lang, default="Phone")),
                        "item": st.column_config.TextColumn(t("editor.col_item", lang=selected_lang, default="Item Name *")),
                        "quantity": st.column_config.NumberColumn(t("editor.col_qty", lang=selected_lang, default="Qty"), min_value=0.0, step=0.5),
                        "unit": st.column_config.TextColumn(t("editor.col_unit", lang=selected_lang, default="Unit")),
                        "unit_price": st.column_config.NumberColumn(t("editor.col_rate", lang=selected_lang, default="Rate (₹)"), min_value=0.0, step=1.0),
                        "amount": st.column_config.NumberColumn(t("editor.col_amount", lang=selected_lang, default="Total (₹) *"), min_value=0.0, step=1.0),
                        "paid": st.column_config.NumberColumn(t("editor.col_paid", lang=selected_lang, default="Paid (₹)"), min_value=0.0, step=1.0),
                        "due": st.column_config.NumberColumn(t("editor.col_due", lang=selected_lang, default="Due (₹)"), min_value=0.0, step=1.0),
                        "status": st.column_config.SelectboxColumn(
                            t("editor.col_status", lang=selected_lang, default="Status"),
                            options=["confirmed", "pending", "rejected"],
                        ),
                        "flags": st.column_config.TextColumn(t("editor.col_flags", lang=selected_lang, default="Flags"), disabled=True),
                    }

                    edited_df = st.data_editor(
                        df_current,
                        column_config=col_config,
                        num_rows="dynamic",
                        use_container_width=True,
                        key="ledger_table_editor",
                    )

                    # Null-safe Sanitizer & Field-Aware Revalidation
                    # Converts DataFrame rows back to ExtractedLedgerRow and runs revalidate_row()
                    existing_by_id = {r.row_id: r for r in st.session_state.edited_rows}
                    synced_rows: List[ExtractedLedgerRow] = []

                    for idx, row_dict in edited_df.iterrows():
                        def _clean_str(val: Any) -> Optional[str]:
                            if pd.isna(val) or val is None:
                                return None
                            s = str(val).strip()
                            return None if not s or s.lower() in ("none", "null", "nan") else s

                        def _clean_float(val: Any) -> Optional[float]:
                            if pd.isna(val) or val is None:
                                return None
                            try:
                                s = str(val).strip().replace(",", "")
                                if not s or s.lower() in ("none", "null", "nan"):
                                    return None
                                return float(s)
                            except (ValueError, TypeError):
                                return None

                        row_id_val = int(row_dict.get("row_id") or (idx + 1))
                        matched_row = existing_by_id.get(row_id_val)

                        if matched_row:
                            target_row = matched_row
                            target_row.date = _clean_str(row_dict.get("date"))
                            target_row.customer = _clean_str(row_dict.get("customer"))
                            target_row.customer_phone = _clean_str(row_dict.get("customer_phone"))
                            target_row.item = str(row_dict.get("item") or "").strip()
                            target_row.quantity = _clean_float(row_dict.get("quantity"))
                            target_row.unit = _clean_str(row_dict.get("unit")) or "unit"
                            target_row.unit_price = _clean_float(row_dict.get("unit_price"))
                            target_row.amount = _clean_float(row_dict.get("amount"))
                            target_row.paid = _clean_float(row_dict.get("paid"))
                            target_row.due = _clean_float(row_dict.get("due"))
                        else:
                            # Manually added row from '+' button
                            target_row = ExtractedLedgerRow(
                                row_id=row_id_val,
                                date=_clean_str(row_dict.get("date")),
                                customer=_clean_str(row_dict.get("customer")),
                                customer_phone=_clean_str(row_dict.get("customer_phone")),
                                item=str(row_dict.get("item") or "").strip(),
                                quantity=_clean_float(row_dict.get("quantity")),
                                unit=_clean_str(row_dict.get("unit")) or "unit",
                                unit_price=_clean_float(row_dict.get("unit_price")),
                                amount=_clean_float(row_dict.get("amount")),
                                paid=_clean_float(row_dict.get("paid")),
                                due=_clean_float(row_dict.get("due")),
                            )

                        # Revalidate row preserving untouched uncertainty flags
                        revalidated = revalidate_row(target_row)
                        synced_rows.append(revalidated)

                    st.session_state.edited_rows = synced_rows

                # Dynamic Financial & Verification Metrics Header
                active_rows = st.session_state.edited_rows
                total_sales = sum(float(r.amount) for r in active_rows if r.amount is not None)
                total_paid = sum(float(r.paid) for r in active_rows if r.paid is not None)
                total_due = sum(float(r.due) for r in active_rows if r.due is not None)
                flagged_count = sum(1 for r in active_rows if r.needs_review)

                st.markdown("---")
                m1, m2, m3, m4 = st.columns(4)
                with m1:
                    st.metric(
                        t("dashboard.total_sales", lang=selected_lang, default="Total Sales"),
                        f"₹{total_sales:.2f}",
                        delta=f"{len(active_rows)} transactions",
                    )
                with m2:
                    st.metric(
                        t("dashboard.total_collected", lang=selected_lang, default="Total Collected"),
                        f"₹{total_paid:.2f}",
                    )
                with m3:
                    st.metric(
                        t("dashboard.total_credit", lang=selected_lang, default="Outstanding Dues"),
                        f"₹{total_due:.2f}",
                        delta=f"₹{total_due:.2f} credit" if total_due > 0 else "0 dues",
                        delta_color="inverse",
                    )
                with m4:
                    st.metric(
                        t("upload.review_required", lang=selected_lang, default="Needs Review"),
                        flagged_count,
                        delta=f"-{flagged_count} flagged" if flagged_count else "All Clean",
                        delta_color="inverse" if flagged_count else "normal",
                    )

                # Flag Details Expander
                flagged_rows = [r for r in active_rows if r.needs_review]
                if flagged_rows:
                    with st.expander(f"⚠️ {len(flagged_rows)} Rows Requiring Attention (Click to expand)"):
                        for fr in flagged_rows:
                            st.markdown(f"**Row #{fr.row_id} (`{fr.item or 'Unknown'}`)**: {', '.join(fr.review_reasons)}")

                # Deterministic Batch ID Generation (SHA-256)
                if st.session_state.get("preprocessed_result") is not None:
                    batch_bytes = st.session_state.preprocessed_result.to_processed_bytes()
                    computed_batch_id = hashlib.sha256(batch_bytes).hexdigest()[:16]
                else:
                    computed_batch_id = hashlib.sha256(
                        (chosen_fixture or "fixture").encode("utf-8")
                    ).hexdigest()[:16]

                st.markdown("---")
                col_save_btn, col_save_status = st.columns([2, 3])

                with col_save_btn:
                    save_button_label = (
                        t("editor.confirm_save_btn", lang=selected_lang, default="Confirm & Save to Supabase")
                    )
                    do_save = st.button(save_button_label, type="primary", key="btn_save_confirmed_records")

                with col_save_status:
                    active_shop_id = st.session_state.get("selected_shop_id")
                    if not active_shop_id:
                        st.warning(
                            "⚠️ "
                            + t(
                                "editor.no_shop_warning",
                                lang=selected_lang,
                                default="Select or register an active shop in the sidebar before saving.",
                            )
                        )
                    else:
                        active_label = t("shop.select_label", lang=selected_lang, default="Active Shop")
                        st.caption(f"{active_label}: `{active_shop_id}` &nbsp;|&nbsp; Batch ID: `{computed_batch_id}`")
                        if not st.session_state.get("auth_token"):
                            st.caption("ℹ️ " + t("auth.demo_unauthenticated_hint", lang=selected_lang))

                # Handle Save Action
                if do_save:
                    if not active_shop_id:
                        st.error(t("shop.required_warning", lang=selected_lang))
                    else:
                        # Build confirmed LedgerEntry records (strictly confirmed status)
                        confirmed_entries = []
                        for r in active_rows:
                            entry = r.to_ledger_entry(
                                shop_id=active_shop_id,
                                batch_id=computed_batch_id,
                                row_index=r.row_id,
                                status=EntryStatus.CONFIRMED,
                            )
                            confirmed_entries.append(entry)

                        with st.spinner("Persisting confirmed transactions into Supabase PostgreSQL..."):
                            save_success, save_message, saved_count = save_confirmed_entries(
                                entries=confirmed_entries,
                                shop_id=active_shop_id,
                                batch_id=computed_batch_id,
                                allow_duplicate=st.session_state.get("force_duplicate_save", False),
                                access_token=st.session_state.get("auth_token"),
                            )

                        if save_success:
                            st.success(
                                t(
                                    "editor.save_success",
                                    lang=selected_lang,
                                    count=saved_count,
                                    batch_id=computed_batch_id,
                                )
                            )
                            st.balloons()
                            st.session_state.force_duplicate_save = False
                        elif "DUPLICATE_BATCH" in save_message:
                            st.warning(
                                t(
                                    "editor.duplicate_warning",
                                    lang=selected_lang,
                                    count=saved_count,
                                )
                            )
                            if st.button(
                                "⚠️ "
                                + t(
                                    "editor.btn_force_save",
                                    lang=selected_lang,
                                    default="Yes, Save Duplicate Batch",
                                ),
                                key="btn_force_duplicate",
                            ):
                                st.session_state.force_duplicate_save = True
                                st.rerun()
                        elif "RLS_ERROR_42501" in save_message or "42501" in save_message:
                            st.error(f"🔒 {save_message}")
                            st.info(
                                "To resolve RLS policy restrictions, ensure you are signed in and run: "
                                "`sql/migrations/002_add_auth_and_rls.sql` in the Supabase SQL editor."
                            )
                        elif "MIGRATION_REQUIRED" in save_message:
                            st.error(save_message)
                            st.info(
                                "To apply the migration, open your Supabase Dashboard SQL Editor and run: "
                                "`sql/migrations/001_add_paid_due_and_idempotency.sql`"
                            )
                        else:
                            st.error(save_message)

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
