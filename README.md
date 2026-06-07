# COBOL → Python Modernizer

**A production-grade AI migration engine for legacy financial systems.**

Translates COBOL source code to validated, production-ready Python using a RAG-enhanced LLM pipeline with fine-tuned Small Language Models — and proves the translation is mathematically correct to 4 decimal places before returning any output.

**Live demo:** [cobol-modernizer.onrender.com](https://cobol-modernizer.onrender.com)  
**Stack:** Python · GPT-4o · ChromaDB · LoRA · HuggingFace · Streamlit · GnuCOBOL

---

## The Problem

COBOL processes an estimated $3 trillion in daily financial transactions globally. Migrating this code to modern languages is one of the most critical challenges facing major banks — but AI-assisted migration has a fundamental trust problem: how do you prove the Python gives exactly the same answer as the COBOL?

This project solves that problem with a closed-loop validation system that doesn't just translate code — it *proves* the translation is correct.

---

## Architecture

```
COBOL source
     │
     ├── [SLM Router] ── EXEC CICS blocks? ──► CicsTranslator (LoRA fine-tuned)
     │                   COMP-3/VSAM fields? ► VsamDecoder   (LoRA fine-tuned)
     │
     ├── [GnuCOBOL] ── compile + run ──► ground-truth output
     │
     ├── [ChromaDB] ── cosine similarity search over 27 COBOL knowledge chunks
     │                 keyword pre-filter + semantic search
     │                 ──► top-k documentation chunks
     │
     ├── [GPT-4o] ── context-injected prompt (RAG chunks + SLM stubs)
     │               ──► python_code + pytest_code (JSON)
     │
     ├── [pytest] ── structural validation
     │               fail ──► error log ──► GPT-4o retry
     │
     └── [4dp comparator] ── ∀ numeric token: |cobol_val - python_val| ≤ 0.0001
                             fail ──► precise diff ──► GPT-4o retry
                             pass ──► ✅ verified translated_module.py
```

---

## Key Technical Features

### RAG Pipeline
A ChromaDB vector store indexes 27 hand-authored documentation chunks covering the complete COBOL language — PIC clauses, COMPUTE semantics, PERFORM loops, OCCURS arrays, REDEFINES, COPY books, intrinsic functions, file I/O, date handling, and banking-specific calculation patterns. Retrieval is two-stage: a zero-cost keyword pre-filter promotes exact construct matches, followed by semantic cosine-similarity search using OpenAI `text-embedding-3-small`. The most relevant chunks are injected into every LLM call, giving GPT-4o the specific syntax rules for the exact constructs in the source being translated.

### Fine-Tuned SLMs
Two LoRA-adapted Small Language Models handle constructs that general-purpose LLMs handle poorly due to limited training data:

**SLM-1 — CICS Translator** (`Qwen2.5-0.5B-Instruct` + LoRA)  
Translates `EXEC CICS ... END-EXEC` blocks to Python. Trained on 35 synthetic examples covering file I/O, program control, TS/TD queues, terminal I/O, interval control, and syncpoint commands. Maps CICS semantics to SQLAlchemy, Python exceptions, and standard library equivalents.

**SLM-2 — VSAM Decoder** (`Qwen2.5-0.5B-Instruct` + LoRA)  
Translates mainframe binary record layouts to Python dataclasses. Trained on 15 examples covering EBCDIC decoding (`cp037`), COMP-3 packed decimal (nibble extraction + sign handling), COMP/COMP-4 binary fields (`struct.unpack` big-endian), OCCURS arrays, and REDEFINES overlays. The SLMs run locally, keeping sensitive copybook layouts off external APIs — critical for bank security requirements.

### 4-Decimal-Place Validation Loop
The equivalence checker extracts every numeric token from both outputs as `decimal.Decimal`, asserts token counts match, then verifies `abs(cobol_val - python_val) ≤ 0.0001` per pair. It also checks non-numeric labels to catch transpositions. Failures produce precise diffs (`Token 1: COBOL=16885.24  Python=16885.2300  diff=0.0100`) that are fed back to GPT-4o as structured feedback for the next retry. This directly addresses the legal precision requirements of financial systems.

### Self-Correcting Retry Loop
On any failure — pytest error, syntax error, or equivalence mismatch — the full error log is injected into the next LLM prompt under a `Self-Correction Contract`: the model must address the exact error and not repeat it. The loop runs up to `max_retries` times (default 5) before raising `TranslationError`.

---

## Project Structure

```
cobol-modernizer/
├── engine.py                    # Core RAG + SLM + validation loop
├── app.py                       # Streamlit dashboard
├── cli.py                       # Headless CLI for batch/CI use
├── hf_push.py                   # Push trained SLM adapters to HuggingFace Hub
├── requirements.txt
├── render.yaml                  # Render deployment config
│
├── rag/
│   ├── cobol_docs.py            # 27 COBOL knowledge chunks
│   └── store.py                 # ChromaDB vector store + two-stage retrieval
│
├── slm/
│   ├── router.py                # Detects and dispatches CICS/VSAM blocks
│   ├── shared/base.py           # LoRA training loop + HuggingFace Hub push/pull
│   ├── cics/
│   │   ├── data.py              # 35 EXEC CICS → Python training pairs
│   │   ├── translator.py        # CicsTranslator (SlmBase subclass)
│   │   └── train.py             # Standalone training script
│   └── vsam/
│       ├── data.py              # 15 copybook → Python decoder training pairs
│       ├── decoder.py           # VsamDecoder (SlmBase subclass)
│       └── train.py             # Standalone training script
│
├── cobol_samples/
│   ├── compound_interest.cob    # PIC, COMPUTE, fixed-point arithmetic
│   ├── payroll.cob              # Multi-field calculation, tax deductions
│   ├── loan_amortisation.cob    # Annuity formula, DIVIDE, MULTIPLY
│   ├── customer_cics.cob        # EXEC CICS READ/REWRITE/SYNCPOINT/RETURN
│   └── transaction_vsam.cob     # COMP-3 amounts, COMP counters, VSAM file read
│
└── tests/
    ├── test_engine.py           # 36 tests: engine, RAG, 4dp comparator
    └── test_slm.py              # 38 tests: router, CICS data, VSAM data, integration
```

**74 tests, all passing.**

---

## Running Locally

**Requirements:** Python 3.11+, Git

```bash
git clone https://github.com/Aniket-BPHC/cobol-modernizer.git
cd cobol-modernizer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."
streamlit run app.py
```

**Optional — enable 4-decimal-place equivalence checking:**
```bash
# macOS
brew install gnu-cobol

# Ubuntu/Debian
sudo apt-get install gnucobol

export COBC_AVAILABLE=1
```

**Optional — train and enable SLMs:**
```bash
python slm/cics/train.py    # ~45 min CPU / ~4 min GPU
python slm/vsam/train.py    # ~30 min CPU / ~3 min GPU

# Push to HuggingFace Hub
python hf_push.py --hf-token hf_... --username YOUR_USERNAME

# Set env vars and restart
export HF_CICS_ADAPTER_REPO="YOUR_USERNAME/cobol-cics-adapter"
export HF_VSAM_ADAPTER_REPO="YOUR_USERNAME/cobol-vsam-adapter"
```

**CLI usage:**
```bash
python cli.py cobol_samples/payroll.cob
python cli.py cobol_samples/compound_interest.cob --model gpt-4o-mini --retries 3
cat my_program.cob | python cli.py -
```

**Run the test suite (no API key required):**
```bash
pytest tests/ -v
```

---

## Deployment

The application is deployed on Render with automatic redeploy on every push to `main`. Configuration is defined in `render.yaml`.

For deployment on other platforms, the following environment variables are required:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key for embeddings and translation |
| `HF_TOKEN` | HuggingFace token for downloading SLM adapters |
| `HF_CICS_ADAPTER_REPO` | HuggingFace repo for the CICS LoRA adapter |
| `HF_VSAM_ADAPTER_REPO` | HuggingFace repo for the VSAM LoRA adapter |
| `COBC_AVAILABLE` | Set to `1` if GnuCOBOL is installed on the server |
| `IS_HOSTED` | Set to `1` for hosted deployments |

---

## Why This Matters for Financial Institutions

Three properties make this system suitable as a foundation for production bank migration tooling:

**Verifiable correctness.** Every translation is validated against the original COBOL's actual output. The 4-decimal-place comparator catches floating-point drift (`0.30000000000000004` vs `0.30`) that would be a legal liability in financial calculations.

**Data sovereignty.** The SLMs run locally. Sensitive COBOL source code — which may contain proprietary business logic, customer data schemas, or regulatory calculation methods — never leaves the institution's infrastructure. Only anonymised or synthetic code needs to reach an external API.

**Auditability.** Every translation attempt is logged with stage, status, and the exact feedback fed to the model. The retry history is a complete audit trail of how the final Python was produced, which is a compliance requirement in regulated financial environments.

---

## License

MIT
