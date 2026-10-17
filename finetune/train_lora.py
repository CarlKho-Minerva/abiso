"""QLoRA fine-tune of Gemma 4 31B on the Abiso train split. RUNS ON THE PC ONLY.

Not run yet, and not runnable on the Macs (Carl's rule). See finetune/README.md
for the exact command, the GPU checks to do first, and how to serve the adapter.

    python finetune/train_lora.py --data data/finetune/train.jsonl --out runs/abiso-lora-v1

Design:
- Base google/gemma-4-31B-it (the bf16 parent of the AWQ checkpoint vLLM serves),
  loaded in 4-bit NF4 so it fits one 32 GB RTX 5090 with the vision tower frozen.
- LoRA r=16 on the language model's attention and MLP projections only.
- Loss on the assistant turn only (the JSON), so it learns the output, not the prompt.
- Images are passed through the processor exactly as compile.py sends them.
- Deterministic seed; checkpoints every epoch so a killed job can resume.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--base", default="google/gemma-4-31B-it")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=3072)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--seed", type=int, default=20261003)
    ap.add_argument("--resume", action="store_true")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    # Heavy imports here so the module imports cleanly on the Mac for tests.
    import torch
    from PIL import Image
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForImageTextToText, AutoProcessor, BitsAndBytesConfig,
                              Trainer, TrainingArguments)

    random.seed(a.seed)
    torch.manual_seed(a.seed)
    rows = [json.loads(l) for l in a.data.read_text().splitlines() if l.strip()]
    if not rows:
        raise SystemExit(f"no rows in {a.data}")
    repo = Path(__file__).resolve().parent.parent

    processor = AutoProcessor.from_pretrained(a.base)
    model = AutoModelForImageTextToText.from_pretrained(
        a.base,
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                               bnb_4bit_compute_dtype=torch.bfloat16,
                                               bnb_4bit_use_double_quant=True),
        torch_dtype=torch.bfloat16, device_map={"": 0}, attn_implementation="sdpa")
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model = get_peft_model(model, LoraConfig(
        r=a.rank, lora_alpha=a.alpha, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
        # language model only; the vision tower stays frozen
        target_modules=r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)"))
    model.print_trainable_parameters()

    def encode(row):
        images = [Image.open(repo / p).convert("RGB") for p in row["images"]]
        msgs = []
        for m in row["messages"]:
            content = [{"type": "text", "text": m["content"]}]
            if m["role"] == "user":
                content = [{"type": "image"} for _ in images] + content
            msgs.append({"role": m["role"], "content": content})
        prompt = processor.apply_chat_template(msgs[:-1], add_generation_prompt=True, tokenize=False)
        full = processor.apply_chat_template(msgs, tokenize=False)
        enc = processor(text=full, images=images or None, return_tensors="pt",
                        truncation=True, max_length=a.max_len)
        n_prompt = len(processor(text=prompt, images=images or None, return_tensors="pt")["input_ids"][0])
        labels = enc["input_ids"].clone()
        labels[:, :n_prompt] = -100           # loss on the assistant JSON only
        enc["labels"] = labels
        return {k: v[0] for k, v in enc.items()}

    data = [encode(r) for r in rows]

    def collate(batch):
        assert len(batch) == 1, "batch size 1; use --grad-accum"
        return {k: v.unsqueeze(0) for k, v in batch[0].items()}

    args = TrainingArguments(
        output_dir=str(a.out), num_train_epochs=a.epochs, learning_rate=a.lr,
        per_device_train_batch_size=1, gradient_accumulation_steps=a.grad_accum,
        lr_scheduler_type="cosine", warmup_ratio=0.05, bf16=True, logging_steps=5,
        save_strategy="epoch", save_total_limit=3, seed=a.seed, report_to=[],
        gradient_checkpointing=True, remove_unused_columns=False, optim="paged_adamw_8bit")
    trainer = Trainer(model=model, args=args, train_dataset=data, data_collator=collate)
    trainer.train(resume_from_checkpoint=a.resume or None)
    model.save_pretrained(a.out / "adapter")
    processor.save_pretrained(a.out / "adapter")
    (a.out / "TRAINED_ON.json").write_text(json.dumps(
        {"data": str(a.data), "n_rows": len(rows), "post_ids": [r["post_id"] for r in rows],
         "base": a.base, "args": {k: str(v) for k, v in vars(a).items()}}, indent=1))
    print(f"adapter -> {a.out / 'adapter'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
