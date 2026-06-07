"""
slm/router.py
─────────────
SLM dispatch layer — sits between the engine and the two SLMs.

Responsibilities
────────────────
1. Detect whether a COBOL source file contains EXEC CICS blocks and/or
   VSAM/EBCDIC/COMP-3 data structures that require SLM pre-processing.
2. Extract those specialised blocks from the source.
3. Route each block to the correct SLM (CicsTranslator or VsamDecoder).
4. Stitch the SLM-generated Python back into the source as inline comments
   and code stubs that the main RAG+GPT-4o pipeline can then refine.
5. Return the enriched source + a dict of pre-translated segments so the
   engine can assemble the final module.

This design means:
  - The main GPT-4o engine never sees raw EXEC CICS blocks (which it handles poorly).
  - Sensitive copybook layouts are handled by a local SLM (no external API call).
  - The validation loop is unchanged — the stitched output still goes through
    pytest and the 4-decimal-place equivalence check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slm.cics.translator import CicsTranslator
    from slm.vsam.decoder    import VsamDecoder

# ── Detection patterns ─────────────────────────────────────────────────────

_CICS_BLOCK_RE = re.compile(
    r"EXEC\s+CICS\b.*?END-EXEC",
    re.IGNORECASE | re.DOTALL,
)

_COMP3_RE = re.compile(
    r"\bCOMP-3\b|\bPACKED-DECIMAL\b",
    re.IGNORECASE,
)

_COMP_RE = re.compile(
    r"\bCOMP\b|\bCOMP-4\b|\bBINARY\b",
    re.IGNORECASE,
)

_EBCDIC_HINT_RE = re.compile(
    r"EBCDIC|VSAM|KSDS|ESDS|RRDS|FD\s+\w+.*?RECORDING\s+MODE",
    re.IGNORECASE | re.DOTALL,
)

_COPYBOOK_RECORD_RE = re.compile(
    r"^[ \t]*01\s+\w+.*?(?=^[ \t]*01\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)


# ── Result types ───────────────────────────────────────────────────────────

@dataclass
class SlmSegment:
    """One SLM-translated segment extracted from the COBOL source."""
    slm_type:    str   # "cics" or "vsam"
    original:    str   # the original COBOL text
    translated:  str   # the SLM-generated Python


@dataclass
class RouterResult:
    """Output of SlmRouter.process()."""
    enriched_source:  str                         # COBOL with SLM stubs injected
    segments:         list[SlmSegment] = field(default_factory=list)
    has_cics:         bool = False
    has_vsam:         bool = False
    cics_preamble:    str  = ""  # DFHRESP constants + helper classes
    vsam_helpers:     str  = ""  # shared COMP-3/EBCDIC utility functions


# ── Router ─────────────────────────────────────────────────────────────────

class SlmRouter:
    """
    Detects specialised COBOL constructs and pre-translates them with
    the appropriate SLM before the main RAG+GPT-4o pipeline runs.

    Parameters
    ----------
    cics_slm : CicsTranslator | None
        Pre-loaded CICS SLM. If None and CICS blocks are detected, one is
        created on demand (triggers lazy model load).
    vsam_slm : VsamDecoder | None
        Pre-loaded VSAM SLM. If None and VSAM patterns are detected, one is
        created on demand.
    auto_train : bool
        If True, automatically train an SLM if no adapter exists.
        Default False — requires explicit training for production use.
    """

    def __init__(
        self,
        cics_slm: "CicsTranslator | None" = None,
        vsam_slm: "VsamDecoder | None"    = None,
        auto_train: bool = False,
    ) -> None:
        self._cics_slm   = cics_slm
        self._vsam_slm   = vsam_slm
        self._auto_train = auto_train

    # ── Public API ─────────────────────────────────────────────────────────

    def needs_slm(self, cobol_source: str) -> dict[str, bool]:
        """
        Quickly scan the source and report which SLMs are needed.
        Zero cost — no model loading.
        """
        return {
            "cics": bool(_CICS_BLOCK_RE.search(cobol_source)),
            "vsam": (
                bool(_COMP3_RE.search(cobol_source))
                or bool(_EBCDIC_HINT_RE.search(cobol_source))
            ),
        }

    def process(self, cobol_source: str) -> RouterResult:
        """
        Pre-translate all specialised blocks in the COBOL source.

        Steps
        ─────
        1. Detect CICS blocks and VSAM/COMP-3 patterns.
        2. For each CICS block: call CicsTranslator.translate().
        3. For each VSAM copybook record with COMP-3/EBCDIC: call VsamDecoder.translate().
        4. Replace original blocks in the source with Python stub comments.
        5. Prepend necessary preamble (DFHRESP constants, COMP-3 helpers).
        """
        result = RouterResult(enriched_source=cobol_source)
        needs  = self.needs_slm(cobol_source)

        if needs["cics"]:
            result.has_cics = True
            self._process_cics(cobol_source, result)

        if needs["vsam"]:
            result.has_vsam = True
            self._process_vsam(cobol_source, result)

        return result

    # ── CICS processing ────────────────────────────────────────────────────

    def _process_cics(self, source: str, result: RouterResult) -> None:
        slm = self._get_cics_slm()

        # Translate DFHRESP constants + helper classes (always needed with CICS)
        result.cics_preamble = slm.translate(
            "Define the CICS DFHRESP constants needed for response code checking."
        )
        result.cics_preamble += "\n\n" + slm.translate(
            "Define the CicsContext, CicsAbend, XctlTransfer and CicsReturn helper classes."
        )

        enriched = result.enriched_source
        for match in _CICS_BLOCK_RE.finditer(source):
            original_block = match.group(0)
            translated     = slm.translate(original_block)

            seg = SlmSegment(
                slm_type="cics",
                original=original_block,
                translated=translated,
            )
            result.segments.append(seg)

            # Replace the EXEC CICS block with a stub that GPT-4o can see
            stub = (
                f"# [SLM-CICS-TRANSLATED]\n"
                f"# Original: {original_block.splitlines()[0].strip()}...\n"
                f"{translated}\n"
                f"# [/SLM-CICS-TRANSLATED]"
            )
            enriched = enriched.replace(original_block, stub, 1)

        result.enriched_source = enriched

    # ── VSAM processing ────────────────────────────────────────────────────

    def _process_vsam(self, source: str, result: RouterResult) -> None:
        slm = self._get_vsam_slm()

        # Find all 01-level records that have COMP-3 or COMP fields
        for record_match in _COPYBOOK_RECORD_RE.finditer(source):
            record_text = record_match.group(0)
            if not (_COMP3_RE.search(record_text) or _COMP_RE.search(record_text)):
                continue

            prompt = (
                f"Decode the following COBOL copybook record layout into a Python dataclass "
                f"with from_bytes() and to_bytes() methods:\n\n{record_text}"
            )
            translated = slm.translate(prompt)

            seg = SlmSegment(
                slm_type="vsam",
                original=record_text,
                translated=translated,
            )
            result.segments.append(seg)

        # Also generate shared utility functions if COMP-3 is present
        if _COMP3_RE.search(source):
            result.vsam_helpers = slm.translate(
                "Decode a COMP-3 (packed decimal) field. "
                "Field definition: PIC S9(7)V99 COMP-3. Storage: 5 bytes packed decimal."
            )

    # ── SLM lazy loaders ───────────────────────────────────────────────────

    def _get_cics_slm(self) -> "CicsTranslator":
        if self._cics_slm is None:
            from slm.cics.translator import CicsTranslator
            self._cics_slm = CicsTranslator()
            if not self._cics_slm.is_trained():
                if self._auto_train:
                    print("[Router] CICS SLM not trained — training now…")
                    self._cics_slm.train()
                else:
                    raise RuntimeError(
                        "CICS SLM adapter not found. "
                        "Run: python slm/cics/train.py  (or set auto_train=True)"
                    )
        return self._cics_slm

    def _get_vsam_slm(self) -> "VsamDecoder":
        if self._vsam_slm is None:
            from slm.vsam.decoder import VsamDecoder
            self._vsam_slm = VsamDecoder()
            if not self._vsam_slm.is_trained():
                if self._auto_train:
                    print("[Router] VSAM SLM not trained — training now…")
                    self._vsam_slm.train()
                else:
                    raise RuntimeError(
                        "VSAM SLM adapter not found. "
                        "Run: python slm/vsam/train.py  (or set auto_train=True)"
                    )
        return self._vsam_slm
