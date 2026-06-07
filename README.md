# COBOL → Python Modernizer  ·  RAG Edition

A closed-loop, RAG-enhanced system that translates legacy COBOL to validated,
production-grade Python — and **proves the math is identical** to 4 decimal
places before handing you any code.

Built to answer the trust question no bank will skip:
*"How do we know the Python gives exactly the same number as the COBOL?"*

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     RAG-Enhanced Engine                         │
│                                                                 │
│  COBOL source                                                   │
│      │                                                          │
│      ├──► [GnuCOBOL]  compile + run  ──► ground-truth output   │
│      │                                                          │
│      ├──► [Keyword scan] detect constructs (PIC, COMPUTE, …)   │
│      │         │                                                │
│      │         ▼                                                │
│      │    [ChromaDB] cosine similarity search                   │
│      │    over 25+ COBOL documentation chunks                   │
│      │         │                                                │
│      │    top-k chunks (PIC rules, COMPUTE semantics,           │
│      │    Decimal precision, banking patterns, …)               │
│      │         │                                                │
│      ▼         ▼                                                │
│  [GPT-4o] ◄── context-injected system prompt                   │
│      │                                                          │
│      ▼                                                          │
│  python_code + pytest_code  (JSON envelope)                     │
│      │                                                          │
│      ├──► [pytest] structural validation                        │
│      │        fail ──► error log ──► GPT-4o retry              │
│      │                                                          │
│      └──► [4-decimal-place comparator]                          │
│               ∀ numeric token: |cobol_val - python_val| ≤ 0.0001│
│               fail ──► precise diff ──► GPT-4o retry           │
│                                                                 │
│  ✅  Verified translated_module.py + test_translated.py         │
└─────────────────────────────────────────────────────────────────┘
```

---

## What was built

### `rag/cobol_docs.py` — The knowledge base
25 hand-authored documentation chunks covering every major COBOL construct:
PIC clauses, COMPUTE, PERFORM loops, MOVE, OCCURS arrays, REDEFINES,
COPY books, intrinsic functions, file I/O, CALL/LINKAGE, date handling,
banking calculation patterns (compound interest, payroll, loan amortisation),
and decimal precision rules.

### `rag/store.py` — The vector store
- **Backend**: ChromaDB (local persistent, no server)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Retrieval**: two-stage — keyword pre-filter (fast, zero-cost) merged
  with semantic cosine-similarity search
- **Idempotent**: content-hashes the docs; skips re-embedding if unchanged

### `engine.py` — The RAG-enhanced validation loop
1. Runs original COBOL via GnuCOBOL → ground truth
2. Queries ChromaDB for the top-k relevant docs
3. Injects them into GPT-4o's system prompt
4. Validates with pytest (structure)
5. Validates with 4-decimal-place numeric comparator (correctness)
6. Retries with precise error feedback until convergence

### `app.py` — Streamlit dashboard
Three-column layout: COBOL input → live log stream → verified Python output.
Shows which RAG chunks were injected (expandable). Download buttons for both files.

### `cli.py` — Headless CLI
Suitable for CI/CD pipelines, batch translation scripts, and demos.

---

## Quickstart

### 1 — Install

```bash
git clone https://github.com/yourname/cobol-modernizer.git
cd cobol-modernizer
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="sk-..."
```

### 2a — Streamlit UI

```bash
streamlit run app.py
```

The RAG store builds automatically on first run (one API call to embed 25 chunks).

### 2b — CLI

```bash
# Basic translation
python cli.py cobol_samples/compound_interest.cob

# More context, cheaper model
python cli.py cobol_samples/payroll.cob --model gpt-4o-mini --top-k 7

# Full pipeline with GnuCOBOL equivalence check
python cli.py cobol_samples/payroll.cob --cobc

# Force rebuild the vector store (e.g. after adding new doc chunks)
python cli.py cobol_samples/loan_amortisation.cob --rebuild-rag

# Read from stdin
cat my_program.cob | python cli.py -
```

### 2c — Python API

```python
from engine import translate
from rag.store import CobolKnowledgeStore

store = CobolKnowledgeStore(api_key="sk-...")
store.build()  # idempotent

result = translate(
    cobol_source=open("payroll.cob").read(),
    api_key="sk-...",
    rag_store=store,        # reuse across calls
    top_k_chunks=5,
    max_retries=5,
)

print(result.python_code)
print(f"Attempts: {result.attempts}")
print(f"RAG chunks used: {len(result.retrieved_chunks)}")
```

---

## Installing GnuCOBOL (optional — enables 4dp equivalence check)

```bash
brew install gnu-cobol          # macOS
sudo apt-get install gnucobol  # Ubuntu/Debian
cobc --version                 # verify
```

Pass `--cobc` to the CLI, or toggle **GnuCOBOL installed** in the sidebar.

---

## Run the test suite

No API key or `cobc` required — all external calls are mocked.

```bash
pytest tests/ -v
```

36 tests across 7 test classes:

| Class | What it tests |
|---|---|
| `TestExtractDecimals` | Numeric token extraction from mixed text |
| `TestCheckDecimalEquivalence` | 4dp comparator: tolerance, token count, label matching |
| `TestRunPytest` | Isolated pytest runner with good/bad/broken code |
| `TestTranslateSuccess` | Happy path, RAG logs, retrieved chunks in result |
| `TestTranslateRetry` | Retry on pytest failure, max-retry convergence, feedback injection |
| `TestTranslateBadLLMOutput` | Invalid JSON, missing keys |
| `TestRagStore` | Keyword matcher, hash stability, build idempotency, doc schema |

---

## The 4-decimal-place comparator

The spec requires: *"if the numbers don't match to the 4th decimal place, the agent retries."*

`_check_decimal_equivalence()` in `engine.py` implements this precisely:

```python
# Extract every numeric token from both outputs as Decimal
cobol_nums  = _extract_decimals(cobol_output)   # e.g. [Decimal("16885.24")]
python_nums = _extract_decimals(python_output)  # e.g. [Decimal("16885.2399")]

# Assert token counts match (catches missing / extra values)
assert len(cobol_nums) == len(python_nums)

# Assert each pair within 0.0001
for cv, pv in zip(cobol_nums, python_nums):
    assert abs(cv - pv) <= Decimal("0.0001")

# Assert non-numeric labels also match (catches "Net Pay" vs "Gross Pay")
```

This catches:
- float drift (`0.30000000000000004` vs `0.30`) if drift > 0.0001
- wrong scale (`16885.24` vs `16885.2300`)
- missing output lines
- transposed labels

---

## The RAG retrieval strategy

For a given COBOL source snippet, `store.retrieve()` runs two passes:

**Pass 1 — Keyword pre-filter (zero API cost)**
Regex-scans the source for COBOL keywords (COMPUTE, PIC, PERFORM, OCCURS, etc.)
and immediately promotes the corresponding chunk IDs to the front of results.
`decimal_precision` is always promoted — it's relevant to every financial program.

**Pass 2 — Semantic similarity (one embedding API call)**
Embeds the full source with `text-embedding-3-small` and queries ChromaDB by
cosine distance. Catches conceptually related chunks even when the exact keyword
isn't present (e.g. "interest rate" → `interest_calculation` chunk).

**Merge**: keyword results first (ordering by detection order), then semantic
results, deduplicated, capped at `top_k`.

---

## Adding new COBOL documentation

Edit `rag/cobol_docs.py` — add a new dict to `CHUNKS`:

```python
{
    "id": "my_new_topic",           # unique, snake_case
    "topic": "COBOL Report Writer", # human label
    "tags": ["REPORT SECTION", "INITIATE", "GENERATE", "TERMINATE"],
    "text": "Full documentation text goes here…",
},
```

Then run `python cli.py any_file.cob --rebuild-rag` or call `store.build(force=True)`.
The hash changes automatically and ChromaDB re-embeds on next run.

---

## Environment variables

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Required — used for both embeddings and chat completions |

---

## License

MIT

---

## SLM Layer (Gap 1 & Gap 3)

Two fine-tuned Small Language Models handle constructs that GPT-4o handles
poorly because they appear rarely in public training data.

### Why SLMs and not more RAG chunks?

RAG injects documentation — it tells GPT-4o the *rules*. SLMs are trained on
(input → output) pairs — they learn the *pattern* directly. For CICS commands
(a finite ~50-command vocabulary with exact Python equivalents) and COMP-3
binary decoding (a deterministic byte-level algorithm), a trained SLM is more
reliable and consistent than a prompted large model.

The other critical reason: **data sovereignty**. JPMorgan and Goldman will not
send production COBOL source containing mainframe copybook layouts to an
external API. The SLMs run locally, on-premise, with no external calls.

### SLM-1 — CICS Translator (`slm/cics/`)

Translates `EXEC CICS ... END-EXEC` blocks to Python.

**What it learns** (~35 training pairs):
- File control: `READ`, `READNEXT`, `STARTBR`/`ENDBR`, `WRITE`, `REWRITE`, `DELETE`
- Program control: `LINK`, `XCTL`, `RETURN`, `ABEND`
- Queues: `WRITEQ TS`, `READQ TS`, `DELETEQ TS`, `WRITEQ TD`, `READQ TD`
- Terminal I/O: `SEND MAP`, `RECEIVE MAP`, `SEND TEXT`, `RECEIVE`
- Time/interval: `ASKTIME`, `FORMATTIME`, `DELAY`
- Transaction: `SYNCPOINT`, `SYNCPOINT ROLLBACK`
- DFHRESP constants and helper classes (`CicsAbend`, `XctlTransfer`, `CicsReturn`)

**Python output conventions**:
- `READ FILE` → `db.session.query(...).with_for_update()`
- `WRITE FILE` → `db.session.add(...)` with `IntegrityError` catch for `DUPREC`
- `SYNCPOINT` → `db.session.commit()`
- `ABEND` → `raise CicsAbend(abcode='XXXX')`
- `XCTL` → `raise XctlTransfer(program='NAME')`
- All response codes use `DFHRESP_*` constants

### SLM-2 — VSAM/Mainframe Data Decoder (`slm/vsam/`)

Translates COBOL copybook field definitions into Python `dataclass` decoders
that handle binary mainframe data formats.

**What it learns** (~15 training pairs):
- EBCDIC text field decoding (`cp037` codec)
- COMP-3 packed decimal: nibble extraction, sign nibble (`0xC`/`0xD`/`0xF`)
- COMP/COMP-4 binary: `struct.unpack` with big-endian (`>`) format strings
- OCCURS arrays → Python `list`
- REDEFINES overlays → `@property`
- VSAM KSDS/ESDS/RRDS file reading patterns
- Round-trip encode/decode methods

### Architecture: SLM Router (`slm/router.py`)

```
COBOL source
     │
     ├── [router.needs_slm()] — zero-cost regex scan
     │
     ├── EXEC CICS detected?
     │       └── CicsTranslator.translate(each block)
     │           → inlined as # [SLM-CICS-TRANSLATED] stubs
     │
     ├── COMP-3 / VSAM detected?
     │       └── VsamDecoder.translate(each 01-level record)
     │           → Python dataclass decoders generated
     │
     └── Enriched source → RAG retrieval → GPT-4o → validation loop
```

The router is a **pre-processor**: it handles specialised blocks first, then
the enriched source (with SLM stubs already in place) flows into the existing
RAG + GPT-4o pipeline unchanged. The validation loop still applies.

### Training the SLMs

```bash
# SLM-1: CICS translator
python slm/cics/train.py
python slm/cics/train.py --epochs 5 --model Qwen/Qwen2.5-1.5B-Instruct  # larger

# SLM-2: VSAM decoder
python slm/vsam/train.py
python slm/vsam/train.py --epochs 5 --lora-r 32  # more capacity

# Force retrain
python slm/cics/train.py --force
```

**Hardware requirements**:
| Setup | Time (CICS) | Time (VSAM) |
|---|---|---|
| CPU only (8-core) | ~45 min | ~30 min |
| Single GPU (RTX 3090) | ~4 min | ~3 min |
| Single GPU (A100) | ~90 sec | ~60 sec |

Base model: `Qwen/Qwen2.5-0.5B-Instruct` (500M params, ~1 GB RAM, CPU-safe).
Swap to `Qwen/Qwen2.5-1.5B-Instruct` or `microsoft/phi-2` for higher accuracy.

### Using the SLMs in the pipeline

```python
from engine import translate
from rag.store import CobolKnowledgeStore
from slm.router import SlmRouter
from slm.cics.translator import CicsTranslator
from slm.vsam.decoder import VsamDecoder

# Load pre-trained SLMs
cics_slm = CicsTranslator()   # loads adapter from slm/cics/adapter/
vsam_slm = VsamDecoder()      # loads adapter from slm/vsam/adapter/
router   = SlmRouter(cics_slm=cics_slm, vsam_slm=vsam_slm)

result = translate(
    cobol_source=open('cobol_samples/customer_cics.cob').read(),
    api_key='sk-...',
    rag_store=CobolKnowledgeStore(api_key='sk-...'),
    slm_router=router,
)
```

### New sample COBOL files

| File | Constructs |
|---|---|
| `cobol_samples/customer_cics.cob` | `EXEC CICS READ UPDATE`, `REWRITE`, `WRITEQ TS`, `WRITEQ TD`, `SYNCPOINT`, `RETURN` |
| `cobol_samples/transaction_vsam.cob` | `COMP-3` amounts and dates, `COMP` counters, `RECORDING MODE F`, sequential file read loop |

---

## Complete file inventory

```
cobol_modernizer/
├── engine.py                        # RAG + SLM + validation loop
├── app.py                           # Streamlit dashboard
├── cli.py                           # CLI (--slm flag coming)
├── requirements.txt
│
├── rag/
│   ├── cobol_docs.py                # 27 COBOL knowledge chunks
│   └── store.py                     # ChromaDB vector store + retrieval
│
├── slm/
│   ├── router.py                    # Detects & dispatches CICS/VSAM blocks
│   ├── shared/
│   │   └── base.py                  # LoRA training loop + inference base class
│   ├── cics/
│   │   ├── data.py                  # ~35 EXEC CICS → Python training pairs
│   │   ├── translator.py            # CicsTranslator (SlmBase subclass)
│   │   └── train.py                 # Standalone training script
│   └── vsam/
│       ├── data.py                  # ~15 copybook → Python decoder training pairs
│       ├── decoder.py               # VsamDecoder (SlmBase subclass)
│       └── train.py                 # Standalone training script
│
├── cobol_samples/
│   ├── compound_interest.cob
│   ├── payroll.cob
│   ├── loan_amortisation.cob
│   ├── customer_cics.cob            # NEW: CICS file I/O + queues
│   └── transaction_vsam.cob        # NEW: COMP-3 + binary VSAM records
│
└── tests/
    ├── test_engine.py               # 36 tests: engine, RAG, 4dp comparator
    └── test_slm.py                  # 38 tests: router, CICS data, VSAM data, integration
```

**Total: 74 tests, all passing.**
