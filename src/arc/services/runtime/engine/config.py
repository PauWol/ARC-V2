"""Engine configuration for the ARC-V2 local llama.cpp runtime.

All knobs are read from ARC_RUNTIME_* environment variables, matching the
existing arc.foundation.constants pattern (ARC_RUNTIME_PORT, ARC_RUNTIME_DEBUG).
If your project centralizes constants there, move the os.getenv() calls below
into arc/foundation/constants.py and import them here instead — kept local
for now so this module is self-contained and easy to review.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import gguf

from arc.foundation.constants import (
    ARC_RUNTIME_EMBEDDING_POOLING,
    ARC_RUNTIME_GEN_TIMEOUT_S,
    ARC_RUNTIME_MODEL_PATH,
    ARC_RUNTIME_N_BATCH,
    ARC_RUNTIME_N_CTX,
    ARC_RUNTIME_N_GPU_LAYERS,
    ARC_RUNTIME_N_THREADS,
    ARC_RUNTIME_VERBOSE,
)


def _free_vram_mb() -> int | None:
    """Best-effort free VRAM query via nvidia-smi. Returns None if no NVIDIA GPU."""
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            timeout=5,
        )
        # If multiple GPUs, take the one with the most free memory.
        values = [int(x.strip()) for x in out.decode().splitlines() if x.strip()]
        return max(values) if values else None
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def _gguf_layer_estimate(model_path: Path) -> tuple[int | None, float | None]:
    """Return (n_layers, bytes_per_layer_estimate) read cheaply from GGUF metadata.

    Uses the `gguf` package to read only the header/metadata, not the tensors,
    so this is fast even for large models. Falls back to (None, None) if the
    `gguf` package isn't installed or the file can't be parsed — callers must
    handle that by falling back to a conservative default.
    """

    try:
        reader = gguf.GGUFReader(model_path)
        n_layers = None
        for key in ("block_count",):
            for field_name, f in reader.fields.items():
                if field_name.endswith(key):
                    n_layers = int(f.parts[f.data[0]][0])
                    break
            if n_layers:
                break
        if not n_layers:
            return None, None

        file_size = os.path.getsize(model_path)
        # Rough split: embeddings/output head are a small, roughly fixed slice;
        # the remainder is divided evenly across transformer blocks. This is a
        # heuristic, not an exact accounting of tensor sizes per layer.
        non_layer_fraction = 0.10
        bytes_per_layer = (file_size * (1 - non_layer_fraction)) / n_layers
        return n_layers, bytes_per_layer
    except Exception:
        return None, None


def _auto_gpu_layers(model_path: Path) -> int:
    """Decide how many layers to offload to GPU.

    - Apple Silicon (Metal): offload everything; unified memory + the Metal
      backend handle this well, and llama.cpp's own paging manages the rest.
    - NVIDIA GPU present: estimate how many transformer layers fit in free
      VRAM (leaving ~15% headroom for KV cache + activations) using GGUF
      metadata. Falls back to 0 (CPU-only) if estimation isn't possible.
    - No GPU detected: 0 (CPU-only).
    """
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return -1  # llama-cpp-python: offload all layers

    free_mb = _free_vram_mb()
    if free_mb is None:
        return 0

    n_layers, bytes_per_layer = _gguf_layer_estimate(model_path)
    if not n_layers or not bytes_per_layer:
        # Can't estimate — be conservative rather than risk an OOM crash mid-load.
        return 0

    usable_bytes = free_mb * 1024 * 1024 * 0.85
    fit_layers = int(usable_bytes // bytes_per_layer)
    return max(0, min(fit_layers, n_layers))


@dataclass
class EngineConfig:
    model_path: Path
    n_ctx: int = 4096
    n_threads: int | None = None  # None -> llama.cpp picks os.cpu_count()-based default
    n_batch: int = 256
    n_gpu_layers: int = 0
    embedding_pooling: str = (
        "unspecified"  # mean | cls | last, passed to embedding context
    )
    generation_timeout_s: float = 120.0
    verbose: bool = False

    # Qwen3-style tags (see parsing.py); overridable for other model families later.
    think_open: str = "<think>"
    think_close: str = "</think>"
    tool_call_open: str = "<tool_call>"
    tool_call_close: str = "</tool_call>"

    @classmethod
    def from_env(cls) -> "EngineConfig":
        model_path = ARC_RUNTIME_MODEL_PATH
        if not model_path:
            raise RuntimeError(
                "ARC_RUNTIME_MODEL_PATH is not set. Point it at a local .gguf file."
            )
        if not os.path.isfile(model_path):
            raise RuntimeError(f"ARC_RUNTIME_MODEL_PATH does not exist: {model_path}")

        n_gpu_layers_env = ARC_RUNTIME_N_GPU_LAYERS
        if n_gpu_layers_env.strip().lower() == "auto":
            n_gpu_layers = _auto_gpu_layers(model_path)
        else:
            n_gpu_layers = int(n_gpu_layers_env)

        n_threads_env = ARC_RUNTIME_N_THREADS
        n_threads = int(n_threads_env) if n_threads_env != "None" else None

        return cls(
            model_path=model_path,
            n_ctx=ARC_RUNTIME_N_CTX,
            n_threads=n_threads,
            n_batch=ARC_RUNTIME_N_BATCH,
            n_gpu_layers=n_gpu_layers,
            embedding_pooling=ARC_RUNTIME_EMBEDDING_POOLING,
            generation_timeout_s=ARC_RUNTIME_GEN_TIMEOUT_S,
            verbose=ARC_RUNTIME_VERBOSE,
        )
