"""Fetch YourMT3+'s code and the YPTF.MoE+Multi (noPS) checkpoint from its Hugging Face Space."""
from huggingface_hub import snapshot_download

snapshot_download(
    "mimbres/YourMT3", repo_type="space", local_dir="space",
    allow_patterns=["amt/src/**", "model_helper.py",
                    "amt/logs/2024/mc13_256_g4_all_v7_mt3f_sqr_rms_moe_wf4_n8k2_silu_rope_rp_b36_nops/**"],
)
