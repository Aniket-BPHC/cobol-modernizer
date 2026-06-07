"""
tests/test_slm.py
─────────────────
Tests for the SLM layer: router detection, CICS data, VSAM data, base class,
and engine integration. No model downloads required — all SLM inference is mocked.
"""

from __future__ import annotations

import json
import textwrap
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from slm.router import SlmRouter, RouterResult, SlmSegment
from slm.cics.data import CICS_TRAINING_PAIRS
from slm.vsam.data import VSAM_TRAINING_PAIRS


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _mock_slm(translate_return: str = "# mock output\npass") -> MagicMock:
    slm = MagicMock()
    slm.is_trained.return_value = True
    slm.translate.return_value = translate_return
    return slm


CICS_SOURCE = textwrap.dedent("""\
    IDENTIFICATION DIVISION.
    PROGRAM-ID. CUSTREAD.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 WS-CUST-ID   PIC X(8).
    01 WS-RESP      PIC 9(4) COMP.
    PROCEDURE DIVISION.
        EXEC CICS READ
            FILE('CUSTOMER')
            INTO(WS-CUST-REC)
            RIDFLD(WS-CUST-ID)
            RESP(WS-RESP)
        END-EXEC
        STOP RUN.
""")

VSAM_SOURCE = textwrap.dedent("""\
    IDENTIFICATION DIVISION.
    PROGRAM-ID. VSAMREAD.
    DATA DIVISION.
    WORKING-STORAGE SECTION.
    01 TRANSACTION-RECORD.
       05 TXN-ID      PIC X(10).
       05 TXN-AMOUNT  PIC S9(9)V99 COMP-3.
       05 TXN-DATE    PIC 9(8) COMP-3.
    PROCEDURE DIVISION.
        STOP RUN.
""")

CICS_AND_VSAM_SOURCE = CICS_SOURCE.replace(
    "01 WS-RESP      PIC 9(4) COMP.",
    "01 WS-RESP      PIC 9(4) COMP.\n    01 TXN-AMT    PIC S9(9)V99 COMP-3.",
)

PLAIN_SOURCE = textwrap.dedent("""\
    IDENTIFICATION DIVISION.
    PROGRAM-ID. PLAIN.
    PROCEDURE DIVISION.
        COMPUTE WS-RESULT = 100.
        STOP RUN.
""")


# ══════════════════════════════════════════════════════════════════════════════
# SlmRouter — detection
# ══════════════════════════════════════════════════════════════════════════════

class TestSlmRouterDetection:
    def test_detects_cics(self):
        router = SlmRouter()
        needs = router.needs_slm(CICS_SOURCE)
        assert needs["cics"] is True
        assert needs["vsam"] is False

    def test_detects_vsam_comp3(self):
        router = SlmRouter()
        needs = router.needs_slm(VSAM_SOURCE)
        assert needs["cics"] is False
        assert needs["vsam"] is True

    def test_detects_both(self):
        router = SlmRouter()
        needs = router.needs_slm(CICS_AND_VSAM_SOURCE)
        assert needs["cics"] is True
        assert needs["vsam"] is True

    def test_detects_neither_plain(self):
        router = SlmRouter()
        needs = router.needs_slm(PLAIN_SOURCE)
        assert needs["cics"] is False
        assert needs["vsam"] is False

    def test_detects_ebcdic_hint(self):
        src = "FD CUSTOMER-FILE RECORDING MODE F.\n01 CUST-REC PIC X(80)."
        needs = SlmRouter().needs_slm(src)
        assert needs["vsam"] is True

    def test_detects_exec_cics_case_insensitive(self):
        src = "exec cics syncpoint end-exec"
        needs = SlmRouter().needs_slm(src)
        assert needs["cics"] is True


# ══════════════════════════════════════════════════════════════════════════════
# SlmRouter — process() with mocked SLMs
# ══════════════════════════════════════════════════════════════════════════════

class TestSlmRouterProcess:
    def test_process_cics_calls_slm(self):
        cics_mock = _mock_slm("ws_resp = DFHRESP_NORMAL\n# READ translated")
        router = SlmRouter(cics_slm=cics_mock)

        result = router.process(CICS_SOURCE)

        assert result.has_cics is True
        assert cics_mock.translate.called
        # The enriched source should contain the SLM stub marker
        assert "[SLM-CICS-TRANSLATED]" in result.enriched_source
        # Original block is preserved as a comment inside the SLM stub
        # but the raw EXEC CICS verb is no longer a live instruction
        assert result.enriched_source.count("EXEC CICS READ") ==                result.enriched_source.count("# Original: EXEC CICS READ")

    def test_process_vsam_calls_slm(self):
        vsam_mock = _mock_slm("# COMP-3 decoder\ndef decode(): pass")
        router = SlmRouter(vsam_slm=vsam_mock)

        result = router.process(VSAM_SOURCE)

        assert result.has_vsam is True
        assert vsam_mock.translate.called

    def test_process_plain_no_slm_calls(self):
        cics_mock = _mock_slm()
        vsam_mock = _mock_slm()
        router = SlmRouter(cics_slm=cics_mock, vsam_slm=vsam_mock)

        result = router.process(PLAIN_SOURCE)

        assert result.has_cics is False
        assert result.has_vsam is False
        # SLMs should never have been called
        cics_mock.translate.assert_not_called()
        vsam_mock.translate.assert_not_called()

    def test_segments_populated(self):
        cics_mock = _mock_slm("ws_resp = DFHRESP_NORMAL")
        router = SlmRouter(cics_slm=cics_mock)

        result = router.process(CICS_SOURCE)

        # Should have at least the preamble calls + one block segment
        cics_segments = [s for s in result.segments if s.slm_type == "cics"]
        assert len(cics_segments) >= 1
        assert cics_segments[0].slm_type == "cics"
        assert "EXEC CICS READ" in cics_segments[0].original

    def test_cics_preamble_set(self):
        cics_mock = _mock_slm("DFHRESP_NORMAL = 0")
        router = SlmRouter(cics_slm=cics_mock)
        result = router.process(CICS_SOURCE)
        assert result.cics_preamble != ""

    def test_untrained_slm_raises_without_auto_train(self):
        router = SlmRouter()  # no pre-loaded SLMs, auto_train=False
        with pytest.raises(RuntimeError, match="adapter not found"):
            router.process(CICS_SOURCE)

    def test_router_result_dataclass(self):
        r = RouterResult(enriched_source="test", has_cics=True)
        assert r.has_cics is True
        assert r.segments == []
        assert r.cics_preamble == ""


# ══════════════════════════════════════════════════════════════════════════════
# CICS training data validation
# ══════════════════════════════════════════════════════════════════════════════

class TestCicsTrainingData:
    def test_all_pairs_have_required_keys(self):
        for pair in CICS_TRAINING_PAIRS:
            assert "input"  in pair, f"Missing 'input':  {pair}"
            assert "output" in pair, f"Missing 'output': {pair}"

    def test_no_empty_fields(self):
        for pair in CICS_TRAINING_PAIRS:
            assert pair["input"].strip(),  "Empty input in CICS pair"
            assert pair["output"].strip(), "Empty output in CICS pair"

    def test_all_outputs_are_valid_python(self):
        import ast
        for pair in CICS_TRAINING_PAIRS:
            try:
                ast.parse(pair["output"])
            except SyntaxError as e:
                pytest.fail(f"Invalid Python in CICS output:\n{pair['output']}\nError: {e}")

    def test_outputs_use_dfhresp_constants(self):
        """Most CICS outputs should reference DFHRESP_ constants."""
        dfhresp_count = sum(
            1 for p in CICS_TRAINING_PAIRS if "DFHRESP" in p["output"]
        )
        # At least half should use DFHRESP constants
        assert dfhresp_count >= len(CICS_TRAINING_PAIRS) // 2

    def test_file_io_outputs_use_db_session(self):
        """File control outputs should use db.session."""
        file_pairs = [
            p for p in CICS_TRAINING_PAIRS
            if any(k in p["input"] for k in ["READ\n", "WRITE\n", "REWRITE\n", "DELETE\n"])
        ]
        for pair in file_pairs:
            assert "db.session" in pair["output"], (
                f"File I/O CICS output missing db.session:\n{pair['output']}"
            )

    def test_no_float_in_outputs(self):
        """No CICS output should use raw float literals for financial values."""
        import re
        float_re = re.compile(r'\b\d+\.\d+\b')
        for pair in CICS_TRAINING_PAIRS:
            floats = float_re.findall(pair["output"])
            # Allow version numbers and small literals like 1000 — only flag if float
            # appears without Decimal() wrapper
            if floats:
                for f in floats:
                    assert f"Decimal('{f}')" in pair["output"] or \
                           f"Decimal(\"{f}\")" in pair["output"] or \
                           float(f) == int(float(f)), (
                               f"Bare float {f!r} in CICS output — use Decimal:\n{pair['output']}"
                           )

    def test_minimum_training_examples(self):
        assert len(CICS_TRAINING_PAIRS) >= 20, (
            f"Need at least 20 CICS training pairs, got {len(CICS_TRAINING_PAIRS)}"
        )

    def test_covers_key_command_categories(self):
        all_inputs = "\n".join(p["input"] for p in CICS_TRAINING_PAIRS)
        for keyword in ["READ", "WRITE", "LINK", "SYNCPOINT", "WRITEQ TS", "WRITEQ TD"]:
            assert keyword in all_inputs, f"Missing CICS category: {keyword}"


# ══════════════════════════════════════════════════════════════════════════════
# VSAM training data validation
# ══════════════════════════════════════════════════════════════════════════════

class TestVsamTrainingData:
    def test_all_pairs_have_required_keys(self):
        for pair in VSAM_TRAINING_PAIRS:
            assert "input"  in pair
            assert "output" in pair

    def test_all_outputs_are_valid_python(self):
        import ast
        for pair in VSAM_TRAINING_PAIRS:
            try:
                ast.parse(pair["output"])
            except SyntaxError as e:
                pytest.fail(f"Invalid Python in VSAM output:\n{pair['output']}\nError: {e}")

    def test_no_float_for_financial_fields(self):
        """VSAM financial outputs must use Decimal, not float."""
        for pair in VSAM_TRAINING_PAIRS:
            if "V99" in pair["input"] or "V9" in pair["input"]:
                assert "Decimal" in pair["output"], (
                    f"Financial VSAM output missing Decimal:\n{pair['output']}"
                )

    def test_comp3_outputs_decode_sign_nibble(self):
        """COMP-3 decoder outputs must handle the sign nibble (skip test-suite pairs)."""
        comp3_pairs = [
            p for p in VSAM_TRAINING_PAIRS
            if "COMP-3" in p["input"] and "def decode" in p["output"]
        ]
        assert len(comp3_pairs) >= 1, "Expected at least one COMP-3 decoder pair"
        for pair in comp3_pairs:
            assert any(s in pair["output"] for s in ["0xD", "0xC", "sign", "nibble"]), (
                f"COMP-3 output missing sign handling:\n{pair['output']}"
            )

    def test_ebcdic_outputs_use_cp037(self):
        """EBCDIC decoder outputs that define from_bytes() must use cp037."""
        ebcdic_pairs = [
            p for p in VSAM_TRAINING_PAIRS
            if "EBCDIC" in p["input"] and "def from_bytes" in p["output"]
        ]
        assert len(ebcdic_pairs) >= 1, "Expected at least one EBCDIC from_bytes pair"
        for pair in ebcdic_pairs:
            assert "cp037" in pair["output"], (
                f"EBCDIC output missing cp037 codec:\n{pair['output']}"
            )

    def test_from_bytes_asserts_length(self):
        """from_bytes() methods that take a 'raw: bytes' param must assert length."""
        dataclass_pairs = [
            p for p in VSAM_TRAINING_PAIRS
            if "from_bytes" in p["output"] and "def from_bytes" in p["output"]
        ]
        assert len(dataclass_pairs) >= 1, "Expected at least one from_bytes() pair"
        for pair in dataclass_pairs:
            assert "assert" in pair["output"] and "len(raw)" in pair["output"], (
                f"from_bytes() missing length assertion:\n{pair['output']}"
            )

    def test_minimum_training_examples(self):
        assert len(VSAM_TRAINING_PAIRS) >= 10

    def test_covers_key_data_types(self):
        all_inputs = "\n".join(p["input"] for p in VSAM_TRAINING_PAIRS)
        for concept in ["COMP-3", "EBCDIC", "COMP", "VSAM", "OCCURS"]:
            assert concept in all_inputs, f"Missing VSAM concept: {concept}"


# ══════════════════════════════════════════════════════════════════════════════
# Engine integration — slm_router parameter
# ══════════════════════════════════════════════════════════════════════════════

class TestEngineSlmIntegration:
    """Verify engine.translate() accepts and uses the slm_router parameter."""

    @patch("engine.OpenAI")
    def test_engine_accepts_slm_router(self, MockOpenAI):
        from engine import translate, TranslationResult
        import json, textwrap

        good_python = textwrap.dedent("""\
            from decimal import Decimal, getcontext, ROUND_HALF_UP
            getcontext().prec = 28
            def calculate():
                return Decimal('100.00')
            if __name__ == '__main__':
                print(f'Result: {calculate()}')
        """)
        good_pytest = textwrap.dedent("""\
            from decimal import Decimal
            import translated_module as tm
            def test_basic():
                assert abs(tm.calculate() - Decimal('100.00')) < Decimal('0.0001')
            def test_decimal():
                assert isinstance(tm.calculate(), Decimal)
            def test_positive():
                assert tm.calculate() > 0
        """)

        client = MockOpenAI.return_value
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content=json.dumps({"python_code": good_python, "pytest_code": good_pytest})
            ))]
        )

        from unittest.mock import MagicMock as MM
        from rag.store import CobolKnowledgeStore
        mock_store = MM(spec=CobolKnowledgeStore)
        mock_store.build.return_value = None
        mock_store.retrieve.return_value = ["Decimal precision rule."]

        cics_mock = _mock_slm()
        router = SlmRouter(cics_slm=cics_mock)

        result = translate(
            cobol_source=PLAIN_SOURCE,  # no CICS/VSAM, router should be no-op
            api_key="sk-test",
            cobol_binary_available=False,
            rag_store=mock_store,
            slm_router=router,
        )
        assert result.python_code == good_python
        # No CICS/VSAM in plain source — SLM should not have been invoked
        cics_mock.translate.assert_not_called()

    @patch("engine.OpenAI")
    def test_engine_slm_stage_in_logs_when_cics_present(self, MockOpenAI):
        from engine import translate
        import json, textwrap

        good_python = textwrap.dedent("""\
            from decimal import Decimal, getcontext
            getcontext().prec = 28
            def calculate():
                return Decimal('0.00')
            if __name__ == '__main__':
                print(f'Result: {calculate()}')
        """)
        good_pytest = textwrap.dedent("""\
            from decimal import Decimal
            import translated_module as tm
            def test_basic():
                assert abs(tm.calculate() - Decimal('0.00')) < Decimal('0.0001')
            def test_decimal():
                assert isinstance(tm.calculate(), Decimal)
            def test_positive():
                assert tm.calculate() >= 0
        """)

        client = MockOpenAI.return_value
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content=json.dumps({"python_code": good_python, "pytest_code": good_pytest})
            ))]
        )

        from unittest.mock import MagicMock as MM
        from rag.store import CobolKnowledgeStore
        mock_store = MM(spec=CobolKnowledgeStore)
        mock_store.build.return_value = None
        mock_store.retrieve.return_value = ["CICS rule."]

        cics_mock = _mock_slm("ws_resp = DFHRESP_NORMAL")
        router = SlmRouter(cics_slm=cics_mock)

        result = translate(
            cobol_source=CICS_SOURCE,
            api_key="sk-test",
            cobol_binary_available=False,
            rag_store=mock_store,
            slm_router=router,
        )

        # "slm" stage should appear in logs
        slm_logs = [e for e in result.logs if e.stage == "slm"]
        assert len(slm_logs) >= 1
        assert any(e.status == "success" for e in slm_logs)


# ══════════════════════════════════════════════════════════════════════════════
# SlmBase — unit tests (no model loading)
# ══════════════════════════════════════════════════════════════════════════════

class TestSlmBase:
    def test_is_trained_false_when_no_adapter(self, tmp_path):
        from slm.cics.translator import CicsTranslator
        slm = CicsTranslator()
        slm.ADAPTER_DIR = tmp_path / "no_adapter"
        assert slm.is_trained() is False

    def test_is_trained_true_when_adapter_exists(self, tmp_path):
        from slm.cics.translator import CicsTranslator
        adapter_dir = tmp_path / "adapter"
        adapter_dir.mkdir()
        (adapter_dir / "adapter_config.json").write_text("{}")
        slm = CicsTranslator()
        slm.ADAPTER_DIR = adapter_dir
        assert slm.is_trained() is True

    def test_translate_raises_if_not_trained(self, tmp_path):
        from slm.vsam.decoder import VsamDecoder
        slm = VsamDecoder()
        slm.ADAPTER_DIR = tmp_path / "no_adapter"
        with pytest.raises(RuntimeError, match="No trained adapter"):
            slm.translate("some input")

    def test_cics_system_prompt_contains_rules(self):
        from slm.cics.translator import CicsTranslator
        prompt = CicsTranslator().system_prompt()
        assert "EXEC CICS" in prompt
        assert "db.session" in prompt
        assert "DFHRESP" in prompt
        assert "CicsAbend" in prompt

    def test_vsam_system_prompt_contains_rules(self):
        from slm.vsam.decoder import VsamDecoder
        prompt = VsamDecoder().system_prompt()
        assert "cp037" in prompt
        assert "COMP-3" in prompt
        assert "struct.unpack" in prompt
        assert "Decimal" in prompt

    def test_cics_dataset_non_empty(self):
        from slm.cics.translator import CicsTranslator
        pairs = CicsTranslator().generate_dataset()
        assert len(pairs) > 0

    def test_vsam_dataset_non_empty(self):
        from slm.vsam.decoder import VsamDecoder
        pairs = VsamDecoder().generate_dataset()
        assert len(pairs) > 0
