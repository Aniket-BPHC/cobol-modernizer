"""
app.py — Streamlit dashboard (hosting-safe edition).

Hosting changes vs local version
─────────────────────────────────
• No threading / Queue — replaced with a synchronous generator + st.empty()
  so it works on Streamlit Community Cloud and HuggingFace Spaces.
• GnuCOBOL toggle removed — always False on hosted; shown only when COBC_AVAILABLE
  env-var is set (set it on Railway/Render where cobc can be installed).
• SLM toggle — hidden unless HF_CICS_ADAPTER_REPO or HF_VSAM_ADAPTER_REPO
  env-vars are set (set after uploading adapters to HuggingFace Hub).
• API key read from st.secrets first, then env-var, then sidebar input.
"""

from __future__ import annotations

import os

import streamlit as st

from engine import AttemptLog, TranslationError, translate
from rag.store import CobolKnowledgeStore

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="COBOL Modernizer",
    page_icon="🔄",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap');
code, textarea { font-family: 'JetBrains Mono', monospace !important; font-size: 12.5px !important; }
.log-entry { padding: 6px 12px; border-radius: 6px; margin-bottom: 5px;
             font-family: 'JetBrains Mono', monospace; font-size: 12px; line-height: 1.5; }
.log-info    { background:#f0f2f6; border-left:3px solid #aaa; }
.log-success { background:#eaf3de; border-left:3px solid #639922; color:#27500A; }
.log-error   { background:#fff0ed; border-left:3px solid #D85A30; color:#993C1D; }
.log-warning { background:#faeeda; border-left:3px solid #EF9F27; color:#854F0B; }
.verified    { background:#eaf3de; border:1px solid #97C459; border-radius:8px;
               padding:10px 16px; color:#3B6D11; font-weight:600; font-size:14px; margin-bottom:12px; }
.chip { display:inline-block; background:#EEEDFE; color:#3C3489; border-radius:20px;
        padding:2px 10px; font-size:11px; font-weight:500; margin-right:6px; }
.env-badge { display:inline-block; padding:2px 10px; border-radius:20px; font-size:11px;
             font-weight:500; margin-bottom:8px; }
.env-hosted { background:#E1F5EE; color:#085041; }
.env-local  { background:#EEEDFE; color:#26215C; }
</style>
""", unsafe_allow_html=True)

# ── Environment detection ──────────────────────────────────────────────────
COBC_AVAILABLE   = os.environ.get("COBC_AVAILABLE", "").lower() in ("1", "true", "yes")
CICS_ADAPTER_REPO = os.environ.get("HF_CICS_ADAPTER_REPO", "")
VSAM_ADAPTER_REPO = os.environ.get("HF_VSAM_ADAPTER_REPO", "")
SLM_AVAILABLE    = bool(CICS_ADAPTER_REPO or VSAM_ADAPTER_REPO)
IS_HOSTED        = os.environ.get("IS_HOSTED", "").lower() in ("1", "true", "yes") \
                   or "STREAMLIT_SHARING_MODE" in os.environ \
                   or "SPACE_ID" in os.environ   # HuggingFace Spaces

# ── API key resolution: secrets → env → sidebar input ─────────────────────
def _resolve_api_key(sidebar_value: str) -> str:
    if sidebar_value:
        return sidebar_value
    try:
        return st.secrets["OPENAI_API_KEY"]
    except Exception:
        pass
    return os.environ.get("OPENAI_API_KEY", "")

# ── Session state ──────────────────────────────────────────────────────────
for k, v in {
    "logs": [], "python_code": "", "pytest_code": "",
    "chunks": [], "running": False, "done": False,
    "error": "", "attempts": 0,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ Settings")

    env_label = "☁️ Hosted" if IS_HOSTED else "💻 Local"
    env_class = "env-hosted" if IS_HOSTED else "env-local"
    st.markdown(f'<span class="env-badge {env_class}">{env_label}</span>',
                unsafe_allow_html=True)

    # API key — hidden if already set via secrets/env
    _env_key = os.environ.get("OPENAI_API_KEY", "")
    _has_secret = False
    try:
        _has_secret = bool(st.secrets.get("OPENAI_API_KEY"))
    except Exception:
        pass

    if _has_secret or _env_key:
        sidebar_key = ""
        st.success("✓ API key loaded from environment", icon="🔑")
    else:
        sidebar_key = st.text_input("OpenAI API Key", type="password",
            help="Enter your key here, or set OPENAI_API_KEY in environment/secrets.")

    model = st.selectbox("Model", ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"])
    max_retries = st.slider("Max retries", 1, 8, 5)
    top_k = st.slider("RAG context chunks", 2, 8, 5)

    if COBC_AVAILABLE:
        cobol_binary = st.toggle("GnuCOBOL equivalence check",
            help="4-decimal-place behavioral check using cobc.")
    else:
        cobol_binary = False
        if not IS_HOSTED:
            st.caption("💡 Install GnuCOBOL and set COBC_AVAILABLE=1 to enable "
                       "4-decimal-place equivalence checking.")

    if SLM_AVAILABLE:
        use_slm = st.toggle("SLM pre-processing (CICS/VSAM)",
            help="Use fine-tuned SLMs for EXEC CICS and COMP-3 blocks.")
    else:
        use_slm = False
        if not IS_HOSTED:
            st.caption("💡 Train SLMs and set HF_CICS_ADAPTER_REPO / "
                       "HF_VSAM_ADAPTER_REPO to enable SLM pre-processing.")

    st.divider()
    st.caption(
        "**RAG pipeline**: embeds your COBOL, queries a ChromaDB vector store "
        "of COBOL syntax rules, and injects the most relevant chunks into every "
        "GPT-4o call. Validation ensures correctness before returning any code."
    )

# ── Sample programs ────────────────────────────────────────────────────────
SAMPLES = {
    "Compound Interest": """\
IDENTIFICATION DIVISION.
PROGRAM-ID. COMPOUND-INTEREST.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 PRINCIPAL      PIC S9(7)V99 VALUE 10000.00.
01 ANNUAL-RATE    PIC S9(3)V99 VALUE 5.25.
01 YEARS          PIC 9(3)    VALUE 10.
01 COMPOUND-FREQ  PIC 9(3)    VALUE 12.
01 RESULT         PIC S9(12)V99.
PROCEDURE DIVISION.
    COMPUTE RESULT = PRINCIPAL *
        ((1 + ANNUAL-RATE / 100 / COMPOUND-FREQ)
        ** (COMPOUND-FREQ * YEARS)).
    DISPLAY "Maturity Amount: " RESULT.
    STOP RUN.
""",
    "Payroll Deductions": """\
IDENTIFICATION DIVISION.
PROGRAM-ID. PAYROLL.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 HOURS-WORKED    PIC 9(3)V99  VALUE 42.50.
01 HOURLY-RATE     PIC 9(5)V99  VALUE 28.75.
01 TAX-RATE        PIC S9(3)V99 VALUE 22.00.
01 GROSS-PAY       PIC S9(9)V99.
01 TAX-AMOUNT      PIC S9(9)V99.
01 NET-PAY         PIC S9(9)V99.
PROCEDURE DIVISION.
    COMPUTE GROSS-PAY = HOURS-WORKED * HOURLY-RATE.
    COMPUTE TAX-AMOUNT = GROSS-PAY * TAX-RATE / 100.
    COMPUTE NET-PAY = GROSS-PAY - TAX-AMOUNT.
    DISPLAY "Gross Pay  : " GROSS-PAY.
    DISPLAY "Tax Amount : " TAX-AMOUNT.
    DISPLAY "Net Pay    : " NET-PAY.
    STOP RUN.
""",
    "Loan Amortisation": """\
IDENTIFICATION DIVISION.
PROGRAM-ID. LOAN-AMORT.
DATA DIVISION.
WORKING-STORAGE SECTION.
01 LOAN-AMOUNT     PIC S9(9)V99  VALUE 250000.00.
01 ANNUAL-RATE     PIC S9(5)V99  VALUE 6.50.
01 TERM-YEARS      PIC 9(3)      VALUE 30.
01 MONTHLY-RATE    PIC S9(5)V9(8).
01 NUM-PAYMENTS    PIC 9(5).
01 MONTHLY-PMT     PIC S9(9)V99.
PROCEDURE DIVISION.
    DIVIDE ANNUAL-RATE BY 1200 GIVING MONTHLY-RATE.
    MULTIPLY TERM-YEARS BY 12 GIVING NUM-PAYMENTS.
    COMPUTE MONTHLY-PMT =
        LOAN-AMOUNT * MONTHLY-RATE /
        (1 - (1 + MONTHLY-RATE) ** (-NUM-PAYMENTS)).
    DISPLAY "Monthly Payment: " MONTHLY-PMT.
    STOP RUN.
""",
}

# ── Main layout ────────────────────────────────────────────────────────────
st.title("🔄 COBOL → Python Modernizer  ·  RAG + SLM Edition")
st.caption(
    "Paste legacy COBOL. The RAG-enhanced validation loop retrieves relevant "
    "syntax rules from a ChromaDB vector store, injects them into GPT-4o, "
    "validates the output with pytest, and verifies correctness to 4 decimal places."
)

col_in, col_log, col_out = st.columns([1.1, 0.9, 1.0])

with col_in:
    st.subheader("COBOL source")
    sample = st.selectbox("Load sample →", ["(paste your own)"] + list(SAMPLES))
    cobol_input = st.text_area(
        "cobol", value=SAMPLES.get(sample, ""), height=340,
        label_visibility="collapsed", placeholder="Paste COBOL here…",
    )
    run_btn = st.button(
        "▶  Run RAG Validation Loop",
        use_container_width=True, type="primary",
        disabled=st.session_state.running,
    )

# ── Translation (synchronous — hosting-safe) ───────────────────────────────
if run_btn and cobol_input.strip():
    api_key = _resolve_api_key(sidebar_key)
    if not api_key:
        st.error("Enter your OpenAI API key in the sidebar.")
        st.stop()

    for k, v in {"logs": [], "python_code": "", "pytest_code": "",
                 "chunks": [], "done": False, "error": "", "attempts": 0}.items():
        st.session_state[k] = v
    st.session_state.running = True

    # Build shared store once
    store = CobolKnowledgeStore(api_key=api_key)

    # Build SLM router if adapters available
    slm_router = None
    if use_slm and SLM_AVAILABLE:
        try:
            from slm.router import SlmRouter
            from slm.cics.translator import CicsTranslator
            from slm.vsam.decoder import VsamDecoder
            cics_slm = CicsTranslator(hf_repo=CICS_ADAPTER_REPO) if CICS_ADAPTER_REPO else None
            vsam_slm = VsamDecoder(hf_repo=VSAM_ADAPTER_REPO)    if VSAM_ADAPTER_REPO else None
            slm_router = SlmRouter(cics_slm=cics_slm, vsam_slm=vsam_slm)
        except Exception as exc:
            st.warning(f"SLM load failed ({exc}) — continuing without SLM pre-processing.")

    # Live log placeholder in centre column
    log_box = col_log.empty()
    rendered_logs: list[AttemptLog] = []

    def _render_logs(logs: list[AttemptLog]) -> None:
        html = ""
        for e in logs:
            css  = {"info":"log-info","success":"log-success",
                    "error":"log-error","warning":"log-warning"}.get(e.status, "log-info")
            icon = {"info":"ℹ️","success":"✅","error":"❌","warning":"⚠️"}.get(e.status, "·")
            chip = (f'<span class="chip">{e.stage}</span>'
                    if e.attempt > 0
                    else f'<span class="chip" style="background:#E1F5EE;color:#085041">'
                         f'{e.stage}</span>')
            msg  = e.message.replace("\n", "<br>")
            html += f'<div class="log-entry {css}">{chip}{icon} {msg}</div>'
        log_box.markdown(html, unsafe_allow_html=True)

    def _on_log(entry: AttemptLog) -> None:
        rendered_logs.append(entry)
        _render_logs(rendered_logs)

    col_log.subheader("Validation loop log")

    try:
        result = translate(
            cobol_source=cobol_input,
            api_key=api_key,
            model=model,
            max_retries=max_retries,
            top_k_chunks=top_k,
            cobol_binary_available=cobol_binary,
            progress_callback=_on_log,
            rag_store=store,
            slm_router=slm_router,
        )
        st.session_state.python_code = result.python_code
        st.session_state.pytest_code = result.pytest_code
        st.session_state.attempts    = result.attempts
        st.session_state.chunks      = result.retrieved_chunks
        st.session_state.logs        = rendered_logs
    except TranslationError as exc:
        st.session_state.error = str(exc)
        st.session_state.logs  = rendered_logs
    finally:
        st.session_state.running = False
        st.session_state.done    = True
    st.rerun()

# ── Log panel (after run) ──────────────────────────────────────────────────
with col_log:
    if not st.session_state.running:
        st.subheader("Validation loop log")
        if not st.session_state.logs:
            st.info("Logs stream here once the loop starts.")
        else:
            html = ""
            for e in st.session_state.logs:
                css  = {"info":"log-info","success":"log-success",
                        "error":"log-error","warning":"log-warning"}.get(e.status, "log-info")
                icon = {"info":"ℹ️","success":"✅","error":"❌","warning":"⚠️"}.get(e.status,"·")
                chip = (f'<span class="chip">{e.stage}</span>'
                        if e.attempt > 0
                        else f'<span class="chip" style="background:#E1F5EE;color:#085041">'
                             f'{e.stage}</span>')
                msg  = e.message.replace("\n", "<br>")
                html += f'<div class="log-entry {css}">{chip}{icon} {msg}</div>'
            st.markdown(html, unsafe_allow_html=True)

        if st.session_state.error:
            st.error(f"Translation failed:\n\n{st.session_state.error}")

# ── Output panel ───────────────────────────────────────────────────────────
with col_out:
    st.subheader("Generated Python")
    if st.session_state.done and st.session_state.python_code:
        check_label = ("✅ 4-decimal-place equivalence verified"
                       if cobol_binary else "✅ pytest validation passed")
        st.markdown(
            f'<div class="verified">{check_label}'
            f' · {st.session_state.attempts} attempt(s)</div>',
            unsafe_allow_html=True,
        )
        if st.session_state.chunks:
            with st.expander(f"📚 {len(st.session_state.chunks)} RAG chunks injected"):
                for i, chunk in enumerate(st.session_state.chunks, 1):
                    with st.expander(f"Chunk {i}: {chunk.split(chr(10))[0][:90]}"):
                        st.text(chunk)

        tab_py, tab_test = st.tabs(["translated_module.py", "test_translated.py"])
        with tab_py:
            st.code(st.session_state.python_code, language="python")
            st.download_button("⬇ Download translated_module.py",
                data=st.session_state.python_code,
                file_name="translated_module.py", mime="text/x-python",
                use_container_width=True)
        with tab_test:
            st.code(st.session_state.pytest_code, language="python")
            st.download_button("⬇ Download test_translated.py",
                data=st.session_state.pytest_code,
                file_name="test_translated.py", mime="text/x-python",
                use_container_width=True)
    elif not st.session_state.running:
        st.info("Validated Python appears here after the loop completes.")
