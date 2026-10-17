# Fine-tuning Gemma 4 31B (built, not yet scored)

`build_dataset.py` turns the 117 training-split labels into chat examples; `train_lora.py` trains a
4-bit LoRA adapter on one 32 GB GPU (saves every epoch, `--resume`). The adapter has not been scored on
the locked 50, so no result claims a fine-tune.
