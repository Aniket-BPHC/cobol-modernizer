"""
slm/vsam/train.py
─────────────────
Standalone training script for SLM-2 (VSAM / mainframe data decoder).

Run:
    python slm/vsam/train.py
    python slm/vsam/train.py --epochs 5
    python slm/vsam/train.py --model Qwen/Qwen2.5-1.5B-Instruct  # larger
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from slm.vsam.decoder import VsamDecoder


def main():
    parser = argparse.ArgumentParser(description="Train the VSAM/mainframe-data SLM.")
    parser.add_argument("--epochs",  type=int,   default=3)
    parser.add_argument("--lr",      type=float, default=3e-4)
    parser.add_argument("--lora-r",  type=int,   default=16)
    parser.add_argument("--batch",   type=int,   default=2)
    parser.add_argument("--model",   type=str,   default=None)
    parser.add_argument("--force",   action="store_true")
    args = parser.parse_args()

    slm = VsamDecoder()
    if args.model:
        slm.BASE_MODEL_ID = args.model

    if slm.is_trained() and not args.force:
        print(f"Adapter already exists at {slm.ADAPTER_DIR}. Use --force to retrain.")
        return

    print(f"Training VSAM SLM on {len(slm.generate_dataset())} examples…")
    slm.train(
        num_epochs=args.epochs,
        learning_rate=args.lr,
        lora_r=args.lora_r,
        batch_size=args.batch,
    )

    # Quick smoke test
    print("\n── Smoke test ────────────────────────────────────")
    test_input = """\
Decode a COMP-3 (packed decimal) field.
Field: PIC S9(7)V99 COMP-3. Storage: 5 bytes."""
    result = slm.translate(test_input)
    print(f"Input:\n{test_input}\n\nOutput:\n{result}")
    print("── Training complete ─────────────────────────────")


if __name__ == "__main__":
    main()
