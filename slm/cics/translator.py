"""
slm/cics/translator.py
──────────────────────
SLM-1: CICS-to-Python translator.

Fine-tunes a small causal LM (default: Qwen2.5-0.5B-Instruct) on ~35 synthetic
EXEC CICS → Python translation pairs using LoRA.

The model learns:
  • Every major CICS command category (file I/O, program control, TS/TD queues,
    terminal I/O, interval control, syncpoint, storage)
  • DFHRESP constant values and how to check them
  • The CicsContext / CicsAbend / XctlTransfer / CicsReturn helper classes
  • Idiomatic Python patterns that mirror CICS semantics (sqlalchemy for file I/O,
    Python exceptions for ABEND/XCTL/RETURN, etc.)

Usage
─────
    from slm.cics.translator import CicsTranslator

    slm = CicsTranslator()
    if not slm.is_trained():
        slm.train()          # ~30 min CPU, ~3 min GPU

    python_code = slm.translate(cics_block)
"""

from __future__ import annotations

from pathlib import Path

from slm.shared.base import SlmBase
from slm.cics.data import CICS_TRAINING_PAIRS


class CicsTranslator(SlmBase):

    ADAPTER_DIR = Path(__file__).parent / "adapter"

    def system_prompt(self) -> str:
        return (
            "You are an expert IBM CICS-to-Python migration engineer. "
            "Your job is to translate EXEC CICS command blocks into equivalent "
            "idiomatic Python 3.11+ code.\n\n"
            "RULES:\n"
            "1. Each EXEC CICS ... END-EXEC block becomes a Python statement or block.\n"
            "2. Use sqlalchemy (db.session) for all file I/O operations.\n"
            "3. Use DFHRESP_* constants (e.g. DFHRESP_NORMAL, DFHRESP_NOTFND) for response codes.\n"
            "4. EXEC CICS ABEND → raise CicsAbend(abcode='XXXX')\n"
            "5. EXEC CICS XCTL → raise XctlTransfer(program='NAME', commarea=...)\n"
            "6. EXEC CICS RETURN → raise CicsReturn(transid='XXXX', commarea=...)\n"
            "7. EXEC CICS SYNCPOINT → db.session.commit()\n"
            "8. EXEC CICS SYNCPOINT ROLLBACK → db.session.rollback()\n"
            "9. Temporary storage queues → cics_ctx.ts_queues dict.\n"
            "10. Add a comment on the first line: # EXEC CICS <COMMAND> ...\n"
            "Output only the Python code. No explanations, no markdown fences."
        )

    def generate_dataset(self) -> list[dict[str, str]]:
        return CICS_TRAINING_PAIRS
