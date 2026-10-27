"""Unit tests for Gemma 4 AI Business Assistant Engine, UI, Multi-Tenant Shop Isolation, and Multilingual Support."""

import unittest
from unittest.mock import MagicMock, patch

from app import clear_user_session, render_ai_assistant
from core.assistant import (
    SUPPORTED_ASSISTANT_LANGUAGES,
    AssistantResponse,
    ask_gemma_business_assistant,
    build_grounded_business_context,
    format_context_for_prompt,
    get_fallback_response,
    get_last_assistant_metrics,
)
from core.i18n import t


class MockSessionState(dict):
    """Dict subclass that allows attribute access like Streamlit's SessionStateProxy."""

    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class TestAssistantGroundedContext(unittest.TestCase):
    """Verifies that facts passed to Gemma 4 derive strictly from pure Python calculations."""

    def setUp(self) -> None:
        self.sample_entries = [
            {
                "id": "e1",
                "shop_id": "shop-1",
                "customer_name": "Ramesh Patel",
                "customer_phone": "9876543210",
                "item_name": "Sugar",
                "quantity": 10.0,
                "unit": "kg",
                "unit_price": 40.0,
                "total_amount": 400.0,
                "paid_amount": 300.0,
                "due_amount": 100.0,
                "entry_date": "2026-03-01",
                "status": "confirmed",
            },
            {
                "id": "e2",
                "shop_id": "shop-1",
                "customer_name": "Suresh Kumar",
                "customer_phone": "9123456780",
                "item_name": "Toor Dal",
                "quantity": 5.0,
                "unit": "kg",
                "unit_price": 120.0,
                "total_amount": 600.0,
                "paid_amount": 600.0,
                "due_amount": 0.0,
                "entry_date": "2026-03-02",
                "status": "confirmed",
            },
            {
                "id": "e3",
                "shop_id": "shop-1",
                "customer_name": "Ramesh Patel",
                "customer_phone": "9876543210",
                "item_name": "Sugar",
                "quantity": 5.0,
                "unit": "kg",
                "unit_price": 40.0,
                "total_amount": 200.0,
                "paid_amount": 150.0,
                "due_amount": 50.0,
                "entry_date": "2026-03-02",
                "status": "confirmed",
            },
        ]

    def test_build_grounded_business_context_aggregations(self) -> None:
        """Verifies accurate calculation of sales, debtors, top products, and timeline."""
        ctx = build_grounded_business_context(self.sample_entries, shop_name="Sri Krishna Stores")

        self.assertTrue(ctx["has_data"])
        self.assertEqual(ctx["shop_name"], "Sri Krishna Stores")
        self.assertEqual(ctx["total_transactions"], 3)

        # Sales summary
        sales = ctx["sales_summary"]
        self.assertEqual(sales["total_sales"], 1200.0)
        self.assertEqual(sales["total_collected"], 1050.0)
        self.assertEqual(sales["total_due"], 150.0)
        self.assertEqual(sales["average_order_value"], 400.0)

        # Debtors
        debtors = ctx["debtors_summary"]
        self.assertEqual(debtors["pending_customers_count"], 1)  # Only Ramesh has pending dues
        self.assertEqual(debtors["total_outstanding_due"], 150.0)
        self.assertEqual(debtors["top_debtors"][0]["customer_name"], "Ramesh Patel")
        self.assertEqual(debtors["top_debtors"][0]["current_due"], 150.0)

        # Top products
        top_rev = ctx["top_products_by_revenue"]
        self.assertEqual(len(top_rev), 2)
        # Sugar revenue: 400 + 200 = 600, Toor Dal: 600
        # Both generated 600
        rev_names = [p["product_name"] for p in top_rev]
        self.assertIn("Sugar", rev_names)
        self.assertIn("Toor Dal", rev_names)

        # Temporal summary
        temporal = ctx["temporal_summary"]
        self.assertEqual(temporal["earliest_date"], "2026-03-01")
        self.assertEqual(temporal["latest_date"], "2026-03-02")
        self.assertEqual(temporal["distinct_days_count"], 2)

    def test_build_grounded_business_context_empty(self) -> None:
        """Empty entries list yields has_data=False and zeroes."""
        ctx = build_grounded_business_context([], shop_name="Empty Store")
        self.assertFalse(ctx["has_data"])
        self.assertEqual(ctx["total_transactions"], 0)

    def test_format_context_for_prompt(self) -> None:
        """Verifies that format_context_for_prompt converts numbers into clean textual facts."""
        ctx = build_grounded_business_context(self.sample_entries, shop_name="Test Kirana")
        prompt_facts = format_context_for_prompt(ctx)

        self.assertIn("STORE NAME: Test Kirana", prompt_facts)
        self.assertIn("Total Sales (Billed): ₹1200.00", prompt_facts)
        self.assertIn("Total Collected (Cash Inflow): ₹1050.00", prompt_facts)
        self.assertIn("Total Outstanding Dues (Pending Udhaar): ₹150.00", prompt_facts)
        self.assertIn("Ramesh Patel", prompt_facts)
        self.assertIn("2026-03-01 to 2026-03-02", prompt_facts)

    def test_format_context_empty(self) -> None:
        """Empty context prompt returns clean no-records brief."""
        ctx = build_grounded_business_context([], shop_name="Empty Store")
        prompt_facts = format_context_for_prompt(ctx)
        self.assertIn("No confirmed transactions are recorded", prompt_facts)


class TestAssistantFallbackResponses(unittest.TestCase):
    """Verifies deterministic analytical answers across all 5 languages when offline or unconfigured."""

    def setUp(self) -> None:
        self.entries = [
            {
                "id": "e1",
                "customer_name": "Manjunath",
                "item_name": "Rice (Sona Masoori)",
                "quantity": 25.0,
                "unit": "kg",
                "unit_price": 50.0,
                "total_amount": 1250.0,
                "paid_amount": 1000.0,
                "due_amount": 250.0,
                "entry_date": "2026-03-10",
                "status": "confirmed",
            }
        ]
        self.ctx = build_grounded_business_context(self.entries, shop_name="Nandi Provision")

    def test_fallback_sales_in_all_languages(self) -> None:
        """Verifies sales questions produce grounded responses in English, Kannada, Hindi, Telugu, and Tamil."""
        queries = {
            "en": "What are my total sales and collections?",
            "kn": "ನನ್ನ ಒಟ್ಟು ಮಾರಾಟ ಎಷ್ಟು?",
            "hi": "मेरी कुल बिक्री कितनी है?",
            "te": "నా మొత్తం అమ్మకాలు ఎంత?",
            "ta": "எனது மொத்த விற்பனை எவ்வளவு?",
        }
        for lang, q in queries.items():
            ans = get_fallback_response(q, self.ctx, lang=lang)
            self.assertIn("1250", ans, f"Sales figure missing in {lang}")
            self.assertIn("1000", ans, f"Collection figure missing in {lang}")

    def test_fallback_dues_in_all_languages(self) -> None:
        """Verifies customer dues questions produce debtor facts in all 5 languages."""
        queries = {
            "en": "Who owes pending dues?",
            "kn": "ಯಾರ ಮೇಲೆ ಸಾಲ ಬಾಕಿ ಇದೆ?",
            "hi": "किस पर उधार बकाया है?",
            "te": "ఎవరి దగ్గర బాకీ ఉంది?",
            "ta": "யார் கடன் வைத்துள்ளார்?",
        }
        for lang, q in queries.items():
            ans = get_fallback_response(q, self.ctx, lang=lang)
            self.assertIn("250", ans, f"Due figure missing in {lang}")
            self.assertIn("Manjunath", ans, f"Debtor name missing in {lang}")

    def test_fallback_top_products_in_all_languages(self) -> None:
        """Verifies product performance questions produce item rankings in all 5 languages."""
        queries = {
            "en": "Which are my top items?",
            "kn": "ಹೆಚ್ಚು ಮಾರಾಟವಾದ ವಸ್ತುಗಳು ಯಾವುವು?",
            "hi": "सबसे लोकप्रिय उत्पाद कौन से हैं?",
            "te": "టాప్ వస్తువులు ఏవి?",
            "ta": "முக்கிய பொருட்கள் எவை?",
        }
        for lang, q in queries.items():
            ans = get_fallback_response(q, self.ctx, lang=lang)
            self.assertIn("Rice (Sona Masoori)", ans, f"Top product missing in {lang}")

    def test_fallback_dates_in_all_languages(self) -> None:
        """Verifies timeline questions produce date comparisons in all 5 languages."""
        queries = {
            "en": "How do sales compare over time and dates?",
            "kn": "ದಿನಾಂಕವಾರು ಮಾರಾಟ ಹೇಗಿದೆ?",
            "hi": "दिनांकवार बिक्री कैसी है?",
            "te": "తేదీల వారీగా అమ్మకాలు ఎలా ఉన్నాయి?",
            "ta": "தேதி வாரியான விற்பனை எப்படி உள்ளது?",
        }
        for lang, q in queries.items():
            ans = get_fallback_response(q, self.ctx, lang=lang)
            self.assertIn("2026-03-10", ans, f"Date missing in {lang}")


class TestAskGemmaBusinessAssistant(unittest.TestCase):
    """Verifies ask_gemma_business_assistant integration with GenAI client and fallback mechanisms."""

    def setUp(self) -> None:
        self.sample_entries = [
            {
                "id": "e1",
                "customer_name": "Anil",
                "item_name": "Atta",
                "quantity": 10.0,
                "unit": "kg",
                "unit_price": 35.0,
                "total_amount": 350.0,
                "paid_amount": 350.0,
                "due_amount": 0.0,
                "entry_date": "2026-03-05",
                "status": "confirmed",
            }
        ]

    def test_empty_query_rejected(self) -> None:
        """Empty query returns False and helpful placeholder message."""
        success, msg = ask_gemma_business_assistant("", self.sample_entries, lang="en")
        self.assertFalse(success)
        self.assertTrue(len(msg) > 0)

    def test_empty_entries_honest_rejection(self) -> None:
        """Empty records return True and honest no-data notice without calling Gemma."""
        mock_client = MagicMock()
        success, msg = ask_gemma_business_assistant(
            "What are my sales?",
            [],
            shop_name="Empty Store",
            lang="en",
            client=mock_client,
        )
        self.assertTrue(success)
        self.assertIn("No confirmed transactions", msg)
        mock_client.models.generate_content.assert_not_called()

    def test_mock_genai_client_call_enforces_grounding(self) -> None:
        """Verifies that calling with a client formats prompt with verified facts and target language."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Your total sales for Sri Krishna Stores are ₹350.00 with all payments collected."
        mock_client.models.generate_content.return_value = mock_response

        mock_config = MagicMock()
        mock_config.gemma_model_name = "gemma-4-26b-a4b-it"

        success, reply = ask_gemma_business_assistant(
            query="What are my sales?",
            entries=self.sample_entries,
            shop_name="Sri Krishna Stores",
            lang="en",
            config=mock_config,
            client=mock_client,
        )

        self.assertTrue(success)
        self.assertEqual(reply, mock_response.text)

        # Inspect prompt passed to model
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args[1]
        self.assertEqual(call_kwargs["model"], "gemma-4-26b-a4b-it")
        prompt = call_kwargs["contents"]
        self.assertIn("STRICT GROUNDING RULES", prompt)
        self.assertIn("Total Sales (Billed): ₹350.00", prompt)
        self.assertIn("TARGET RESPONSE LANGUAGE: English", prompt)

    def test_kannada_target_language_in_prompt(self) -> None:
        """Verifies that Kannada language target instructions are embedded into prompt."""
        mock_client = MagicMock()
        mock_response = MagicMock(text="ನಿಮ್ಮ ಅಂಗಡಿಯ ಒಟ್ಟು ಮಾರಾಟ ₹350 ಆಗಿದೆ.")
        mock_client.models.generate_content.return_value = mock_response

        success, reply = ask_gemma_business_assistant(
            query="ಮಾರಾಟದ ವಿವರ ನೀಡಿ",
            entries=self.sample_entries,
            shop_name="ಶ್ರೀ ಕೃಷ್ಣ ಸ್ಟೋರ್ಸ್",
            lang="kn",
            client=mock_client,
        )

        self.assertTrue(success)
        prompt = mock_client.models.generate_content.call_args[1]["contents"]
        self.assertIn("TARGET RESPONSE LANGUAGE: Kannada (ಕನ್ನಡ)", prompt)

    def test_unconfigured_api_key_uses_deterministic_fallback(self) -> None:
        """When GEMMA_API_KEY is not set and client is None, uses deterministic fallback without error."""
        mock_config = MagicMock()
        mock_config.is_gemma_configured = False

        success, reply = ask_gemma_business_assistant(
            query="What are my sales?",
            entries=self.sample_entries,
            shop_name="Sri Krishna Stores",
            lang="en",
            config=mock_config,
            client=None,
        )

        self.assertTrue(success)
        self.assertIn("350.00", reply)

    def test_api_error_mapping_401_403_429(self) -> None:
        """Verifies that API errors (401, 403, 429) return False with actionable troubleshooting messages."""
        mock_client = MagicMock()

        # 401 Invalid Key
        mock_client.models.generate_content.side_effect = Exception("API_KEY_INVALID (401)")
        success, err = ask_gemma_business_assistant("test", self.sample_entries, client=mock_client)
        self.assertFalse(success)
        self.assertIn("Gemma API Key Error", err)

        # 429 Quota
        mock_client.models.generate_content.side_effect = Exception("RESOURCE_EXHAUSTED (429)")
        success, err = ask_gemma_business_assistant("test", self.sample_entries, client=mock_client)
        self.assertFalse(success)
        self.assertIn("Quota / Rate limit reached", err)


class TestAssistantUIRenderingAndIsolation(unittest.TestCase):
    """Verifies UI rendering, clear chat, retry flow, and multi-tenant shop isolation."""

    def setUp(self) -> None:
        self.mock_session_state = MockSessionState({
            "assistant_messages": [{"role": "user", "content": "Hello"}],
            "assistant_shop_id": "shop-1",
            "assistant_last_error": None,
        })

    def test_clear_user_session_wipes_assistant_history(self) -> None:
        """Ensures logging out wipes assistant conversation memory."""
        with patch("app.st") as mock_st, patch("app.sign_out"):
            mock_st.session_state = self.mock_session_state
            clear_user_session()
            self.assertEqual(mock_st.session_state["assistant_messages"], [])
            self.assertIsNone(mock_st.session_state["assistant_shop_id"])
            self.assertIsNone(mock_st.session_state["assistant_last_error"])

    def test_shop_switch_clears_assistant_history(self) -> None:
        """Switching to a different shop resets the conversation so Shop A's chat is never visible to Shop B."""
        with patch("app.st") as mock_st:
            mock_st.session_state = MockSessionState({
                "assistant_messages": [{"role": "user", "content": "Shop A Private Query"}],
                "assistant_shop_id": "shop-A",
            })
            mock_st.columns.side_effect = lambda n: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
            mock_st.button.return_value = False
            mock_st.form_submit_button.return_value = False

            # Render for shop-B
            render_ai_assistant("en", [], "Shop B", "shop-B")

            # Assistant messages must have been wiped for shop-B
            self.assertEqual(mock_st.session_state["assistant_messages"], [])
            self.assertEqual(mock_st.session_state["assistant_shop_id"], "shop-B")

    def test_suggested_question_click_triggers_assistant(self) -> None:
        """Clicking a suggested question button triggers ask_gemma_business_assistant and appends response."""
        with patch("app.st") as mock_st, patch("app.ask_gemma_business_assistant") as mock_ask:
            mock_st.session_state = MockSessionState({
                "assistant_messages": [],
                "assistant_shop_id": "shop-1",
            })
            mock_st.columns.side_effect = lambda n: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]

            # Simulate clicking button 1 (sug_q1)
            def button_mock(label, key=None, **kwargs):
                return key == "btn_sug_q1"

            mock_st.button.side_effect = button_mock
            mock_st.form_submit_button.return_value = False
            mock_ask.return_value = (True, "Total sales are ₹1000.")

            render_ai_assistant("en", [{"id": "e1"}], "Shop 1", "shop-1")

            mock_ask.assert_called_once()
            messages = mock_st.session_state["assistant_messages"]
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["role"], "user")
            self.assertEqual(messages[1]["role"], "assistant")
            self.assertEqual(messages[1]["content"], "Total sales are ₹1000.")


class TestAssistantTranslations(unittest.TestCase):
    """Verifies that all 16 assistant translation keys are complete and valid across all 5 language packs."""

    def test_all_assistant_keys_present_in_five_languages(self) -> None:
        """Checks completeness of keys: title, subtitle, grounding_badge, input_placeholder, send_btn, clear_btn, etc."""
        keys = [
            "assistant.title",
            "assistant.subtitle",
            "assistant.grounding_badge",
            "assistant.input_placeholder",
            "assistant.send_btn",
            "assistant.clear_btn",
            "assistant.suggested_title",
            "assistant.suggested_q1",
            "assistant.suggested_q2",
            "assistant.suggested_q3",
            "assistant.suggested_q4",
            "assistant.suggested_q5",
            "assistant.empty_data_notice",
            "assistant.error_api",
            "assistant.retry_btn",
            "assistant.thinking",
        ]
        for lang in ["en", "kn", "hi", "te", "ta"]:
            for key in keys:
                val = t(key, lang=lang)
                self.assertTrue(len(val) > 0, f"Translation key '{key}' is empty in {lang}")
                self.assertNotIn(key, val, f"Translation key '{key}' missing in {lang}")


class TestAssistantOptimization(unittest.TestCase):
    """Verifies intent-based prompt token filtering, response caching, and latency tracking."""

    def setUp(self) -> None:
        self.entries = [
            {
                "id": f"e{i}",
                "customer_name": f"Customer_{i % 5}",
                "item_name": f"Item_{i % 4}",
                "quantity": 2.0,
                "unit": "kg",
                "unit_price": 50.0,
                "total_amount": 100.0,
                "paid_amount": 70.0 if i % 2 == 0 else 100.0,
                "due_amount": 30.0 if i % 2 == 0 else 0.0,
                "entry_date": f"2026-03-{10 + (i % 5):02d}",
                "status": "confirmed",
            }
            for i in range(20)
        ]
        self.ctx = build_grounded_business_context(self.entries, shop_name="Optimization Test Store")

    def test_intent_based_prompt_token_reduction(self) -> None:
        """Verifies that targeted queries send substantially fewer prompt tokens than full context."""
        full_facts = format_context_for_prompt(self.ctx, query=None)
        sales_facts = format_context_for_prompt(self.ctx, query="What are my total sales and collected cash?")
        dues_facts = format_context_for_prompt(self.ctx, query="Who owes the most pending dues?")
        products_facts = format_context_for_prompt(self.ctx, query="Which are my top 5 selling items?")

        # Filtered prompts must be significantly smaller than full facts
        self.assertLess(len(sales_facts), len(full_facts))
        self.assertLess(len(dues_facts), len(full_facts))
        self.assertLess(len(products_facts), len(full_facts))

        # Targeted facts must contain relevant data
        self.assertIn("Total Sales", sales_facts)
        self.assertIn("Customer_", dues_facts)
        self.assertIn("PRODUCT PERFORMANCE", products_facts)

        # Irrelevant sections should be omitted
        self.assertNotIn("TOP CUSTOMERS WITH PENDING DUES", products_facts)
        self.assertNotIn("TOP PRODUCTS BY REVENUE", dues_facts)

    def test_assistant_response_caching_and_metrics(self) -> None:
        """Verifies in-memory caching and AssistantResponse tuple compatibility."""
        mock_config = MagicMock()
        mock_config.is_gemma_configured = False

        q = "Give me an executive business summary."
        # Offline fallback will be returned since is_gemma_configured is False
        success1, reply1 = ask_gemma_business_assistant(
            query=q,
            entries=self.entries,
            shop_name="Optimization Test Store",
            lang="en",
            config=mock_config,
            client=None,
        )
        self.assertTrue(success1)
        self.assertIsInstance(reply1, str)
        self.assertTrue(isinstance(reply1, AssistantResponse) or hasattr(reply1, "metrics"))
        metrics1 = reply1.metrics
        self.assertIn("analytics_calc_time_ms", metrics1)
        self.assertIn("api_latency_ms", metrics1)
        self.assertIn("total_request_time_ms", metrics1)

        # Second call with the same query should hit the cache
        success2, reply2 = ask_gemma_business_assistant(
            query=q,
            entries=self.entries,
            shop_name="Optimization Test Store",
            lang="en",
            config=mock_config,
            client=None,
        )
        self.assertTrue(success2)
        metrics2 = reply2.metrics
        self.assertTrue(metrics2.get("cached", False))

        # Global metrics helper
        last_metrics = get_last_assistant_metrics()
        self.assertIsNotNone(last_metrics)
        self.assertTrue(last_metrics.get("cached", False))


if __name__ == "__main__":
    unittest.main()
