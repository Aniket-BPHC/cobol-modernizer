"""
slm/vsam/decoder.py
───────────────────
SLM-2: Mainframe data decoder (VSAM / EBCDIC / COMP-3).

Fine-tunes a small causal LM on synthetic (copybook → Python decoder) pairs.
The model learns to emit production-grade Python that:
  • Decodes EBCDIC text fields (cp037 codec)
  • Unpacks COMP-3 packed decimal with correct sign nibble handling
  • Unpacks COMP/COMP-4 binary fields with big-endian struct.unpack
  • Handles OCCURS arrays and REDEFINES field overlays
  • Reads VSAM KSDS/ESDS/RRDS flat-file exports in streaming fashion

Usage
─────
    from slm.vsam.decoder import VsamDecoder

    slm = VsamDecoder()
    if not slm.is_trained():
        slm.train()

    python_code = slm.translate(copybook_description)
"""

from __future__ import annotations

from pathlib import Path

from slm.shared.base import SlmBase
from slm.vsam.data import VSAM_TRAINING_PAIRS


class VsamDecoder(SlmBase):

    ADAPTER_DIR = Path(__file__).parent / "adapter"

    def system_prompt(self) -> str:
        return (
            "You are an expert IBM mainframe data engineer. "
            "Your job is to translate COBOL copybook field definitions into "
            "Python 3.11+ code that decodes binary mainframe records.\n\n"
            "RULES:\n"
            "1. EBCDIC text fields (PIC X, PIC A): decode with raw.decode('cp037').strip()\n"
            "2. COMP-3 (packed decimal): decode each byte as two BCD nibbles; "
            "   last nibble is sign (0xC=positive, 0xD=negative, 0xF=unsigned).\n"
            "3. COMP / COMP-4 (binary): use struct.unpack with big-endian ('>') format.\n"
            "   PIC 9(1-4) COMP = 2 bytes ('>H' unsigned, '>h' signed).\n"
            "   PIC 9(5-9) COMP = 4 bytes ('>I' unsigned, '>i' signed).\n"
            "   PIC 9(10-18) COMP = 8 bytes ('>Q' unsigned, '>q' signed).\n"
            "4. Zoned decimal (PIC 9, no COMP): each digit is one EBCDIC byte; "
            "   decode as string then convert to int or Decimal.\n"
            "5. OCCURS n TIMES: produce a list of length n decoded from sequential bytes.\n"
            "6. REDEFINES: implement as @property on the dataclass.\n"
            "7. Always use decimal.Decimal (never float) for fields with V (decimal point).\n"
            "8. Always assert the byte length at the start of from_bytes().\n"
            "9. Include encode/to_bytes methods for round-trip capability.\n"
            "10. Output only Python code. No explanations, no markdown fences."
        )

    def generate_dataset(self) -> list[dict[str, str]]:
        return VSAM_TRAINING_PAIRS
