from datasets import load_dataset

# Load full dataset
dataset = load_dataset("stas/openwebtext-10k", split="train", streaming=False)

# Shuffle and select first 1000 samples
small_dataset = dataset.shuffle(seed=42).select(range(1000))

# Save to disk
save_path = "openwebtext-1k"
small_dataset.save_to_disk(save_path)
print(f"Saved to {save_path}")
