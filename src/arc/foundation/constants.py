import os
from pathlib import Path
from dotenv import load_dotenv
from typing import TypeVar

T = TypeVar("T")


def path(p: str | Path) -> Path:
    """Expand '~' and return a Path."""
    return Path(p).expanduser()


ENV_PATH = path("~/arc/.env")


def load_dot_env() -> bool:
    """Load ARC's .env file.

    Returns:
        True if the .env file was loaded.
        False if the file does not exist.
    """
    if not ENV_PATH.is_file():
        return False

    return load_dotenv(
        ENV_PATH,
        override=False,
    )


def get_env(key: str, default: T) -> str | T:
    """
    Return an environment variable or a default value.
    """
    return os.getenv(key, default)


def get_env_str(key: str, default: str) -> str:
    return os.getenv(key, default)


def get_env_bool(key: str, default: str = "false") -> bool:
    value = os.getenv(key)
    if value is None:
        value = default
    return value.strip().lower() in {"1", "true", "True", "yes", "on"}


def get_env_int(key: str, default: str) -> int:
    value = os.getenv(key)
    if value is None:
        value = default
    return int(value)


DEFAULT_DOT_ENV = {
    "TERMINAL_NO_COLOR": "0",
    "PYTHONUNBUFFERED": "1",
    # ---
    "ARC_DIR": "~/arc",
    "AGENT_WORKSPACE": "~/arc/workspace",
    "LLM_MODEL_STORE": "~/arc/models",
    # ---
    "HF_TOKEN": "YOUR-HUGGINGFACE-TOKEN-OPTIONAL",
    # ---
    "LOG_LEVEL": "INFO",
    "LOG_FILE": "~/arc/workspace/agent.log",
    "LOG_CONSOLE": "1",
    "LOG_JSON": "0",
    "LOG_ROTATE": "1",
    "LOG_MAX_BYTES": "10485760",
    "LOG_BACKUP_COUNT": "2",
    # ---
    "SANDBOX_ALLOW": "READ,WRITE,EXECUTE,NETWORK",
    "SANDBOX_CONFIRM": "DELETE,SYSTEM,INSTALL",
    # ---
    "EXTRACTOR_INPUT_TOKEN_THRESHOLD": "150",
}

_DEV = DEFAULT_DOT_ENV

# System
SERVICES_CONFIG_PATH = Path(__file__).parent.parent / "config" / "services.arc.yaml"
TERMINAL_NO_COLOR = get_env_bool("TERMINAL_NO_COLOR", _DEV["TERMINAL_NO_COLOR"])

# Project Directories
ARC_DIR = path(get_env("ARC_DIR", _DEV["ARC_DIR"]))
AGENT_WORKSPACE = path(get_env("AGENT_WORKSPACE", _DEV["AGENT_WORKSPACE"]))

# LLM Model Management
LLM_MODEL_STORE = path(get_env("LLM_MODEL_STORE", _DEV["LLM_MODEL_STORE"]))
HF_TOKEN = get_env_str("HF_TOKEN", "None")


# Logging
LOG_LEVEL = get_env_str("LOG_LEVEL", _DEV["LOG_LEVEL"])
LOG_FILE = path(get_env("LOG_FILE", _DEV["LOG_FILE"]))
LOG_CONSOLE = get_env_bool("LOG_CONSOLE", _DEV["LOG_CONSOLE"])
LOG_JSON = get_env_bool("LOG_JSON", _DEV["LOG_JSON"])
LOG_ROTATE = get_env_bool("LOG_ROTATE", _DEV["LOG_ROTATE"])
LOG_MAX_BYTES = get_env_int("LOG_MAX_BYTES", _DEV["LOG_MAX_BYTES"])
LOG_BACKUP_COUNT = get_env_int("LOG_BACKUP_COUNT", _DEV["LOG_BACKUP_COUNT"])


# Permissions
SANDBOX_ALLOW = get_env_str("SANDBOX_ALLOW", _DEV["SANDBOX_ALLOW"])
SANDBOX_CONFIRM = get_env_str("SANDBOX_CONFIRM", _DEV["SANDBOX_CONFIRM"])

# Agent Runtime
EXTRACTOR_INPUT_TOKEN_THRESHOLD = get_env_int(
    "EXTRACTOR_INPUT_TOKEN_THRESHOLD", _DEV["EXTRACTOR_INPUT_TOKEN_THRESHOLD"]
)


def workspace_path(_path: str | None = None) -> Path:
    """Return the Agent workspace path or a path inside it."""

    workspace = path(AGENT_WORKSPACE)
    workspace.mkdir(parents=True, exist_ok=True)

    if _path is None:
        return workspace

    full = (workspace / _path).resolve()

    if not full.is_relative_to(workspace.resolve()):
        raise ValueError("Path escape blocked")

    return full
