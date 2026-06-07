"""
slm/cics/train.py
─────────────────
Standalone training script for SLM-1 (CICS translator).

Run:
    python slm/cics/train.py
    python slm/cics/train.py --epochs 5 --lr 2e-4
    python slm/cics/train.py --model microsoft/phi-2  # larger base model
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from slm.cics.translator import CicsTranslator


def main():
    parser = argparse.ArgumentParser(description="Train the CICS-to-Python SLM.")
    parser.add_argument("--epochs",  type=int,   default=3)
    parser.add_argument("--lr",      type=float, default=3e-4)
    parser.add_argument("--lora-r",  type=int,   default=16)
    parser.add_argument("--batch",   type=int,   default=2)
    parser.add_argument("--model",   type=str,   default=None,
        help="Override base model HuggingFace ID.")
    parser.add_argument("--force",   action="store_true",
        help="Re-train even if adapter already exists.")
    args = parser.parse_args()

    slm = CicsTranslator()
    if args.model:
        slm.BASE_MODEL_ID = args.model

    if slm.is_trained() and not args.force:
        print(f"Adapter already exists at {slm.ADAPTER_DIR}. Use --force to retrain.")
        return

    print(f"Training CICS SLM on {len(slm.generate_dataset())} examples…")
    slm.train(
        num_epochs=args.epochs,
        learning_rate=args.lr,
        lora_r=args.lora_r,
        batch_size=args.batch,
    )

    # Quick smoke test
    print("\n── Smoke test ────────────────────────────────────")
    test_input = """\
EXEC CICS READ
    FILE('CUSTOMER')
    INTO(WS-CUST-REC)
    RIDFLD(WS-CUST-ID)
    RESP(WS-RESP)
END-EXEC"""
    result = slm.translate(test_input)
    print(f"Input:\n{test_input}\n\nOutput:\n{result}")
    print("── Training complete ─────────────────────────────")


if __name__ == "__main__":
    main()
