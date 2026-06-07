"""
cli.py — Command-line interface for the COBOL Modernizer (RAG edition).

Usage:
    python cli.py cobol_samples/compound_interest.cob
    python cli.py cobol_samples/payroll.cob --model gpt-4o-mini --retries 3 --top-k 6
    python cli.py cobol_samples/compound_interest.cob --cobc --output ./output
    cat my_program.cob | python cli.py -
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engine import AttemptLog, TranslationError, translate
from rag.store import CobolKnowledgeStore

STATUS_ICONS = {"info": "  ·", "success": "  ✓", "error": "  ✗", "warning": "  ⚠"}
STAGE_COLOR  = {
    "ground_truth": "\033[36m", "rag": "\033[35m",
    "llm_call": "\033[34m",     "pytest": "\033[33m",
    "equivalence": "\033[34m",  "success": "\033[32m",
}
RESET = "\033[0m"; BOLD = "\033[1m"; RED = "\033[31m"; GREEN = "\033[32m"


def _print_log(entry: AttemptLog) -> None:
    icon  = STATUS_ICONS.get(entry.status, "  ·")
    color = STAGE_COLOR.get(entry.stage, "")
    tag   = f"[attempt {entry.attempt}] " if entry.attempt > 0 else ""
    stage = f"[{entry.stage}]"
    print(f"{color}{icon} {tag}{BOLD}{stage}{RESET}{color} {entry.message}{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cobol-modernizer",
        description="RAG-enhanced COBOL→Python translator with 4-decimal-place validation.",
    )
    parser.add_argument("source", help="Path to .cob/.cbl file, or '-' for stdin.")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=5,
        help="Number of COBOL docs chunks to inject into the prompt (default: 5).")
    parser.add_argument("--output", default="./output")
    parser.add_argument("--cobc", action="store_true",
        help="Enable GnuCOBOL 4-decimal-place behavioral equivalence check.")
    parser.add_argument("--input-data", default="")
    parser.add_argument("--rebuild-rag", action="store_true",
        help="Force-rebuild the ChromaDB vector store even if it's current.")
    args = parser.parse_args()

    if args.source == "-":
        cobol_source: str | Path = sys.stdin.read()
    else:
        p = Path(args.source)
        if not p.exists():
            print(f"{RED}Error: file not found: {p}{RESET}", file=sys.stderr)
            return 1
        cobol_source = p if args.cobc else p.read_text()

    print(f"\n{BOLD}COBOL → Python Modernizer (RAG){RESET}  "
          f"model={args.model}  retries={args.retries}  top_k={args.top_k}\n"
          + "─" * 64)

    key = args.api_key or __import__("os").environ.get("OPENAI_API_KEY", "")
    store = CobolKnowledgeStore(api_key=key)
    if args.rebuild_rag:
        print("  [RAG] Force-rebuilding vector store…")
        store.build(force=True)

    try:
        result = translate(
            cobol_source=cobol_source,
            api_key=key,
            model=args.model,
            max_retries=args.retries,
            top_k_chunks=args.top_k,
            cobol_input_data=args.input_data,
            cobol_binary_available=args.cobc,
            progress_callback=_print_log,
            rag_store=store,
        )
    except TranslationError as exc:
        print(f"\n{RED}{BOLD}Translation failed:{RESET}\n{exc}", file=sys.stderr)
        return 1

    print("─" * 64)
    print(f"\n{GREEN}{BOLD}✓ Done in {result.attempts} attempt(s){RESET}")
    print(f"  RAG chunks used: {len(result.retrieved_chunks)}\n")

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "translated_module.py").write_text(result.python_code, encoding="utf-8")
    (out_dir / "test_translated.py").write_text(result.pytest_code, encoding="utf-8")

    print(f"  Python → {out_dir / 'translated_module.py'}")
    print(f"  Tests  → {out_dir / 'test_translated.py'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
