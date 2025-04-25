from datasets import load_dataset

# Load full dataset
dataset = load_dataset("stas/openwebtext-10k", split="train", streaming=False)

# Shuffle and select first 200 samples
small_dataset = dataset.shuffle(seed=42).select(range(200))

# Save to disk
save_path = "openwebtext-200"
small_dataset.save_to_disk(save_path)
print(f"Saved to {save_path}")
