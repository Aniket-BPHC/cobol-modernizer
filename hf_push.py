"""
hf_push.py — Push trained SLM adapters to HuggingFace Hub.

Run AFTER training both SLMs:
    python slm/cics/train.py
    python slm/vsam/train.py
    python hf_push.py --hf-token hf_xxx --username yourname

This creates two model repos on HuggingFace:
    yourname/cobol-cics-adapter
    yourname/cobol-vsam-adapter

Then set those repo names as env-vars on your hosting platform.
"""

from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Push trained COBOL SLM adapters to HuggingFace Hub."
    )
    parser.add_argument("--hf-token",  required=True,
        help="HuggingFace write token (Settings → Access Tokens → New token → Write).")
    parser.add_argument("--username",  required=True,
        help="Your HuggingFace username.")
    parser.add_argument("--cics-repo", default="cobol-cics-adapter",
        help="Repo name for CICS adapter (default: cobol-cics-adapter).")
    parser.add_argument("--vsam-repo", default="cobol-vsam-adapter",
        help="Repo name for VSAM adapter (default: cobol-vsam-adapter).")
    parser.add_argument("--skip-cics", action="store_true")
    parser.add_argument("--skip-vsam", action="store_true")
    args = parser.parse_args()

    cics_repo_id = f"{args.username}/{args.cics_repo}"
    vsam_repo_id = f"{args.username}/{args.vsam_repo}"

    if not args.skip_cics:
        from slm.cics.translator import CicsTranslator
        cics = CicsTranslator()
        if not cics.is_trained():
            print("ERROR: CICS adapter not found. Run: python slm/cics/train.py")
            sys.exit(1)
        print(f"Pushing CICS adapter to {cics_repo_id}…")
        cics.push_to_hub(cics_repo_id, token=args.hf_token)
        print(f"✓ CICS adapter live at https://huggingface.co/{cics_repo_id}")

    if not args.skip_vsam:
        from slm.vsam.decoder import VsamDecoder
        vsam = VsamDecoder()
        if not vsam.is_trained():
            print("ERROR: VSAM adapter not found. Run: python slm/vsam/train.py")
            sys.exit(1)
        print(f"Pushing VSAM adapter to {vsam_repo_id}…")
        vsam.push_to_hub(vsam_repo_id, token=args.hf_token)
        print(f"✓ VSAM adapter live at https://huggingface.co/{vsam_repo_id}")

    print("\n── Next steps ─────────────────────────────────────────────────────")
    if not args.skip_cics:
        print(f"Set env-var:  HF_CICS_ADAPTER_REPO = {cics_repo_id}")
    if not args.skip_vsam:
        print(f"Set env-var:  HF_VSAM_ADAPTER_REPO = {vsam_repo_id}")
    print("───────────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
