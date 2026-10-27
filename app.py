"""Kirana Ledger Digitizer - Main Streamlit Application.

Team: Ton Up
Hackathon: Best Use of Gemma 4
Milestone: Milestone 2 - Ledger Image Upload, OpenCV Preprocessing, Gemma 4 Extraction & Data Validation
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    import plotly.express as px
except ImportError:
    px = None

try:
    import streamlit as st
except ImportError:
    st = None  # Handled gracefully if run outside streamlit runtime

from core.analytics import compute_dashboard_metrics
from core.assistant import ask_gemma_business_assistant
from core.auth import (
    admin_create_user,
    admin_list_users,
    is_admin_user,
    sign_in,
    sign_out,
    sign_up,
)
from core.catalog import get_catalog_manager
from core.config import get_config
from core.db import (
    admin_assign_shop_by_email,
    assign_shop_owner,
    check_batch_exists,
    count_shop_ledger_entries,
    create_shop,
    delete_shop,
    fetch_available_shops,
    fetch_confirmed_ledger_entries,
    get_db_manager,
    provision_shop_with_owner,
    resolve_owner_shop,
    save_confirmed_entries,
    update_shop,
)
from core.exporter import export_ledger_to_excel
from core.extractor import get_extractor
from core.i18n import get_language_manager, t
from core.image_preprocessor import get_preprocessor
from core.models import EntryStatus, ExtractedLedgerRow, LedgerEntry
from core.validator import (
    STATUS_FULLY_PAID,
    STATUS_HAS_DUE,
    calculate_payment_split,
    revalidate_row,
    sync_editor_rows_with_changes,
    update_row_payment,
)


def clear_user_session() -> None:
    """Signs out of Supabase Auth and clears session state completely."""
    try:
        sign_out()
    except Exception:
        pass
    if st is not None:
        if "auth_user" in st.session_state:
            st.session_state["auth_user"] = None
        if "auth_token" in st.session_state:
            st.session_state["auth_token"] = None
        if "selected_shop_id" in st.session_state:
            st.session_state["selected_shop_id"] = None
        if "extraction_result" in st.session_state:
            st.session_state["extraction_result"] = None
        if "preprocessed_result" in st.session_state:
            st.session_state["preprocessed_result"] = None
        if "dashboard_records" in st.session_state:
            st.session_state["dashboard_records"] = None
        if "dashboard_shop_id" in st.session_state:
            st.session_state["dashboard_shop_id"] = None
        if "dashboard_last_refreshed" in st.session_state:
            st.session_state["dashboard_last_refreshed"] = None
        if "dashboard_refresh_needed" in st.session_state:
            st.session_state["dashboard_refresh_needed"] = False
        if "last_save_status" in st.session_state:
            st.session_state["last_save_status"] = None
        if "assistant_messages" in st.session_state:
            st.session_state["assistant_messages"] = []
        if "assistant_shop_id" in st.session_state:
            st.session_state["assistant_shop_id"] = None
        if "assistant_last_error" in st.session_state:
            st.session_state["assistant_last_error"] = None


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


def should_show_admin_portal(user: Optional[Dict[str, Any]]) -> bool:
    """Determines whether the dedicated Admin Portal tab should be shown on the main page."""
    return is_admin_user(user) if user else False


def should_show_shopkeeper_dashboard(user: Optional[Dict[str, Any]]) -> bool:
    """Determines whether the shopkeeper-facing Dashboard & Insights should be shown on the main page."""
    return not is_admin_user(user) if user else False


def should_show_main_app(user: Optional[Dict[str, Any]]) -> bool:
    """Determines whether the main application (tabs, upload, dashboard) should be shown."""
    return bool(user and isinstance(user, dict) and user.get("id"))


def _render_login_form(role_key: str = "unified", current_lang: str = "en") -> None:
    """Unified login form for authenticating users via Supabase Auth."""
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
        btn_label = t("auth.login_btn", lang=current_lang, default="Sign In")
        submitted = st.form_submit_button(
            "🔐 " + btn_label,
            use_container_width=True,
            type="primary",
        )

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
    authentication succeeds. Unified Supabase Auth for all users.
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

        _render_login_form(role_key="unified", current_lang=current_lang)

        st.markdown("---")
        st.caption(
            "ℹ️ "
            + t(
                "auth.provisioning_notice",
                lang=current_lang,
                default="User accounts are created by the platform administrator via the Admin Portal.",
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
    # Single-Shop-Owner Profile & Auto-Resolution
    # -------------------------------------------------------------------------
    st.sidebar.markdown("---")
    st.sidebar.subheader("🏪 " + t("shop.title", lang=current_lang, default="Shop Profile"))

    user_id = auth_user.get("id") if auth_user else None

    # Automatically resolve the authenticated owner's single shop
    resolved_id, resolved_name, resolved_shop = resolve_owner_shop(
        auth_user=auth_user,
        access_token=auth_token,
        is_admin=is_admin,
        current_selected_id=st.session_state.get("selected_shop_id"),
    )

    available_shops = fetch_available_shops(
        owner_id=user_id, access_token=auth_token, is_admin=is_admin
    )
    shop_options: Dict[str, str] = {}
    for s in available_shops:
        s_id = str(s.get("id"))
        s_name = str(s.get("name") or t("shop.unnamed_shop", lang=current_lang, default="Unnamed Shop"))
        s_owner = f" ({s.get('owner_name')})" if s.get("owner_name") else ""
        shop_options[s_id] = f"{s_name}{s_owner}"

    if resolved_id:
        st.session_state.selected_shop_id = resolved_id
        st.sidebar.success(f"🏪 **{resolved_name}**")
        if resolved_shop:
            owner_disp = resolved_shop.get("owner_name")
            phone_disp = resolved_shop.get("phone")
            addr_disp = resolved_shop.get("address")
            if owner_disp:
                st.sidebar.caption(f"👤 **{t('shop.owner', lang=current_lang, default='Owner')}**: {owner_disp}")
            if phone_disp:
                st.sidebar.caption(f"📞 {phone_disp}")
            if addr_disp:
                st.sidebar.caption(f"📍 {addr_disp}")

        # In testing environments with multiple shops, allow switching
        if len(available_shops) > 1:
            s_ids = list(shop_options.keys())
            def_idx = s_ids.index(resolved_id) if resolved_id in s_ids else 0
            chosen_shop_id = st.sidebar.selectbox(
                label=t("shop.select_label", lang=current_lang, default="Active Shop"),
                options=s_ids,
                index=def_idx,
                format_func=lambda sid: shop_options[sid],
            )
            if chosen_shop_id != resolved_id:
                st.session_state.selected_shop_id = chosen_shop_id
                st.rerun()
    else:
        if auth_user and not is_admin:
            st.sidebar.warning(t("auth.no_owned_shops", lang=current_lang, default="No shops registered for this account."))
        else:
            st.sidebar.info(t("shop.no_shops", lang=current_lang, default="No shops registered yet."))
        st.session_state.selected_shop_id = None

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


def invalidate_dashboard_cache() -> None:
    """Invalidates the in-memory dashboard cache so the next view fetches fresh data from Supabase."""
    if st is not None:
        st.session_state["dashboard_records"] = None
        st.session_state["dashboard_refresh_needed"] = True


def get_cached_dashboard_records(active_shop_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """Retrieves cached dashboard records from session state if matching active_shop_id."""
    if st is None:
        return None
    cached_sid = st.session_state.get("dashboard_shop_id")
    if cached_sid == active_shop_id and not st.session_state.get("dashboard_refresh_needed", False):
        return st.session_state.get("dashboard_records")
    return None


def render_dashboard(
    selected_lang: str,
    active_shop_id: Optional[str],
    active_shop_name: str,
) -> None:
    """Renders the Milestone 5 & 6 Shopkeeper Dashboard and Business Insights view."""
    col_dash_title, col_dash_refresh = st.columns([3, 1])
    with col_dash_title:
        st.subheader(f"📊 {t('nav.dashboard', lang=selected_lang, default='Dashboard')} — {active_shop_name}")
        st.caption("Milestone 5 & 6: Business Insights, Visual Trends, Customer Dues & Excel Export")
    with col_dash_refresh:
        refresh_label = "🔄 " + t("dashboard.refresh_btn", lang=selected_lang, default="Refresh Dashboard")
        btn_refresh = st.button(refresh_label, key="btn_refresh_dashboard", type="secondary")
        last_time = st.session_state.get("dashboard_last_refreshed")
        if last_time:
            st.caption(t("dashboard.last_refreshed", lang=selected_lang, time=last_time, default=f"Last updated: {last_time}"))

    current_auth_token = st.session_state.get("auth_token")

    if not active_shop_id:
        st.warning("⚠️ " + t("editor.no_shop_warning", lang=selected_lang, default="Please select or register an active shop in the sidebar before viewing dashboard analytics."))
    else:
        # Check whether we need to query fresh records from Supabase
        cached_records = get_cached_dashboard_records(active_shop_id)
        needs_refresh = btn_refresh or (cached_records is None)

        if needs_refresh:
            with st.spinner("Fetching confirmed records from Supabase..."):
                confirmed_records = fetch_confirmed_ledger_entries(shop_id=active_shop_id, access_token=current_auth_token)
            st.session_state["dashboard_records"] = confirmed_records
            st.session_state["dashboard_shop_id"] = active_shop_id
            st.session_state["dashboard_last_refreshed"] = datetime.now().strftime("%I:%M:%S %p")
            st.session_state["dashboard_refresh_needed"] = False
            if btn_refresh:
                st.success("🔄 " + t("dashboard.refresh_success", lang=selected_lang, default="Dashboard refreshed with latest transactions!"))
        else:
            confirmed_records = cached_records or []

        if not confirmed_records:
            st.info("ℹ️ " + t("dashboard.no_data_hint", lang=selected_lang, default="No confirmed transactions found for this shop yet. Upload and confirm ledger sheets in the 'Upload & Extract' tab to view analytics."))
        else:
            metrics = compute_dashboard_metrics(confirmed_records, lang=selected_lang)
            summary = metrics["summary"]
            top_products = metrics["top_products"]
            customer_balances = metrics["customer_balances"]
            insights = metrics["insights"]

            # 1. KPI Cards Row
            active_customers_count = len([c for c in customer_balances if c.get("customer_name")])
            k1, k2, k3, k4, k5 = st.columns(5)
            with k1:
                st.metric(
                    label=t("dashboard.total_sales", lang=selected_lang, default="Total Sales (₹)"),
                    value=f"₹{summary['total_sales']:.2f}",
                )
            with k2:
                st.metric(
                    label=t("dashboard.total_collected", lang=selected_lang, default="Total Collected (₹)"),
                    value=f"₹{summary['total_collected']:.2f}",
                )
            with k3:
                st.metric(
                    label=t("dashboard.total_credit", lang=selected_lang, default="Outstanding Dues (₹)"),
                    value=f"₹{summary['total_due']:.2f}",
                    delta=f"₹{summary['total_due']:.2f} credit" if summary["total_due"] > 0 else "0 dues",
                    delta_color="inverse",
                )
            with k4:
                st.metric(
                    label=t("dashboard.transaction_count", lang=selected_lang, default="Total Transactions"),
                    value=summary["transaction_count"],
                )
            with k5:
                st.metric(
                    label=t("dashboard.active_customers", lang=selected_lang, default="Active Customers"),
                    value=active_customers_count,
                )

            st.markdown("---")

            # 2. Plotly Charts Row
            chart_col1, chart_col2 = st.columns(2)

            with chart_col1:
                if px is not None and pd is not None:
                    cash_amt = summary["total_collected"]
                    due_amt = summary["total_due"]
                    if cash_amt > 0 or due_amt > 0:
                        payment_df = pd.DataFrame([
                            {"Category": t("common.cash", lang=selected_lang, default="Cash (Jama)"), "Amount": cash_amt},
                            {"Category": t("common.credit", lang=selected_lang, default="Credit (Udhaar)"), "Amount": due_amt},
                        ])
                        fig_payment = px.pie(
                            payment_df,
                            values="Amount",
                            names="Category",
                            title=t("dashboard.payment_distribution", lang=selected_lang, default="Payment Distribution (Cash vs. Credit)"),
                            hole=0.45,
                            color="Category",
                            color_discrete_map={
                                t("common.cash", lang=selected_lang, default="Cash (Jama)"): "#10b981",
                                t("common.credit", lang=selected_lang, default="Credit (Udhaar)"): "#ef4444",
                            },
                        )
                        fig_payment.update_traces(textposition="inside", textinfo="percent+label")
                        fig_payment.update_layout(margin=dict(t=40, b=20, l=20, r=20), height=320)
                        st.plotly_chart(fig_payment, use_container_width=True)
                    else:
                        st.info("No monetary data available to chart payment distribution.")
                else:
                    st.info("Plotly is required for interactive payment charts.")

            with chart_col2:
                if px is not None and pd is not None:
                    top_rev_items = top_products.get("by_revenue", [])[:5]
                    if top_rev_items:
                        df_top = pd.DataFrame(top_rev_items)
                        df_top_sorted = df_top.sort_values(by="total_revenue", ascending=True)
                        fig_products = px.bar(
                            df_top_sorted,
                            x="total_revenue",
                            y="product_name",
                            orientation="h",
                            title=t("dashboard.top_products_chart", lang=selected_lang, default="Top 5 Products by Revenue"),
                            labels={
                                "total_revenue": t("dashboard.total_sales", lang=selected_lang, default="Revenue (₹)"),
                                "product_name": t("ledger.item_name", lang=selected_lang, default="Product"),
                            },
                            text="total_revenue",
                            color="total_revenue",
                            color_continuous_scale="Blues",
                        )
                        fig_products.update_traces(texttemplate="₹%{text:.2f}", textposition="outside")
                        fig_products.update_layout(
                            showlegend=False,
                            coloraxis_showscale=False,
                            margin=dict(t=40, b=20, l=20, r=20),
                            height=320,
                        )
                        st.plotly_chart(fig_products, use_container_width=True)
                    else:
                        st.info("No product data available to chart.")
                else:
                    st.info("Plotly is required for interactive product charts.")

            st.markdown("---")

            # 3. Multilingual Business Insights Section
            st.subheader(f"💡 {t('insights.title', lang=selected_lang, default='Business Intelligence & Insights')}")
            if insights:
                for ins in insights:
                    if ins.get("type") == "warning":
                        st.warning(f"⚠️ **{ins.get('title', '')}**: {ins.get('message', '')}")
                    else:
                        st.success(f"📈 **{ins.get('title', '')}**: {ins.get('message', '')}")
            else:
                st.info("No insight alerts at this time. All customer accounts and product sales are balanced.")

            st.markdown("---")

            # 4. Customer Outstanding Dues Table
            st.subheader(f"👥 {t('dashboard.customer_dues_title', lang=selected_lang, default='Customer Outstanding Dues (Udhaar Khata)')}")
            if customer_balances:
                dues_table_data = []
                for c in customer_balances:
                    status_badge = "🔴 Pending Due" if c["current_due"] > 0 else "🟢 Cleared"
                    dues_table_data.append({
                        t("ledger.customer_name", lang=selected_lang, default="Customer Name"): c["customer_name"],
                        t("ledger.customer_phone", lang=selected_lang, default="Customer Phone"): c["customer_phone"] or "—",
                        t("ledger.total_amount", lang=selected_lang, default="Total Billed (₹)"): f"₹{c['total_billed']:.2f}",
                        t("editor.col_paid", lang=selected_lang, default="Paid (₹)"): f"₹{c['total_paid']:.2f}",
                        t("editor.col_due", lang=selected_lang, default="Due (₹)"): f"₹{c['current_due']:.2f}",
                        t("common.status", lang=selected_lang, default="Status"): status_badge,
                        t("common.date", lang=selected_lang, default="Last Date"): c["last_transaction_date"] or "—",
                    })
                st.dataframe(dues_table_data, use_container_width=True)
            else:
                st.info("No customer credit accounts found. All transactions were cash or walk-in sales.")

            st.markdown("---")

            # 5. Excel Export Section
            st.subheader(f"📥 {t('common.export_excel', lang=selected_lang, default='Export to Excel')}")
            st.markdown("Download all confirmed ledger transactions, customer dues summaries, and product performance as a multi-sheet spreadsheet.")

            excel_data = export_ledger_to_excel(
                confirmed_records,
                shop_name=active_shop_name,
                lang=selected_lang,
            )
            clean_shop_slug = "".join(c if c.isalnum() else "_" for c in active_shop_name).strip("_") or "shop"
            excel_filename = f"{clean_shop_slug}_ledger_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

            st.download_button(
                label="📥 " + t("common.export_excel", lang=selected_lang, default="Export to Excel (.xlsx)"),
                data=excel_data,
                file_name=excel_filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="btn_download_excel_dashboard",
                type="primary",
            )

        st.markdown("---")

        # 6. Gemma 4 AI Business Assistant Section
        render_ai_assistant(
            selected_lang=selected_lang,
            confirmed_records=confirmed_records or [],
            active_shop_name=active_shop_name,
            active_shop_id=active_shop_id,
        )


def render_ai_assistant(
    selected_lang: str,
    confirmed_records: List[Dict[str, Any]],
    active_shop_name: str,
    active_shop_id: str,
) -> None:
    """Renders the conversational Gemma 4 AI Business Assistant grounded in ledger facts."""
    # Ensure assistant conversation history belongs strictly to the active shop (multi-tenant safety)
    if st.session_state.get("assistant_shop_id") != active_shop_id:
        st.session_state["assistant_messages"] = []
        st.session_state["assistant_shop_id"] = active_shop_id
        st.session_state["assistant_last_error"] = None

    if "assistant_messages" not in st.session_state or st.session_state["assistant_messages"] is None:
        st.session_state["assistant_messages"] = []

    header_col, badge_col = st.columns([3, 2])
    with header_col:
        st.subheader(f"🤖 {t('assistant.title', lang=selected_lang, default='Gemma 4 AI Business Assistant')}")
        st.caption(t("assistant.subtitle", lang=selected_lang, default="Ask business questions grounded in your confirmed ledger records"))
    with badge_col:
        st.info("🔒 " + t("assistant.grounding_badge", lang=selected_lang, default="100% Grounded in Confirmed Ledger Records"))

    # Suggested Questions Row
    st.markdown(f"**💡 {t('assistant.suggested_title', lang=selected_lang, default='Suggested Questions:')}**")
    sug_q1 = t("assistant.suggested_q1", lang=selected_lang, default="What are my total sales and collected cash?")
    sug_q2 = t("assistant.suggested_q2", lang=selected_lang, default="Which are my top 5 selling items?")
    sug_q3 = t("assistant.suggested_q3", lang=selected_lang, default="Who owes the most pending dues?")
    sug_q4 = t("assistant.suggested_q4", lang=selected_lang, default="How do recent sales compare over time?")
    sug_q5 = t("assistant.suggested_q5", lang=selected_lang, default="Give me an executive business summary.")

    pending_query = None
    sq_cols = st.columns(5)
    with sq_cols[0]:
        if st.button(f"💰 {sug_q1}", key="btn_sug_q1", use_container_width=True) is True:
            pending_query = sug_q1
    with sq_cols[1]:
        if st.button(f"📦 {sug_q2}", key="btn_sug_q2", use_container_width=True) is True:
            pending_query = sug_q2
    with sq_cols[2]:
        if st.button(f"👥 {sug_q3}", key="btn_sug_q3", use_container_width=True) is True:
            pending_query = sug_q3
    with sq_cols[3]:
        if st.button(f"📅 {sug_q4}", key="btn_sug_q4", use_container_width=True) is True:
            pending_query = sug_q4
    with sq_cols[4]:
        if st.button(f"📋 {sug_q5}", key="btn_sug_q5", use_container_width=True) is True:
            pending_query = sug_q5

    # Render Conversation History
    messages = st.session_state.get("assistant_messages", [])
    if messages:
        st.markdown("##### 💬 " + t("assistant.title", lang=selected_lang, default="Chat History"))
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            metrics = msg.get("metrics")
            if hasattr(st, "chat_message"):
                with st.chat_message(role):
                    st.markdown(content)
                    if role == "assistant" and metrics:
                        tot_s = metrics.get("total_request_time_ms", 0) / 1000.0
                        api_s = metrics.get("api_latency_ms", 0) / 1000.0
                        calc_ms = metrics.get("analytics_calc_time_ms", 0)
                        cached_tag = " (cached)" if metrics.get("cached") else ""
                        st.caption(f"⚡ *Generated in {tot_s:.2f}s (API: {api_s:.2f}s | Analytics: {calc_ms:.1f}ms){cached_tag}*")
            else:
                prefix = "🧑 **You:**" if role == "user" else "🤖 **Gemma 4:**"
                st.markdown(f"{prefix} {content}")
                if role == "assistant" and metrics:
                    tot_s = metrics.get("total_request_time_ms", 0) / 1000.0
                    api_s = metrics.get("api_latency_ms", 0) / 1000.0
                    calc_ms = metrics.get("analytics_calc_time_ms", 0)
                    cached_tag = " (cached)" if metrics.get("cached") else ""
                    st.caption(f"⚡ *Generated in {tot_s:.2f}s (API: {api_s:.2f}s | Analytics: {calc_ms:.1f}ms){cached_tag}*")

    # Retry banner if last call errored
    last_error = st.session_state.get("assistant_last_error")
    if last_error:
        err_query, err_msg = last_error
        st.error(f"⚠️ {err_msg}")
        col_retry, _ = st.columns([1, 4])
        with col_retry:
            if st.button(f"🔄 {t('assistant.retry_btn', lang=selected_lang, default='Retry Question')}", key="btn_retry_assistant") is True:
                pending_query = err_query
                st.session_state["assistant_last_error"] = None

    # Chat Input Row with Send and Clear Chat buttons
    with st.form("assistant_chat_form", clear_on_submit=True):
        input_col, send_col = st.columns([4, 1])
        with input_col:
            user_input = st.text_input(
                label=t("assistant.title", lang=selected_lang, default="Gemma 4 AI Assistant"),
                placeholder=t("assistant.input_placeholder", lang=selected_lang, default="Ask about your sales, pending dues, fast-moving items, or trends..."),
                key="assistant_user_text_input",
                label_visibility="collapsed",
            )
        with send_col:
            btn_send = st.form_submit_button(
                label="🚀 " + t("assistant.send_btn", lang=selected_lang, default="Send"),
                use_container_width=True,
            )
            if btn_send is True and isinstance(user_input, str) and user_input.strip():
                pending_query = user_input.strip()

    col_clear, _ = st.columns([1, 5])
    with col_clear:
        if st.button(f"🗑️ {t('assistant.clear_btn', lang=selected_lang, default='Clear Chat')}", key="btn_clear_assistant_chat") is True:
            st.session_state["assistant_messages"] = []
            st.session_state["assistant_last_error"] = None
            if hasattr(st, "rerun"):
                st.rerun()

    # Process query if submitted via text or suggested button or retry button
    if pending_query:
        st.session_state["assistant_messages"].append({"role": "user", "content": pending_query})
        with st.spinner(t("assistant.thinking", lang=selected_lang, default="Consulting Gemma 4 with verified ledger facts...")):
            success, reply = ask_gemma_business_assistant(
                query=pending_query,
                entries=confirmed_records,
                shop_name=active_shop_name,
                lang=selected_lang,
            )

        if success:
            metrics = getattr(reply, "metrics", {}) or {}
            st.session_state["assistant_messages"].append({
                "role": "assistant",
                "content": str(reply),
                "metrics": metrics,
            })
            st.session_state["assistant_last_error"] = None
        else:
            st.session_state["assistant_last_error"] = (pending_query, str(reply))

        if hasattr(st, "rerun"):
            st.rerun()


def render_admin_portal(selected_lang: str, auth_user: Dict[str, Any]) -> None:
    """Renders the Admin Portal for multi-shop and single-user-per-shop provisioning, directory inspection, editing, and safe deletion."""
    portal_title = t("admin.portal_title", lang=selected_lang, default="Admin Portal — Store Administration")
    st.subheader(f"🛡️ {portal_title}")
    st.caption("Store administrative control: provision shops with designated user accounts, inspect registered stores, update business details, and manage stores.")

    current_auth_token = st.session_state.get("auth_token")
    is_admin = is_admin_user(auth_user)

    # Automatically resolve the authenticated owner's single shop
    active_shop_id, active_shop_name, active_shop = resolve_owner_shop(
        auth_user=auth_user,
        access_token=current_auth_token,
        is_admin=is_admin,
        current_selected_id=st.session_state.get("selected_shop_id"),
    )

    # Case 1: Non-admin Initial Setup (if non-admin without a shop reaches this view)
    if not is_admin and not active_shop:
        st.info("ℹ️ " + t("shop.no_shops", lang=selected_lang, default="No shop registered yet. Complete initial setup below to register your store."))
        with st.form("owner_initial_setup_form"):
            st.markdown(f"### 🏪 {t('shop.register_new', lang=selected_lang, default='Register New Shop')}")
            st.caption("One-time setup: configure your single kirana store profile. This shop will be automatically linked to your account.")
            new_name = st.text_input(t("shop.name", lang=selected_lang, default="Shop Name *"), key="init_shop_name")
            new_owner = st.text_input(t("shop.owner", lang=selected_lang, default="Owner Name"), key="init_shop_owner")
            new_phone = st.text_input(t("shop.phone", lang=selected_lang, default="Phone Number"), key="init_shop_phone")
            new_addr = st.text_area(t("shop.address", lang=selected_lang, default="Address / Locality"), key="init_shop_addr")

            supported_langs = ["kn", "en", "hi", "te", "ta"]
            lang_labels = {
                "kn": "ಕನ್ನಡ (Kannada)",
                "en": "English",
                "hi": "हिंदी (Hindi)",
                "te": "తెలుగు (Telugu)",
                "ta": "தமிழ் (Tamil)",
            }
            new_lang = st.selectbox(
                t("shop.language", lang=selected_lang, default="Default Language"),
                options=supported_langs,
                format_func=lambda c: lang_labels.get(c, c),
                key="init_shop_lang",
            )
            submit_init = st.form_submit_button("🚀 " + t("shop.create_btn", lang=selected_lang, default="Register Shop"), type="primary")
            if submit_init:
                if not new_name.strip():
                    st.error("Shop name is required.")
                else:
                    shop_payload = {
                        "name": new_name.strip(),
                        "owner_name": new_owner.strip() or None,
                        "phone": new_phone.strip() or None,
                        "address": new_addr.strip() or None,
                        "default_language": new_lang,
                    }
                    user_id = auth_user.get("id") if auth_user else None
                    with st.spinner("Registering store in Supabase..."):
                        ok, created_id, err = create_shop(
                            shop_data=shop_payload,
                            owner_id=user_id,
                            access_token=current_auth_token,
                        )
                    if ok and created_id:
                        st.session_state["selected_shop_id"] = created_id
                        st.success(t("shop.created_success", lang=selected_lang, name=new_name.strip()))
                        st.rerun()
                    else:
                        st.error(err or "Failed to register shop.")
        return

    # Case 2: Multi-shop Admin Portal
    available_shops = fetch_available_shops(
        access_token=current_auth_token,
        is_admin=True,
    )
    if not available_shops and active_shop:
        available_shops = [active_shop]

    users_list = admin_list_users(current_admin_user=auth_user)
    user_map = {str(u.get("id")): u for u in (users_list or [])}

    # Pre-calculate transaction counts for all shops
    shop_entry_counts: Dict[str, int] = {}
    total_tx_count = 0
    for s in available_shops:
        sid = str(s.get("id"))
        cnt = count_shop_ledger_entries(sid, access_token=current_auth_token)
        shop_entry_counts[sid] = cnt
        total_tx_count += cnt

    total_shops_count = len(available_shops)
    total_users_count = len(users_list) if users_list else (1 if auth_user else 0)

    # Summary Metrics Row (4 columns)
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric(
            label=t("admin.total_shops", lang=selected_lang, default="Total Registered Shops"),
            value=total_shops_count,
        )
    with m2:
        st.metric(
            label=t("admin.total_users", lang=selected_lang, default="Total User Accounts"),
            value=total_users_count,
        )
    with m3:
        st.metric(
            label="Store Status",
            value="🟢 Active" if total_shops_count > 0 else "⚪ Awaiting Setup",
            delta=f"{total_shops_count} Stores Online" if total_shops_count > 0 else None,
        )
    with m4:
        st.metric(
            label="Confirmed Transactions",
            value=total_tx_count,
        )

    st.markdown("---")

    # Section 1: Create Shop & User Account (Single consolidated form)
    st.markdown("### ➕ Create Shop & User Account")
    st.caption("Provision a new kirana store and its designated login account in one operation. The shop will be securely bound to this user account.")

    with st.expander("➕ " + t("admin.create_shop_and_user_title", lang=selected_lang, default="Create Shop & User Account"), expanded=True):
        with st.form("admin_create_shop_and_user_form"):
            new_s_name = st.text_input(t("shop.name", lang=selected_lang, default="Shop Name *"), key="admin_new_shop_name")
            new_s_owner = st.text_input(t("shop.owner", lang=selected_lang, default="Owner Name"), key="admin_new_shop_owner")
            new_s_email = st.text_input(t("auth.email", lang=selected_lang, default="Owner Email *"), key="admin_new_shop_email")
            new_s_pwd = st.text_input(t("auth.password", lang=selected_lang, default="Initial Password (min 6 chars) *"), type="password", key="admin_new_shop_pwd")
            new_s_phone = st.text_input(t("shop.phone", lang=selected_lang, default="Phone Number"), key="admin_new_shop_phone")
            new_s_addr = st.text_area(t("shop.address", lang=selected_lang, default="Address / Locality"), key="admin_new_shop_addr")
            supported_langs = ["kn", "en", "hi", "te", "ta"]
            lang_labels = {
                "kn": "ಕನ್ನಡ (Kannada)",
                "en": "English",
                "hi": "हिंदी (Hindi)",
                "te": "తెలుగు (Telugu)",
                "ta": "தமிழ் (Tamil)",
            }
            new_s_lang = st.selectbox(
                t("shop.language", lang=selected_lang, default="Default Language"),
                options=supported_langs,
                format_func=lambda c: lang_labels.get(c, c),
                key="admin_new_shop_lang",
            )
            btn_create_shop_user = st.form_submit_button(
                "🚀 " + t("admin.create_shop_and_user_btn", lang=selected_lang, default="Create Shop & User Account"),
                type="primary",
            )

            if btn_create_shop_user:
                if not new_s_name.strip():
                    st.error("Shop name is required.")
                elif not new_s_email.strip() or "@" not in new_s_email.strip():
                    st.error("A valid owner email address is required.")
                elif not new_s_pwd or len(new_s_pwd) < 6:
                    st.error("Password must be at least 6 characters long.")
                else:
                    shop_payload = {
                        "name": new_s_name.strip(),
                        "owner_name": new_s_owner.strip() or None,
                        "phone": new_s_phone.strip() or None,
                        "address": new_s_addr.strip() or None,
                        "default_language": new_s_lang,
                    }
                    with st.spinner("Provisioning user account and registering store in Supabase..."):
                        ok_prov, prov_sid, prov_uid, err_prov = provision_shop_with_owner(
                            shop_data=shop_payload,
                            owner_email=new_s_email.strip(),
                            owner_password=new_s_pwd,
                            current_admin_user=auth_user,
                            access_token=current_auth_token,
                        )
                    if ok_prov and prov_sid:
                        st.session_state["selected_shop_id"] = prov_sid
                        st.success(f"✅ Successfully provisioned shop '{new_s_name.strip()}' for owner '{new_s_email.strip()}'!")
                        st.rerun()
                    else:
                        st.error(err_prov or "Failed to provision shop and user account.")

    st.markdown("---")

    # Section 2: Registered Stores Directory Table
    st.markdown(f"### 📋 Registered Stores Directory ({total_shops_count})")
    st.caption("All registered kirana stores with designated owner accounts, contact information, and transaction counts.")

    if available_shops:
        directory_rows = []
        for s in available_shops:
            sid = str(s.get("id"))
            owner_uid = str(s.get("owner_id") or "")
            owner_user_entry = user_map.get(owner_uid)
            if owner_user_entry:
                owner_email = owner_user_entry.get("email") or "—"
            elif auth_user and str(auth_user.get("id")) == owner_uid:
                owner_email = auth_user.get("email") or "—"
            else:
                owner_email = "—"

            created_str = str(s.get("created_at") or "—")
            if len(created_str) > 19 and "T" in created_str:
                created_str = created_str.replace("T", " ")[:19]

            directory_rows.append({
                "Shop Name": s.get("name") or "Unnamed Store",
                "Owner Name": s.get("owner_name") or "—",
                "Owner Email": owner_email,
                "Phone": s.get("phone") or "—",
                "Language": (s.get("default_language") or "kn").upper(),
                "Confirmed Transactions": shop_entry_counts.get(sid, 0),
                "Status": "🟢 Active",
                "Created At": created_str,
            })
        st.dataframe(directory_rows, use_container_width=True)
    else:
        st.info("ℹ️ " + t("shop.no_shops", lang=selected_lang, default="No shops registered yet."))

    st.markdown("---")

    # Section 3: Store Management Operations (Edit Shop & Delete Shop)
    st.markdown("### ⚙️ Store Management Operations")

    tool_tab_edit, tool_tab_delete = st.tabs([
        "✏️ " + t("admin.edit_shop_title", lang=selected_lang, default="Edit Shop Details"),
        "🗑️ " + t("admin.delete_shop_title", lang=selected_lang, default="Delete Shop"),
    ])

    with tool_tab_edit:
        st.markdown(f"#### ✏️ {t('admin.edit_shop_title', lang=selected_lang, default='Edit Shop Details')}")
        if not available_shops:
            st.info("No shops available to edit.")
        else:
            shop_options_dict = {
                str(s["id"]): f"{s.get('name', 'Shop')} (Owner: {s.get('owner_name') or user_map.get(str(s.get('owner_id') or ''), {}).get('email') or 'Unassigned'})"
                for s in available_shops
            }
            shop_keys = list(shop_options_dict.keys())
            default_edit_idx = shop_keys.index(str(active_shop_id)) if active_shop_id and str(active_shop_id) in shop_keys else 0

            selected_edit_sid = st.selectbox(
                "Select Shop to Edit",
                options=shop_keys,
                index=default_edit_idx,
                format_func=lambda k: shop_options_dict[k],
                key="admin_select_shop_to_edit",
            )
            target_shop = next((s for s in available_shops if str(s["id"]) == selected_edit_sid), available_shops[0])

            with st.form("admin_edit_shop_form"):
                edit_name = st.text_input(
                    t("shop.name", lang=selected_lang, default="Shop Name *"),
                    value=target_shop.get("name") or "",
                )
                edit_owner = st.text_input(
                    t("shop.owner", lang=selected_lang, default="Owner Name"),
                    value=target_shop.get("owner_name") or "",
                )
                edit_phone = st.text_input(
                    t("shop.phone", lang=selected_lang, default="Phone Number"),
                    value=target_shop.get("phone") or "",
                )
                edit_address = st.text_area(
                    t("shop.address", lang=selected_lang, default="Address / Locality"),
                    value=target_shop.get("address") or "",
                )
                supported_langs = ["kn", "en", "hi", "te", "ta"]
                lang_labels = {
                    "kn": "ಕನ್ನಡ (Kannada)",
                    "en": "English",
                    "hi": "हिंदी (Hindi)",
                    "te": "తెలుగు (Telugu)",
                    "ta": "தமிழ் (Tamil)",
                }
                current_lang_val = target_shop.get("default_language") or "kn"
                def_lang_idx = supported_langs.index(current_lang_val) if current_lang_val in supported_langs else 0
                edit_lang = st.selectbox(
                    t("shop.language", lang=selected_lang, default="Default Language"),
                    options=supported_langs,
                    index=def_lang_idx,
                    format_func=lambda c: lang_labels.get(c, c),
                )

                # Optional owner selection from registered users
                owner_options: Dict[str, str] = {"": "— Unassigned / None —"}
                current_owner_id = str(target_shop.get("owner_id") or "")
                for u in (users_list or []):
                    uid_str = str(u.get("id"))
                    u_disp = f"{u.get('email')} ({u.get('name')})" if u.get("name") and u.get("name") != u.get("email") else str(u.get("email"))
                    owner_options[uid_str] = u_disp

                owner_keys = list(owner_options.keys())
                owner_idx = owner_keys.index(current_owner_id) if current_owner_id in owner_keys else 0
                edit_owner_user = st.selectbox(
                    "Shop Owner (User Account)",
                    options=owner_keys,
                    index=owner_idx,
                    format_func=lambda k: owner_options[k],
                    help="Designated login account that owns this shop. Under Row Level Security, that user has sole access to this shop's transactions.",
                )

                btn_save_edit = st.form_submit_button(
                    "💾 " + t("admin.edit_shop_btn", lang=selected_lang, default="Save Changes"),
                    type="primary",
                )
                if btn_save_edit:
                    if not edit_name.strip():
                        st.error("Shop name is required.")
                    else:
                        update_payload = {
                            "name": edit_name.strip(),
                            "owner_name": edit_owner.strip() or None,
                            "phone": edit_phone.strip() or None,
                            "address": edit_address.strip() or None,
                            "default_language": edit_lang,
                            "owner_id": edit_owner_user if edit_owner_user else None,
                        }
                        with st.spinner("Saving changes to Supabase..."):
                            success, err = update_shop(
                                selected_edit_sid,
                                update_payload,
                                access_token=current_auth_token,
                            )
                        if success:
                            st.success(t("admin.edit_shop_success", lang=selected_lang, name=edit_name.strip()))
                            st.rerun()
                        else:
                            st.error(err or "Failed to update shop.")

    with tool_tab_delete:
        st.markdown(f"#### 🗑️ {t('admin.delete_shop_title', lang=selected_lang, default='Delete Shop')}")
        if not available_shops:
            st.info("No shops available to delete.")
        else:
            del_shop_keys = list(shop_options_dict.keys())
            default_del_idx = del_shop_keys.index(str(active_shop_id)) if active_shop_id and str(active_shop_id) in del_shop_keys else 0

            selected_del_sid = st.selectbox(
                "Select Shop to Delete",
                options=del_shop_keys,
                index=default_del_idx,
                format_func=lambda k: shop_options_dict[k],
                key="admin_select_shop_to_delete",
            )
            del_shop = next((s for s in available_shops if str(s["id"]) == selected_del_sid), available_shops[0])
            del_shop_id = str(del_shop["id"])
            del_shop_name = del_shop.get("name") or "Selected Shop"
            del_entry_count = shop_entry_counts.get(del_shop_id, count_shop_ledger_entries(del_shop_id, access_token=current_auth_token))

            st.info(
                "ℹ️ **Account Preservation**: Deleting a shop record will **NEVER** delete user authentication accounts. "
                "User credentials remain completely safe in Supabase Auth."
            )

            if del_entry_count > 0:
                st.warning(
                    f"⚠️ **Historical Records Warning**: Shop '{del_shop_name}' has **{del_entry_count} confirmed ledger transactions**! "
                    "By default, shop deletion is strictly blocked to protect historical financial records. "
                    "To proceed, you must explicitly confirm cascading deletion of all associated transactions."
                )
                confirm_checked = st.checkbox(
                    f"⚠️ I explicitly confirm deleting shop '{del_shop_name}' and permanently cascading all {del_entry_count} financial ledger records.",
                    key=f"cascade_confirm_{del_shop_id}",
                )
            else:
                st.success(f"✅ Shop '{del_shop_name}' has 0 transaction records. It can be deleted safely without data loss.")
                confirm_checked = st.checkbox(
                    t("admin.delete_shop_confirm", lang=selected_lang, name=del_shop_name),
                    key=f"safe_confirm_{del_shop_id}",
                )

            btn_delete = st.button(
                "🗑️ " + t("admin.delete_shop_btn", lang=selected_lang, default="Delete Shop"),
                type="primary",
                key=f"admin_btn_delete_{del_shop_id}",
            )
            if btn_delete:
                if not confirm_checked:
                    st.error("Please check the confirmation box above to verify that you want to delete this shop.")
                else:
                    allow_cascade = (del_entry_count > 0)
                    with st.spinner("Deleting shop record..."):
                        success, err, count = delete_shop(
                            del_shop_id,
                            access_token=current_auth_token,
                            allow_cascade=allow_cascade,
                        )
                    if success:
                        st.success(t("admin.delete_shop_success", lang=selected_lang, name=del_shop_name))
                        if st.session_state.get("selected_shop_id") == del_shop_id:
                            st.session_state["selected_shop_id"] = None
                        st.rerun()
                    else:
                        st.error(err or "Failed to delete shop.")


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

    resolved_id, resolved_name, resolved_shop = resolve_owner_shop(
        auth_user=auth_user,
        access_token=st.session_state.get("auth_token"),
        is_admin=is_admin,
        current_selected_id=st.session_state.get("selected_shop_id"),
    )
    if resolved_id:
        st.session_state.selected_shop_id = resolved_id
        active_shop_id = resolved_id
        active_shop_name = resolved_name
    else:
        active_shop_id = st.session_state.get("selected_shop_id")
        active_shop_name = "Kirana Store"

    if is_admin:
        st.markdown(
            f"`🛡️ Admin Mode: Single-Shop Administration` &nbsp;|&nbsp; "
            f"**AI Engine**: `{config.gemma_model_name}` &nbsp;|&nbsp; "
            f"**Preprocess**: OpenCV CLAHE & Deskew"
        )
    else:
        st.markdown(
            f"`Milestone 5 & 6: Business Insights, Dashboard & Export Active` &nbsp;|&nbsp; "
            f"**AI Engine**: `{config.gemma_model_name}` &nbsp;|&nbsp; "
            f"**Preprocess**: OpenCV CLAHE & Deskew"
        )

    st.markdown("---")

    if is_admin:
        tab_admin, tab_upload, tab_catalog, tab_readiness, tab_i18n, tab_schema = st.tabs([
            "🛡️ " + t("admin.portal_title", lang=selected_lang, default="Admin Portal — Shop & Shopkeeper Management"),
            "📸 " + t("nav.upload", lang=selected_lang, default="Upload & Extract"),
            "📦 " + t("nav.catalog", lang=selected_lang, default="Product Catalog"),
            "⚙️ " + t("nav.settings", lang=selected_lang, default="System Readiness"),
            "🌐 " + t("common.language", lang=selected_lang, default="Languages"),
            "🗄️ Database Schema",
        ])
        with tab_admin:
            render_admin_portal(selected_lang, auth_user)
    else:
        tab_dashboard, tab_upload, tab_catalog, tab_readiness, tab_i18n, tab_schema = st.tabs([
            "📊 " + t("nav.dashboard", lang=selected_lang, default="Dashboard & Insights"),
            "📸 " + t("nav.upload", lang=selected_lang, default="Upload & Extract"),
            "📦 " + t("nav.catalog", lang=selected_lang, default="Product Catalog"),
            "⚙️ " + t("nav.settings", lang=selected_lang, default="System Readiness"),
            "🌐 " + t("common.language", lang=selected_lang, default="Languages"),
            "🗄️ Database Schema",
        ])
        with tab_dashboard:
            render_dashboard(selected_lang, active_shop_id, active_shop_name)

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
                        processed_bytes = proc_result.to_processed_bytes(
                            ext=".jpg", quality=getattr(config, "image_quality", 88)
                        )
                        result = extractor.extract_from_image(
                            image_bytes=processed_bytes,
                            use_demo=use_demo,
                            demo_fixture_name=chosen_fixture or "kannada_ledger_sample.json",
                            preprocessing_time_ms=getattr(proc_result, "preprocessing_time_ms", 0.0),
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
                if ext_result.latency_breakdown or ext_result.total_request_time_ms:
                    prep_ms = ext_result.preprocessing_time_ms or 0.0
                    api_s = (ext_result.api_latency_ms or 0.0) / 1000.0
                    val_ms = ext_result.parsing_validation_time_ms or 0.0
                    total_s = (ext_result.total_request_time_ms or 0.0) / 1000.0
                    size_kb = (ext_result.payload_size_bytes or 0) / 1024.0
                    st.success(
                        f"⚡ **Performance Breakdown:** Total `{total_s:.2f}s` | "
                        f"Gemma API `{api_s:.2f}s` | Preprocessing `{prep_ms:.1f}ms` | "
                        f"Validation `{val_ms:.1f}ms` | Payload `{size_kb:.1f} KB`"
                    )

                st.subheader("📋 " + t("editor.title", lang=selected_lang, default="Transaction Review & Edit Table"))
                st.caption(t("editor.help", lang=selected_lang, default="Edit extracted values directly in the spreadsheet below. Add rows with '+' or delete rows by selecting them."))

                # Ensure edited_rows exists in session state
                if "edited_rows" not in st.session_state or st.session_state.edited_rows is None:
                    st.session_state.edited_rows = [r for r in ext_result.rows]

                # Synchronize pending edits from st.session_state["ledger_table_editor"]
                # BEFORE building df_current so the data editor immediately displays freshly recalculated Paid values
                # without waiting for a subsequent rerun or a change in another row.
                editor_state = st.session_state.get("ledger_table_editor")
                if isinstance(editor_state, dict) and any(
                    editor_state.get(k) for k in ("edited_rows", "added_rows", "deleted_rows")
                ):
                    st.session_state.edited_rows = sync_editor_rows_with_changes(
                        st.session_state.edited_rows,
                        editor_state,
                    )

                # Convert current edited rows to DataFrame
                df_rows = []
                for r in st.session_state.edited_rows:
                    amt = float(r.amount) if r.amount is not None else 0.0
                    status_label, calc_paid, calc_due = calculate_payment_split(
                        total_amount=amt,
                        payment_status=getattr(r, "payment_status", None),
                        due_amount=r.due,
                    )
                    r.payment_status = status_label
                    r.paid = calc_paid
                    r.due = calc_due

                    df_rows.append({
                        "row_id": int(r.row_id),
                        "date": r.date or "",
                        "customer": r.customer or "",
                        "customer_phone": r.customer_phone or "",
                        "item": r.item or "",
                        "quantity": float(r.quantity) if r.quantity is not None else 1.0,
                        "unit": r.unit or "unit",
                        "unit_price": float(r.unit_price) if r.unit_price is not None else 0.0,
                        "amount": amt,
                        "payment": status_label,
                        "due": calc_due,
                        "paid": calc_paid,
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
                        "payment": st.column_config.SelectboxColumn(
                            t("editor.col_payment", lang=selected_lang, default="Payment"),
                            options=[STATUS_FULLY_PAID, STATUS_HAS_DUE],
                            required=True,
                            help=t("editor.help_payment", lang=selected_lang, default="Select '✅ Fully Paid' or '❌ Has Due'"),
                        ),
                        "due": st.column_config.NumberColumn(
                            t("editor.col_due", lang=selected_lang, default="Due (₹)"),
                            min_value=0.0,
                            step=1.0,
                            help=t("editor.help_due", lang=selected_lang, default="Enter due amount. Paid is auto-calculated as Total - Due."),
                        ),
                        "paid": st.column_config.NumberColumn(
                            t("editor.col_paid", lang=selected_lang, default="Paid (₹)"),
                            disabled=True,
                            help=t("editor.help_paid", lang=selected_lang, default="Auto-calculated: Total - Due"),
                        ),
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

                        amt_raw = _clean_float(row_dict.get("amount"))
                        amt_val = amt_raw if (amt_raw is not None and amt_raw >= 0) else 0.0
                        user_payment = _clean_str(row_dict.get("payment"))
                        user_due = _clean_float(row_dict.get("due"))

                        if matched_row:
                            target_row = matched_row
                            target_row.date = _clean_str(row_dict.get("date"))
                            target_row.customer = _clean_str(row_dict.get("customer"))
                            target_row.customer_phone = _clean_str(row_dict.get("customer_phone"))
                            target_row.item = str(row_dict.get("item") or "").strip()
                            target_row.quantity = _clean_float(row_dict.get("quantity"))
                            target_row.unit = _clean_str(row_dict.get("unit")) or "unit"
                            target_row.unit_price = _clean_float(row_dict.get("unit_price"))

                            prev_amt = target_row.amount
                            prev_status = getattr(target_row, "payment_status", None)
                            prev_due = target_row.due

                            status_changed = (user_payment is not None and user_payment != prev_status)
                            due_changed = (user_due is not None and user_due != prev_due)

                            if "fully paid" in str(user_payment or "").lower() or "✅" in str(user_payment or ""):
                                status_label, calc_paid, calc_due = calculate_payment_split(amt_val, STATUS_FULLY_PAID, 0.0)
                            elif "has due" in str(user_payment or "").lower() or "❌" in str(user_payment or ""):
                                if user_due is not None:
                                    due_clean = max(0.0, min(float(user_due), amt_val))
                                    status_label, calc_paid, calc_due = calculate_payment_split(amt_val, STATUS_HAS_DUE, due_clean)
                                elif status_changed:
                                    due_input = prev_due if (prev_due is not None and prev_due > 0) else amt_val
                                    status_label, calc_paid, calc_due = calculate_payment_split(amt_val, STATUS_HAS_DUE, due_input)
                                else:
                                    due_clean = max(0.0, min(float(prev_due or amt_val), amt_val))
                                    status_label, calc_paid, calc_due = calculate_payment_split(amt_val, STATUS_HAS_DUE, due_clean)
                            else:
                                status_label, calc_paid, calc_due = calculate_payment_split(amt_val, user_payment, user_due)

                            target_row.amount = amt_val
                            target_row.payment_status = status_label
                            target_row.paid = calc_paid
                            target_row.due = calc_due
                        else:
                            # Manually added row from '+' button
                            if "fully paid" in str(user_payment or "").lower() or "✅" in str(user_payment or ""):
                                status_label, calc_paid, calc_due = calculate_payment_split(amt_val, STATUS_FULLY_PAID, 0.0)
                            elif "has due" in str(user_payment or "").lower() or "❌" in str(user_payment or ""):
                                due_clean = max(0.0, min(float(user_due or amt_val), amt_val)) if user_due is not None else amt_val
                                status_label, calc_paid, calc_due = calculate_payment_split(amt_val, STATUS_HAS_DUE, due_clean)
                            else:
                                status_label, calc_paid, calc_due = calculate_payment_split(amt_val, user_payment, user_due)
                            target_row = ExtractedLedgerRow(
                                row_id=row_id_val,
                                date=_clean_str(row_dict.get("date")),
                                customer=_clean_str(row_dict.get("customer")),
                                customer_phone=_clean_str(row_dict.get("customer_phone")),
                                item=str(row_dict.get("item") or "").strip(),
                                quantity=_clean_float(row_dict.get("quantity")),
                                unit=_clean_str(row_dict.get("unit")) or "unit",
                                unit_price=_clean_float(row_dict.get("unit_price")),
                                amount=amt_val,
                                payment_status=status_label,
                                paid=calc_paid,
                                due=calc_due,
                            )

                        # Revalidate row preserving untouched uncertainty flags
                        revalidated = revalidate_row(target_row)
                        synced_rows.append(revalidated)

                    st.session_state.edited_rows = synced_rows

                    # Interactive Quick Payment Adjuster for Touch & Convenience
                    if st.session_state.edited_rows:
                        with st.expander(
                            "⚡ " + t("editor.quick_payment_title", lang=selected_lang, default="Quick Payment & Dues Adjustment"),
                            expanded=False,
                        ):
                            row_options = {
                                r.row_id: f"#{r.row_id}: {r.item or 'Unknown'} — Total: ₹{float(r.amount or 0.0):.2f} (Current: {getattr(r, 'payment_status', STATUS_FULLY_PAID)})"
                                for r in st.session_state.edited_rows
                            }
                            selected_row_id = st.selectbox(
                                t("editor.quick_payment_select_row", lang=selected_lang, default="Select Transaction Row"),
                                options=list(row_options.keys()),
                                format_func=lambda rid: row_options.get(rid, f"Row #{rid}"),
                                key="quick_payment_row_select",
                            )
                            target_sel_row = next((r for r in st.session_state.edited_rows if r.row_id == selected_row_id), None)
                            if target_sel_row:
                                row_tot = float(target_sel_row.amount or 0.0)
                                curr_status = getattr(target_sel_row, "payment_status", None) or (STATUS_HAS_DUE if (target_sel_row.due and target_sel_row.due > 0) else STATUS_FULLY_PAID)
                                status_index = 0 if "fully" in curr_status.lower() or "✅" in curr_status else 1

                                qp_col1, qp_col2 = st.columns([2, 3])
                                with qp_col1:
                                    chosen_status = st.radio(
                                        t("editor.quick_payment_mode", lang=selected_lang, default="Payment Status"),
                                        options=[STATUS_FULLY_PAID, STATUS_HAS_DUE],
                                        index=status_index,
                                        key=f"qp_radio_{target_sel_row.row_id}",
                                    )

                                with qp_col2:
                                    if chosen_status == STATUS_FULLY_PAID:
                                        st.success(f"✅ **Fully Paid**: Paid = **₹{row_tot:.2f}**, Due = **₹0.00**")
                                        qp_due_input = 0.0
                                    else:
                                        curr_due_val = float(target_sel_row.due) if (target_sel_row.due is not None and target_sel_row.due > 0) else row_tot
                                        qp_due_input = st.number_input(
                                            t("editor.due_amount_label", lang=selected_lang, default="Due Amount (₹)"),
                                            min_value=0.0,
                                            max_value=max(0.0, row_tot),
                                            value=min(max(0.0, curr_due_val), max(0.0, row_tot)),
                                            step=1.0,
                                            key=f"qp_due_input_{target_sel_row.row_id}",
                                        )
                                        qp_paid_calc = max(0.0, row_tot - qp_due_input)
                                        st.info(f"💡 **Calculated**: Paid = **₹{qp_paid_calc:.2f}** | Due = **₹{qp_due_input:.2f}**")

                                if st.button(t("editor.quick_apply_btn", lang=selected_lang, default="Apply Payment Changes"), key=f"qp_apply_btn_{target_sel_row.row_id}"):
                                    update_row_payment(
                                        target_sel_row,
                                        payment_status=chosen_status,
                                        due_amount=qp_due_input,
                                    )
                                    st.rerun()

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

                # Display persistent save confirmation if a fresh save just occurred
                if st.session_state.get("last_save_status"):
                    saved_info = st.session_state.last_save_status
                    st.success(
                        t(
                            "editor.save_success",
                            lang=selected_lang,
                            count=saved_info.get("count", 0),
                            batch_id=saved_info.get("batch_id", ""),
                        )
                    )
                    st.info(
                        "💡 "
                        + t(
                            "dashboard.saved_view_hint",
                            lang=selected_lang,
                            default="Confirmed records saved to Supabase! Switch to the 'Dashboard & Insights' tab to explore your freshly updated metrics, trends, and customer balances.",
                        )
                    )
                    st.balloons()
                    st.session_state.last_save_status = None

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
                            st.session_state.last_save_status = {
                                "count": saved_count,
                                "batch_id": computed_batch_id,
                            }
                            invalidate_dashboard_cache()
                            st.session_state.force_duplicate_save = False
                            if hasattr(st, "rerun"):
                                try:
                                    st.rerun()
                                except Exception:
                                    pass
                            else:
                                st.success(
                                    t(
                                        "editor.save_success",
                                        lang=selected_lang,
                                        count=saved_count,
                                        batch_id=computed_batch_id,
                                    )
                                )
                                st.balloons()
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
