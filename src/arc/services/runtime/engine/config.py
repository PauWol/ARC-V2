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
from dataclasses import dataclass, field
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
        values = [int(x.strip()) for x in out.decode().splitlines() if x.strip()]
        return max(values) if values else None
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def _gguf_layer_estimate(model_path: Path) -> tuple[int | None, float | None]:
    """Return (n_layers, bytes_per_layer_estimate) read cheaply from GGUF metadata."""
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
        non_layer_fraction = 0.10
        bytes_per_layer = (file_size * (1 - non_layer_fraction)) / n_layers
        return n_layers, bytes_per_layer
    except Exception:
        return None, None


def _auto_gpu_layers(model_path: Path) -> int:
    """Decide how many layers to offload to GPU."""
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return -1

    free_mb = _free_vram_mb()
    if free_mb is None:
        return 0

    n_layers, bytes_per_layer = _gguf_layer_estimate(model_path)
    if not n_layers or not bytes_per_layer:
        return 0

    usable_bytes = free_mb * 1024 * 1024 * 0.85
    fit_layers = int(usable_bytes // bytes_per_layer)
    return max(0, min(fit_layers, n_layers))


@dataclass
class EngineConfig:
    model_path: Path
    n_ctx: int = 4096
    n_threads: int | None = None
    n_batch: int = 256
    n_gpu_layers: int = 0
    embedding_pooling: str = "unspecified"
    generation_timeout_s: float = 120.0
    verbose: bool = False

    # -- chat generation (raw completion + our own tag parsing) --------------
    #
    # We deliberately do NOT set llama-cpp-python's `chat_format` on the
    # Llama() instance anymore. Chat generation goes through the raw
    # completion API (Llama.__call__) against a prompt we render ourselves
    # via templating.ChatTemplateRenderer (using the GGUF's own embedded
    # tokenizer.chat_template), and we parse tool calls out of the token
    # stream via AgenticStreamParser. This sidesteps llama-cpp-python's
    # `chatml-function-calling` handler entirely, which is what raises
    # "Automatic streaming tool choice is not supported" — see
    # agentic_parsing.py's module docstring for the full story. It also
    # means no grammar-constrained decoding overhead unless you opt into
    # it yourself.
    think_open: str = "<think>"
    think_close: str = "</think>"
    tool_call_open: str = "<tool_call>"
    tool_call_close: str = "</tool_call>"

    # Which inner tool-call body format to parse. "json" expects
    # {"name": ..., "arguments": {...}} inside the <tool_call> tags
    # (Hermes/most ChatML-family models). "xml_function_parameter" expects
    # <function=name><parameter=key>value</parameter></function> instead --
    # this is the Qwen3-Coder / Qwen3.5+ *native* format, and is what these
    # GGUFs have actually been observed to emit (their embedded
    # tokenizer.chat_template instructs the model to use it, not JSON) --
    # see agentic_parsing.py's module docstring for both formats in full.
    tool_call_dialect: str = "xml_function_parameter"

    # Extra stop strings appended on top of whatever the request specifies.
    # The model's real EOS token already stops generation on its own
    # (llama.cpp handles that internally); this is a safety net for chat
    # templates whose turn-end marker (e.g. Qwen/ChatML's <|im_end|>)
    # isn't registered as the GGUF's primary EOS token id.
    stop_sequences: list[str] = field(default_factory=lambda: ["<|im_end|>"])

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

        stop_env = os.getenv("ARC_RUNTIME_STOP_SEQUENCES", "<|im_end|>")
        stop_sequences = [s for s in stop_env.split(",") if s.strip()]

        return cls(
            model_path=model_path,
            n_ctx=ARC_RUNTIME_N_CTX,
            n_threads=n_threads,
            n_batch=ARC_RUNTIME_N_BATCH,
            n_gpu_layers=n_gpu_layers,
            embedding_pooling=ARC_RUNTIME_EMBEDDING_POOLING,
            generation_timeout_s=ARC_RUNTIME_GEN_TIMEOUT_S,
            verbose=ARC_RUNTIME_VERBOSE,
            tool_call_open=os.getenv("ARC_RUNTIME_TOOL_CALL_OPEN", "<tool_call>"),
            tool_call_close=os.getenv("ARC_RUNTIME_TOOL_CALL_CLOSE", "</tool_call>"),
            tool_call_dialect=os.getenv(
                "ARC_RUNTIME_TOOL_CALL_DIALECT", "xml_function_parameter"
            ),
            stop_sequences=stop_sequences,
        )
