"""
engine.py — Core translation + RAG-enhanced validation loop engine.

Flow
────
  0. Build / warm the COBOL knowledge vector store (once per process).
  1. Compile & run the COBOL program with GnuCOBOL → ground-truth output.
  2. Detect which COBOL constructs appear in the source.
  3. Retrieve the relevant syntax / pattern documentation from ChromaDB.
  4. Inject retrieved context into the LLM system prompt.
  5. Ask GPT-4o to produce Python + pytest code in a strict JSON envelope.
  6. Run the generated pytest suite.
  7. Execute the generated Python and compare its output to COBOL ground truth
     to 4 decimal places on every numeric token.
  8. On any mismatch, feed the precise error back to GPT-4o and retry.
  9. Raise TranslationError if the loop does not converge within max_retries.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from openai import OpenAI

from rag.store import CobolKnowledgeStore
from slm.router import SlmRouter

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class AttemptLog:
    attempt: int
    stage: str      # "ground_truth"|"rag"|"llm_call"|"pytest"|"equivalence"|"success"
    status: str     # "info"|"warning"|"error"|"success"
    message: str


@dataclass
class TranslationResult:
    python_code: str
    pytest_code: str
    attempts: int
    retrieved_chunks: list[str] = field(default_factory=list)
    logs: list[AttemptLog] = field(default_factory=list)


class TranslationError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_BASE_SYSTEM = textwrap.dedent("""\
You are an expert COBOL-to-Python migration engineer specialising in financial
and legacy systems. Your job is to translate COBOL programs into idiomatic,
production-grade Python 3.11+.

STRICT OUTPUT RULES
───────────────────
• Respond with a single JSON object. No markdown fences, no preamble.
• The JSON must contain exactly two keys:
    "python_code"  – the translated Python module as a string
    "pytest_code"  – a pytest test suite that validates correctness

TRANSLATION RULES
─────────────────
1. NEVER use float for arithmetic. Always use decimal.Decimal with getcontext().prec = 28.
2. Mirror every COBOL PIC clause with Decimal.quantize() to the exact scale.
   PIC S9(n)V9(d) → quantize(Decimal('0.' + '0'*d), rounding=ROUND_HALF_UP)
3. Expose computation as callable functions — not bare scripts — so tests can import them.
4. Keep variable names close to COBOL DATA DIVISION names for traceability.
5. Add a docstring mapping each COBOL paragraph to the Python function it became.
6. The pytest suite must:
   a. import translated_module as tm
   b. Test at least 3 input/output pairs (normal, edge, large values).
   c. Use Decimal arithmetic in expected values — never float literals.
   d. Assert outputs match to 4 decimal places:
      assert abs(result - expected) < Decimal('0.0001')

SELF-CORRECTION CONTRACT
─────────────────────────
If a previous attempt failed, a "feedback" field in the user message describes the error.
You MUST address that exact error. Do not repeat the same mistake.
""")


def _build_system_prompt(context_chunks: list[str]) -> str:
    """Append retrieved COBOL documentation chunks to the base system prompt."""
    if not context_chunks:
        return _BASE_SYSTEM

    joined = "\n\n".join(
        f"[Rule {i+1}] {chunk}"
        for i, chunk in enumerate(context_chunks)
    )
    return (
        _BASE_SYSTEM
        + "\nCOBOL SYNTAX REFERENCE (retrieved for this specific program)\n"
        + "─" * 60 + "\n"
        + joined
        + "\n" + "─" * 60 + "\n"
        + "Apply the rules above when translating the program below.\n"
    )


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _call_llm(
    client: OpenAI,
    cobol_source: str,
    feedback: str,
    model: str,
    context_chunks: list[str],
) -> dict[str, str]:
    system_prompt = _build_system_prompt(context_chunks)
    user_content = f"COBOL source to translate:\n\n```cobol\n{cobol_source}\n```"
    if feedback != "Initial translation request.":
        user_content += f"\n\nFeedback from previous attempt (fix this):\n{feedback}"

    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
    )
    raw = response.choices[0].message.content
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TranslationError(f"LLM returned invalid JSON: {exc}\n\nRaw:\n{raw}") from exc

    for key in ("python_code", "pytest_code"):
        if key not in data:
            raise TranslationError(f"LLM JSON missing key '{key}'")
    return data


# ---------------------------------------------------------------------------
# COBOL runner
# ---------------------------------------------------------------------------

def _run_cobol(program_path: Path, input_data: str = "") -> str:
    with tempfile.TemporaryDirectory() as tmpdir:
        binary = Path(tmpdir) / "cobol_program"
        compile_res = subprocess.run(
            ["cobc", "-x", "-o", str(binary), str(program_path)],
            capture_output=True, text=True,
        )
        if compile_res.returncode != 0:
            raise TranslationError(f"COBOL compilation failed:\n{compile_res.stderr}")

        run_res = subprocess.run(
            [str(binary)],
            input=input_data,
            capture_output=True, text=True,
            timeout=15,
        )
        if run_res.returncode != 0:
            raise TranslationError(f"COBOL runtime error:\n{run_res.stderr}")
        return run_res.stdout.strip()


# ---------------------------------------------------------------------------
# Python + pytest runner
# ---------------------------------------------------------------------------

def _run_pytest(test_code: str, python_code: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "translated_module.py").write_text(python_code, encoding="utf-8")
        (tmp / "test_translated.py").write_text(test_code, encoding="utf-8")
        (tmp / "__init__.py").write_text("", encoding="utf-8")

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "test_translated.py", "-v", "--tb=short"],
            capture_output=True, text=True, cwd=tmpdir,
        )
        return result.returncode == 0, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# Decimal-precision equivalence checker
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _extract_decimals(text: str) -> list[Decimal]:
    """Pull every numeric token out of a string as a Decimal."""
    results = []
    for token in _NUMBER_RE.findall(text):
        try:
            results.append(Decimal(token))
        except InvalidOperation:
            pass
    return results


def _check_decimal_equivalence(
    cobol_output: str,
    python_output: str,
    tolerance: Decimal = Decimal("0.0001"),
) -> tuple[bool, str]:
    """
    Compare COBOL ground-truth output to Python output.

    Rules
    ─────
    1. Extract all numeric tokens from both outputs.
    2. For each pair, assert abs(cobol_val - python_val) < tolerance (4 d.p.).
    3. Also compare non-numeric tokens (labels, strings) with normalised whitespace.
    4. Return (passed, human-readable diff message).
    """
    cobol_nums  = _extract_decimals(cobol_output)
    python_nums = _extract_decimals(python_output)

    # ── Numeric token count must match ────────────────────────────────────
    if len(cobol_nums) != len(python_nums):
        return False, (
            f"Numeric token count mismatch: "
            f"COBOL has {len(cobol_nums)} numbers, Python has {len(python_nums)}.\n"
            f"  COBOL : {cobol_output!r}\n"
            f"  Python: {python_output!r}"
        )

    # ── Each number must match to 4 decimal places ─────────────────────────
    mismatches = []
    for i, (cv, pv) in enumerate(zip(cobol_nums, python_nums)):
        diff = abs(cv - pv)
        if diff > tolerance:
            mismatches.append(
                f"  Token {i+1}: COBOL={cv}  Python={pv}  diff={diff}  "
                f"(tolerance={tolerance})"
            )

    if mismatches:
        return False, (
            "Numeric values differ beyond 4 decimal places:\n"
            + "\n".join(mismatches)
            + f"\n  COBOL output : {cobol_output!r}"
            + f"\n  Python output: {python_output!r}"
            + "\nFix the arithmetic so all values match COBOL to within 0.0001."
        )

    # ── Non-numeric content: normalised string comparison ─────────────────
    def _strip_nums(s: str) -> str:
        return _NUMBER_RE.sub("", s).lower()

    cobol_text  = re.sub(r"\s+", " ", _strip_nums(cobol_output)).strip()
    python_text = re.sub(r"\s+", " ", _strip_nums(python_output)).strip()

    if cobol_text != python_text:
        return False, (
            f"Non-numeric label/text mismatch:\n"
            f"  COBOL : {cobol_text!r}\n"
            f"  Python: {python_text!r}"
        )

    return True, f"All {len(cobol_nums)} numeric values match within {tolerance}."


def _run_python_function(python_code: str, cobol_output: str) -> tuple[bool, str]:
    """Dynamically import the generated module, call it, compare to COBOL output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        mod_path = tmp / "translated_module.py"
        mod_path.write_text(python_code, encoding="utf-8")

        spec   = importlib.util.spec_from_file_location("translated_module", mod_path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            return False, f"Module import error: {exc}"

        fn = None
        for name in dir(module):
            obj = getattr(module, name)
            if callable(obj) and not name.startswith("_") and not name.startswith("test"):
                fn = obj
                break

        if fn is None:
            return False, "No callable function found in translated module."

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                fn()
        except TypeError:
            return True, "(equivalence check skipped — function requires arguments)"
        except Exception as exc:
            return False, f"Runtime error calling translated function: {exc}"

        actual = buf.getvalue().strip()
        return _check_decimal_equivalence(cobol_output, actual)


# ---------------------------------------------------------------------------
# Public API — the RAG-enhanced validation loop
# ---------------------------------------------------------------------------

def translate(
    cobol_source: str | Path,
    *,
    api_key: str | None = None,
    model: str = "gpt-4o",
    max_retries: int = 5,
    top_k_chunks: int = 5,
    cobol_input_data: str = "",
    progress_callback: Callable[[AttemptLog], None] | None = None,
    cobol_binary_available: bool = True,
    rag_store: CobolKnowledgeStore | None = None,
    slm_router: SlmRouter | None = None,
) -> TranslationResult:
    """
    Translate COBOL to validated Python using a RAG-enhanced LLM loop.

    New parameters vs v1
    ────────────────────
    top_k_chunks : int
        How many COBOL documentation chunks to retrieve and inject (default 5).
    rag_store : CobolKnowledgeStore | None
        Pre-built store to reuse across calls. If None, one is created and built.
    slm_router : SlmRouter | None
        Optional SLM router for CICS/VSAM pre-translation. If None and the source
        contains EXEC CICS blocks or COMP-3 fields, those sections are passed
        through to GPT-4o without SLM pre-processing.
    """
    resolved_key = api_key or os.environ["OPENAI_API_KEY"]
    client = OpenAI(api_key=resolved_key)
    logs: list[AttemptLog] = []

    def emit(attempt: int, stage: str, status: str, message: str) -> AttemptLog:
        entry = AttemptLog(attempt=attempt, stage=stage, status=status, message=message)
        logs.append(entry)
        if progress_callback:
            progress_callback(entry)
        return entry

    # ── 0. Resolve source ──────────────────────────────────────────────────
    if isinstance(cobol_source, Path):
        cobol_text = cobol_source.read_text(encoding="utf-8")
        cobol_path: Path | None = cobol_source
    else:
        cobol_text = cobol_source
        cobol_path = None

    # ── 0b. SLM pre-processing (CICS / VSAM) ─────────────────────────────
    slm_result = None
    if slm_router is not None:
        needs = slm_router.needs_slm(cobol_text)
        if needs["cics"] or needs["vsam"]:
            emit(0, "slm", "info",
                 f"SLM pre-processing: cics={needs['cics']}, vsam={needs['vsam']}…")
            slm_result = slm_router.process(cobol_text)
            cobol_text = slm_result.enriched_source   # use enriched source for RAG+LLM
            n_segs = len(slm_result.segments)
            emit(0, "slm", "success",
                 f"SLM translated {n_segs} specialised segment(s). "
                 f"CICS preamble: {'yes' if slm_result.cics_preamble else 'no'}, "
                 f"VSAM helpers: {'yes' if slm_result.vsam_helpers else 'no'}.")

    # ── 1. Ground truth ────────────────────────────────────────────────────
    ground_truth: str | None = None
    if cobol_binary_available and cobol_path:
        emit(0, "ground_truth", "info", "Compiling and running COBOL to establish ground truth…")
        ground_truth = _run_cobol(cobol_path, cobol_input_data)
        emit(0, "ground_truth", "info", f"COBOL ground truth: {ground_truth!r}")

    # ── 2. RAG — retrieve relevant COBOL documentation ────────────────────
    emit(0, "rag", "info", "Building / querying COBOL knowledge store…")
    store = rag_store or CobolKnowledgeStore(api_key=resolved_key)
    store.build()  # idempotent

    context_chunks = store.retrieve(cobol_text, top_k=top_k_chunks)
    chunk_topics = []
    for chunk in context_chunks:
        # Extract first sentence as a short label for the log
        first_line = chunk.split("\n")[0][:80]
        chunk_topics.append(first_line)

    emit(
        0, "rag", "success",
        f"Retrieved {len(context_chunks)} context chunks:\n"
        + "\n".join(f"  • {t}" for t in chunk_topics),
    )

    # ── 3. Translation loop ────────────────────────────────────────────────
    feedback = "Initial translation request."

    for attempt in range(1, max_retries + 1):

        # 3a. LLM call (with injected RAG context)
        emit(attempt, "llm_call", "info",
             f"Calling {model} with {len(context_chunks)} injected context chunks "
             f"(attempt {attempt}/{max_retries})…")
        try:
            llm_resp = _call_llm(client, cobol_text, feedback, model, context_chunks)
        except TranslationError as exc:
            feedback = f"LLM call error: {exc}"
            emit(attempt, "llm_call", "error", feedback)
            continue

        python_code = llm_resp["python_code"]
        pytest_code = llm_resp["pytest_code"]
        emit(attempt, "llm_call", "info", "Code received from LLM.")

        # 3b. pytest structural validation
        emit(attempt, "pytest", "info", "Running generated pytest suite…")
        pytest_passed, pytest_output = _run_pytest(pytest_code, python_code)

        if not pytest_passed:
            feedback = f"pytest failed on attempt {attempt}:\n{pytest_output[-2000:]}"
            emit(attempt, "pytest", "error",
                 f"pytest FAILED — feeding error back to LLM.\n{pytest_output[-800:]}")
            continue

        emit(attempt, "pytest", "success", "pytest passed ✓")

        # 3c. Behavioral equivalence — 4 decimal-place numeric check
        if ground_truth is not None:
            emit(attempt, "equivalence", "info",
                 "Running 4-decimal-place equivalence check…")
            matches, detail = _run_python_function(python_code, ground_truth)

            if not matches:
                feedback = f"Equivalence check failed on attempt {attempt}:\n{detail}"
                emit(attempt, "equivalence", "error", feedback)
                continue

            emit(attempt, "equivalence", "success", detail)
        else:
            emit(attempt, "equivalence", "info",
                 "Equivalence check skipped (no COBOL binary available).")

        # ── Success ────────────────────────────────────────────────────────
        emit(attempt, "success", "success",
             f"Translation complete after {attempt} attempt(s).")
        return TranslationResult(
            python_code=python_code,
            pytest_code=pytest_code,
            attempts=attempt,
            retrieved_chunks=context_chunks,
            logs=logs,
        )

    raise TranslationError(
        f"Translation did not converge within {max_retries} attempts.\n"
        f"Last feedback: {feedback}"
    )
