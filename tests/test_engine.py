"""
tests/test_engine.py
────────────────────
Unit tests for engine.py and rag/store.py.
All OpenAI and subprocess calls are mocked — no API key or cobc required.
Run with:  pytest tests/ -v
"""

from __future__ import annotations

import json
import textwrap
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from engine import (
    AttemptLog,
    TranslationError,
    _check_decimal_equivalence,
    _extract_decimals,
    _run_pytest,
    translate,
)


# ── Fixtures ───────────────────────────────────────────────────────────────

GOOD_PYTHON = textwrap.dedent("""\
    from decimal import Decimal, getcontext, ROUND_HALF_UP
    getcontext().prec = 28

    def calculate():
        principal = Decimal("10000.00")
        rate = Decimal("5.25") / Decimal("100") / Decimal("12")
        result = principal * (1 + rate) ** (12 * 10)
        return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if __name__ == "__main__":
        print(f"Maturity Amount: {calculate()}")
""")

GOOD_PYTEST = textwrap.dedent("""\
    from decimal import Decimal
    import translated_module as tm

    def test_basic():
        result = tm.calculate()
        assert abs(result - Decimal("16885.24")) < Decimal("0.0001")

    def test_returns_decimal():
        assert isinstance(tm.calculate(), Decimal)

    def test_positive():
        assert tm.calculate() > 0
""")

GOOD_LLM_RESPONSE = json.dumps({
    "python_code": GOOD_PYTHON,
    "pytest_code": GOOD_PYTEST,
})


def _make_openai_response(content: str) -> MagicMock:
    msg = MagicMock(); msg.content = content
    choice = MagicMock(); choice.message = msg
    resp = MagicMock(); resp.choices = [choice]
    return resp


def _mock_rag_store() -> MagicMock:
    """A no-op RAG store that returns two dummy chunks."""
    store = MagicMock()
    store.build.return_value = None
    store.retrieve.return_value = [
        "COMPUTE statement: use Decimal arithmetic.",
        "PIC S9(7)V99 → quantize to Decimal('0.01')",
    ]
    return store


# ── _extract_decimals ──────────────────────────────────────────────────────

class TestExtractDecimals:
    def test_single_integer(self):
        assert _extract_decimals("Value: 42") == [Decimal("42")]

    def test_single_decimal(self):
        assert _extract_decimals("Result: 16885.24") == [Decimal("16885.24")]

    def test_multiple_numbers(self):
        result = _extract_decimals("Gross: 1221.88 Tax: 268.81 Net: 953.07")
        assert result == [Decimal("1221.88"), Decimal("268.81"), Decimal("953.07")]

    def test_negative(self):
        assert _extract_decimals("Loss: -450.75") == [Decimal("-450.75")]

    def test_no_numbers(self):
        assert _extract_decimals("No numbers here") == []

    def test_mixed_text_and_numbers(self):
        result = _extract_decimals("Maturity Amount:  +16885.2400000")
        assert len(result) == 1
        assert result[0] == Decimal("16885.2400000")


# ── _check_decimal_equivalence ─────────────────────────────────────────────

class TestCheckDecimalEquivalence:
    def test_exact_match(self):
        ok, msg = _check_decimal_equivalence(
            "Maturity Amount:  16885.24",
            "Maturity Amount: 16885.24",
        )
        assert ok, msg

    def test_within_tolerance(self):
        ok, msg = _check_decimal_equivalence("16885.24", "16885.2399")
        assert ok, msg

    def test_just_outside_tolerance(self):
        ok, msg = _check_decimal_equivalence("16885.24", "16885.2300")
        assert not ok
        assert "0.01" in msg or "differ" in msg

    def test_float_drift_detected(self):
        # 0.1 + 0.2 in float = 0.30000000000000004
        ok, msg = _check_decimal_equivalence("1234.5678", "1234.4999")
        assert not ok

    def test_token_count_mismatch(self):
        ok, msg = _check_decimal_equivalence(
            "Gross: 100.00 Net: 80.00",
            "Gross: 100.00",
        )
        assert not ok
        assert "count mismatch" in msg.lower()

    def test_multi_line_output(self):
        cobol  = "Gross Pay  :  1221.88\nTax Amount :   268.81\nNet Pay    :   953.07"
        python = "Gross Pay  : 1221.88\nTax Amount : 268.81\nNet Pay    : 953.07"
        ok, msg = _check_decimal_equivalence(cobol, python)
        assert ok, msg

    def test_custom_tolerance(self):
        ok, _ = _check_decimal_equivalence("1.00000", "1.00001",
                                            tolerance=Decimal("0.00001"))
        assert ok
        ok2, _ = _check_decimal_equivalence("1.00000", "1.00002",
                                             tolerance=Decimal("0.00001"))
        assert not ok2

    def test_label_mismatch_caught(self):
        ok, msg = _check_decimal_equivalence(
            "Gross Pay: 100.00",
            "Net Pay: 100.00",
        )
        assert not ok
        assert "label" in msg.lower() or "text" in msg.lower()


# ── _run_pytest ────────────────────────────────────────────────────────────

class TestRunPytest:
    def test_passing_suite(self):
        python = textwrap.dedent("""\
            from decimal import Decimal
            def add(a, b): return Decimal(str(a)) + Decimal(str(b))
        """)
        tests = textwrap.dedent("""\
            from decimal import Decimal
            import translated_module as tm
            def test_add():
                assert tm.add(1, 2) == Decimal("3")
        """)
        passed, out = _run_pytest(tests, python)
        assert passed, out

    def test_failing_suite(self):
        python = "def calculate(): return 999"
        tests  = textwrap.dedent("""\
            import translated_module as tm
            def test_wrong(): assert tm.calculate() == 0
        """)
        passed, _ = _run_pytest(tests, python)
        assert not passed

    def test_syntax_error_in_python(self):
        passed, _ = _run_pytest("import translated_module as tm\ndef test_x(): pass",
                                  "def bad(  # broken")
        assert not passed

    def test_import_error_in_test(self):
        passed, _ = _run_pytest("import nonexistent_xyz\ndef test_x(): pass",
                                  "def calculate(): return 1")
        assert not passed

    def test_decimal_precision_test_catches_float(self):
        """A pytest suite asserting abs(result - expected) < 0.0001 should
        catch float drift but pass Decimal math."""
        python = textwrap.dedent("""\
            from decimal import Decimal, getcontext, ROUND_HALF_UP
            getcontext().prec = 28
            def calculate():
                return (Decimal("0.1") + Decimal("0.2")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP)
        """)
        tests = textwrap.dedent("""\
            from decimal import Decimal
            import translated_module as tm
            def test_no_float_drift():
                result = tm.calculate()
                assert abs(result - Decimal("0.30")) < Decimal("0.0001")
        """)
        passed, out = _run_pytest(tests, python)
        assert passed, out


# ── translate — success path ───────────────────────────────────────────────

class TestTranslateSuccess:
    @patch("engine.OpenAI")
    def test_success_first_attempt(self, MockOpenAI):
        client = MockOpenAI.return_value
        client.chat.completions.create.return_value = _make_openai_response(GOOD_LLM_RESPONSE)

        result = translate(
            cobol_source="IDENTIFICATION DIVISION. PROGRAM-ID. X.",
            api_key="sk-test",
            cobol_binary_available=False,
            rag_store=_mock_rag_store(),
        )
        assert result.python_code == GOOD_PYTHON
        assert result.pytest_code == GOOD_PYTEST
        assert result.attempts == 1
        assert any(e.status == "success" for e in result.logs)

    @patch("engine.OpenAI")
    def test_rag_stage_appears_in_logs(self, MockOpenAI):
        client = MockOpenAI.return_value
        client.chat.completions.create.return_value = _make_openai_response(GOOD_LLM_RESPONSE)

        result = translate(
            cobol_source="COMPUTE X = A * B.",
            api_key="sk-test",
            cobol_binary_available=False,
            rag_store=_mock_rag_store(),
        )
        stages = [e.stage for e in result.logs]
        assert "rag" in stages
        assert "llm_call" in stages
        assert "pytest" in stages

    @patch("engine.OpenAI")
    def test_retrieved_chunks_in_result(self, MockOpenAI):
        client = MockOpenAI.return_value
        client.chat.completions.create.return_value = _make_openai_response(GOOD_LLM_RESPONSE)

        result = translate(
            cobol_source="COMPUTE X = A.",
            api_key="sk-test",
            cobol_binary_available=False,
            rag_store=_mock_rag_store(),
        )
        assert len(result.retrieved_chunks) == 2


# ── translate — retry path ─────────────────────────────────────────────────

class TestTranslateRetry:
    @patch("engine.OpenAI")
    def test_retries_after_pytest_failure(self, MockOpenAI):
        bad_python = "def calculate(): return 'wrong_type'"
        bad_pytest = textwrap.dedent("""\
            from decimal import Decimal
            import translated_module as tm
            def test_fail():
                assert abs(tm.calculate() - Decimal("999")) < Decimal("0.0001")
        """)
        bad_resp  = json.dumps({"python_code": bad_python, "pytest_code": bad_pytest})

        client = MockOpenAI.return_value
        client.chat.completions.create.side_effect = [
            _make_openai_response(bad_resp),
            _make_openai_response(GOOD_LLM_RESPONSE),
        ]

        result = translate(
            cobol_source="IDENTIFICATION DIVISION.",
            api_key="sk-test",
            cobol_binary_available=False,
            max_retries=3,
            rag_store=_mock_rag_store(),
        )
        assert result.attempts == 2
        assert any(e.status == "error" for e in result.logs)

    @patch("engine.OpenAI")
    def test_raises_after_max_retries(self, MockOpenAI):
        bad = json.dumps({
            "python_code": "def calculate(): return 'bad'",
            "pytest_code": textwrap.dedent("""\
                from decimal import Decimal
                import translated_module as tm
                def test_x():
                    assert abs(tm.calculate() - Decimal("0")) < Decimal("0.0001")
            """),
        })
        client = MockOpenAI.return_value
        client.chat.completions.create.return_value = _make_openai_response(bad)

        with pytest.raises(TranslationError, match="did not converge"):
            translate(
                cobol_source="IDENTIFICATION DIVISION.",
                api_key="sk-test",
                cobol_binary_available=False,
                max_retries=2,
                rag_store=_mock_rag_store(),
            )

    @patch("engine.OpenAI")
    def test_feedback_injected_on_retry(self, MockOpenAI):
        """Verify the second LLM call receives feedback about the first failure."""
        bad = json.dumps({
            "python_code": "def calculate(): return 'wrong'",
            "pytest_code": textwrap.dedent("""\
                from decimal import Decimal
                import translated_module as tm
                def test_x():
                    assert abs(tm.calculate() - Decimal("1")) < Decimal("0.0001")
            """),
        })
        client = MockOpenAI.return_value
        client.chat.completions.create.side_effect = [
            _make_openai_response(bad),
            _make_openai_response(GOOD_LLM_RESPONSE),
        ]

        translate(
            cobol_source="COMPUTE X = 1.",
            api_key="sk-test",
            cobol_binary_available=False,
            max_retries=3,
            rag_store=_mock_rag_store(),
        )

        # Second call must include feedback in user message
        second_call_kwargs = client.chat.completions.create.call_args_list[1].kwargs
        user_msg = second_call_kwargs["messages"][-1]["content"]
        assert "feedback" in user_msg.lower() or "failed" in user_msg.lower()


# ── translate — bad LLM output ─────────────────────────────────────────────

class TestTranslateBadLLMOutput:
    @patch("engine.OpenAI")
    def test_invalid_json_raises(self, MockOpenAI):
        broken = MagicMock(); broken.content = "}{not json"
        choice = MagicMock(); choice.message = broken
        resp   = MagicMock(); resp.choices = [choice]
        MockOpenAI.return_value.chat.completions.create.return_value = resp

        with pytest.raises(TranslationError):
            translate("IDENTIFICATION DIVISION.", api_key="sk-test",
                      cobol_binary_available=False, max_retries=1,
                      rag_store=_mock_rag_store())

    @patch("engine.OpenAI")
    def test_missing_key_raises(self, MockOpenAI):
        MockOpenAI.return_value.chat.completions.create.return_value = \
            _make_openai_response(json.dumps({"python_code": "def f(): pass"}))

        with pytest.raises(TranslationError):
            translate("IDENTIFICATION DIVISION.", api_key="sk-test",
                      cobol_binary_available=False, max_retries=1,
                      rag_store=_mock_rag_store())


# ── RAG store (unit — no API calls) ───────────────────────────────────────

class TestRagStore:
    def test_keyword_match_compute(self):
        from rag.store import _keyword_match
        ids = _keyword_match("COMPUTE RESULT = A * B.")
        assert "compute" in ids

    def test_keyword_match_pic(self):
        from rag.store import _keyword_match
        ids = _keyword_match("01 AMOUNT PIC S9(7)V99.")
        assert "pic_clause" in ids

    def test_keyword_match_perform(self):
        from rag.store import _keyword_match
        ids = _keyword_match("PERFORM UNTIL WS-EOF = 'Y'.")
        assert "perform" in ids

    def test_decimal_precision_always_included(self):
        from rag.store import _keyword_match
        ids = _keyword_match("STOP RUN.")
        assert "decimal_precision" in ids

    def test_keyword_match_occurs(self):
        from rag.store import _keyword_match
        ids = _keyword_match("01 TABLE OCCURS 10 TIMES PIC 9(5).")
        assert "occurs" in ids

    def test_chunks_hash_stable(self):
        from rag.store import _chunks_hash
        h1 = _chunks_hash()
        h2 = _chunks_hash()
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex

    def test_store_build_skips_when_hash_matches(self, tmp_path):
        """build() should skip embedding when hash file matches."""
        from rag.store import CobolKnowledgeStore, _chunks_hash

        store = CobolKnowledgeStore.__new__(CobolKnowledgeStore)

        mock_col = MagicMock()
        mock_col.count.return_value = len(__import__("rag.cobol_docs", fromlist=["CHUNKS"]).CHUNKS)
        store._col = mock_col

        mock_oai = MagicMock()
        store._client_oai = mock_oai

        # Write the correct hash so build() thinks it's current
        hash_file = tmp_path / "chunks.hash"
        hash_file.write_text(_chunks_hash())

        with patch("rag.store._hash_file", return_value=hash_file):
            store.build()

        mock_oai.embeddings.create.assert_not_called()

    def test_cobol_docs_all_have_required_keys(self):
        from rag.cobol_docs import CHUNKS
        for chunk in CHUNKS:
            assert "id"    in chunk, f"Missing 'id' in chunk: {chunk}"
            assert "topic" in chunk, f"Missing 'topic' in chunk: {chunk}"
            assert "tags"  in chunk, f"Missing 'tags' in chunk: {chunk}"
            assert "text"  in chunk, f"Missing 'text' in chunk: {chunk}"
            assert isinstance(chunk["tags"], list)
            assert len(chunk["text"]) > 20, f"Chunk '{chunk['id']}' text too short"

    def test_cobol_docs_unique_ids(self):
        from rag.cobol_docs import CHUNKS
        ids = [c["id"] for c in CHUNKS]
        assert len(ids) == len(set(ids)), "Duplicate chunk IDs found"
