"""
slm/shared/base.py
──────────────────
Abstract base class shared by both SLMs (CICS translator and VSAM decoder).

Responsibilities
────────────────
• Defines the common training loop using HuggingFace Trainer + LoRA (PEFT).
• Defines the inference interface: translate(input_text) → str.
• Handles model loading, device detection, adapter saving/loading.
• CPU-safe: trains on CPU when CUDA is unavailable (slower but correct).

Both SLMs inherit from SlmBase and only need to supply:
  • BASE_MODEL_ID   – the HuggingFace model to fine-tune
  • ADAPTER_DIR     – where the LoRA adapter weights are saved
  • generate_dataset() – returns a list of {"input": ..., "output": ...} dicts
  • system_prompt()    – the task-specific system prompt for the model
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import torch
try:
    from huggingface_hub import HfApi, snapshot_download
    _HF_AVAILABLE = True
except ImportError:
    _HF_AVAILABLE = False
from datasets import Dataset
from peft import (
    LoraConfig,
    TaskType,
    get_peft_model,
    PeftModel,
)
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    DataCollatorForSeq2Seq,
    GenerationConfig,
    Trainer,
    TrainingArguments,
)

# ── Defaults ───────────────────────────────────────────────────────────────
DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"   # 0.5B — runs on CPU in <2 GB RAM
MAX_INPUT_LENGTH   = 512
MAX_TARGET_LENGTH  = 512
MAX_TOTAL_LENGTH   = MAX_INPUT_LENGTH + MAX_TARGET_LENGTH


class SlmBase(ABC):
    """
    Base class for a LoRA-fine-tuned SLM translator.

    Subclasses implement:
      BASE_MODEL_ID  – str class attribute
      ADAPTER_DIR    – Path class attribute
      generate_dataset() → list[dict]
      system_prompt()    → str
    """

    BASE_MODEL_ID: str   = DEFAULT_BASE_MODEL
    ADAPTER_DIR:   Path  = Path("slm/adapter")

    def __init__(self, device: str | None = None, hf_repo: str | None = None) -> None:
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.hf_repo = hf_repo  # HuggingFace Hub repo id, e.g. 'yourname/cics-adapter'
        self._tokenizer: AutoTokenizer | None = None
        self._model: Any = None  # base or PEFT model

    # ── Abstract interface ─────────────────────────────────────────────────

    @abstractmethod
    def generate_dataset(self) -> list[dict[str, str]]:
        """
        Return a list of training examples, each:
          {"input": "<COBOL snippet>", "output": "<Python equivalent>"}
        """
        ...

    @abstractmethod
    def system_prompt(self) -> str:
        """Return the task-specific system prompt used during training and inference."""
        ...

    # ── Public API ─────────────────────────────────────────────────────────

    def is_trained(self) -> bool:
        """True if a saved LoRA adapter exists on disk."""
        return (self.ADAPTER_DIR / "adapter_config.json").exists()

    def train(
        self,
        num_epochs: int = 3,
        batch_size: int = 2,
        learning_rate: float = 3e-4,
        lora_r: int = 16,
        lora_alpha: int = 32,
        lora_dropout: float = 0.05,
    ) -> None:
        """
        Fine-tune the base model with LoRA on the domain-specific dataset.
        Saves the adapter to ADAPTER_DIR.
        """
        print(f"[SLM:{self.__class__.__name__}] Loading base model {self.BASE_MODEL_ID}…")
        tokenizer = self._load_tokenizer()
        model = AutoModelForCausalLM.from_pretrained(
            self.BASE_MODEL_ID,
            torch_dtype=torch.float32,   # float32 for CPU stability
            trust_remote_code=True,
        )

        # ── LoRA config ────────────────────────────────────────────────────
        lora_cfg = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=lora_r,
            lora_alpha=lora_alpha,
            lora_dropout=lora_dropout,
            target_modules=["q_proj", "v_proj"],  # attention projections
            bias="none",
        )
        model = get_peft_model(model, lora_cfg)
        model.print_trainable_parameters()

        # ── Dataset ────────────────────────────────────────────────────────
        raw = self.generate_dataset()
        print(f"[SLM] Training on {len(raw)} examples…")
        hf_dataset = Dataset.from_list(
            [self._format_example(ex, tokenizer) for ex in raw]
        )

        # ── Training arguments ─────────────────────────────────────────────
        self.ADAPTER_DIR.mkdir(parents=True, exist_ok=True)
        training_args = TrainingArguments(
            output_dir=str(self.ADAPTER_DIR / "checkpoints"),
            num_train_epochs=num_epochs,
            per_device_train_batch_size=batch_size,
            gradient_accumulation_steps=4,
            learning_rate=learning_rate,
            lr_scheduler_type="cosine",
            warmup_ratio=0.05,
            fp16=False,          # CPU-safe
            bf16=False,
            logging_steps=10,
            save_strategy="epoch",
            report_to="none",    # no wandb / tensorboard dependency
            dataloader_pin_memory=False,
        )

        data_collator = DataCollatorForSeq2Seq(
            tokenizer, model=model, padding=True, pad_to_multiple_of=8
        )

        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=hf_dataset,
            data_collator=data_collator,
        )

        print(f"[SLM] Starting training on {self.device}…")
        trainer.train()

        # ── Save adapter ───────────────────────────────────────────────────
        model.save_pretrained(str(self.ADAPTER_DIR))
        tokenizer.save_pretrained(str(self.ADAPTER_DIR))
        print(f"[SLM] Adapter saved to {self.ADAPTER_DIR}")

    def translate(self, input_text: str, max_new_tokens: int = 512) -> str:
        """
        Run inference: given a domain-specific snippet, return the Python translation.
        Loads the fine-tuned adapter on first call (lazy load).
        """
        if self._model is None:
            self._load_for_inference()

        prompt = self._build_inference_prompt(input_text)
        inputs = self._tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=MAX_INPUT_LENGTH,
        ).to(self.device)

        with torch.no_grad():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,          # greedy — deterministic output
                temperature=1.0,
                repetition_penalty=1.1,
                pad_token_id=self._tokenizer.eos_token_id,
            )

        # Decode only the newly generated tokens (skip the prompt)
        new_tokens = output_ids[0][inputs["input_ids"].shape[-1]:]
        return self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    # ── Internal helpers ───────────────────────────────────────────────────

    def push_to_hub(self, repo_id: str, token: str | None = None) -> None:
        """
        Upload the trained LoRA adapter to HuggingFace Hub.
        Call after training: slm.push_to_hub("yourname/cics-adapter")
        """
        if not _HF_AVAILABLE:
            raise RuntimeError("pip install huggingface_hub to use push_to_hub()")
        if not self.is_trained():
            raise RuntimeError("No adapter to push. Train the model first.")
        api = HfApi()
        api.create_repo(repo_id=repo_id, repo_type="model",
                        exist_ok=True, token=token)
        api.upload_folder(
            folder_path=str(self.ADAPTER_DIR),
            repo_id=repo_id,
            repo_type="model",
            token=token,
        )
        print(f"[SLM] Adapter pushed to https://huggingface.co/{repo_id}")

    def _resolve_adapter_source(self) -> str:
        """
        Return the path to load the adapter from.
        Priority: local ADAPTER_DIR → HuggingFace Hub repo.
        """
        if self.is_trained():
            return str(self.ADAPTER_DIR)
        if self.hf_repo:
            if not _HF_AVAILABLE:
                raise RuntimeError("pip install huggingface_hub to load from Hub")
            print(f"[SLM] Downloading adapter from {self.hf_repo}…")
            local_dir = snapshot_download(repo_id=self.hf_repo,
                                          local_dir=str(self.ADAPTER_DIR))
            return local_dir
        raise RuntimeError(
            f"No trained adapter found at {self.ADAPTER_DIR} and no hf_repo set. "
            "Call .train() first, or pass hf_repo='yourname/adapter-name'."
        )

    def _load_tokenizer(self) -> AutoTokenizer:
        source = str(self.ADAPTER_DIR) if self.is_trained() else (self.hf_repo or self.BASE_MODEL_ID)
        tok = AutoTokenizer.from_pretrained(source, trust_remote_code=True)
        if tok.pad_token is None:
            tok.pad_token = tok.eos_token
        self._tokenizer = tok
        return tok

    def _load_for_inference(self) -> None:
        adapter_source = self._resolve_adapter_source()
        print(f"[SLM:{self.__class__.__name__}] Loading adapter from {adapter_source}…")
        tok = self._load_tokenizer()
        base = AutoModelForCausalLM.from_pretrained(
            self.BASE_MODEL_ID,
            torch_dtype=torch.float32,
            trust_remote_code=True,
        )
        self._model = PeftModel.from_pretrained(base, adapter_source)
        self._model.to(self.device)
        self._model.eval()
        print(f"[SLM] Ready on {self.device}.")

    def _build_inference_prompt(self, input_text: str) -> str:
        """Build the chat-style prompt used at inference time."""
        return (
            f"<|system|>\n{self.system_prompt()}\n"
            f"<|user|>\n{input_text}\n"
            f"<|assistant|>\n"
        )

    def _format_example(
        self, example: dict[str, str], tokenizer: AutoTokenizer
    ) -> dict:
        """
        Format one training example as a tokenised, masked sequence.
        Only the target (assistant) tokens contribute to the loss.
        """
        prompt      = self._build_inference_prompt(example["input"])
        full_text   = prompt + example["output"] + tokenizer.eos_token

        tokenized   = tokenizer(
            full_text,
            truncation=True,
            max_length=MAX_TOTAL_LENGTH,
            padding=False,
        )
        prompt_ids  = tokenizer(
            prompt,
            truncation=True,
            max_length=MAX_INPUT_LENGTH,
        )["input_ids"]

        # Mask prompt tokens in labels so loss is only on the output tokens
        labels = list(tokenized["input_ids"])
        for i in range(len(prompt_ids)):
            if i < len(labels):
                labels[i] = -100

        tokenized["labels"] = labels
        return tokenized
