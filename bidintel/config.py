"""Central settings. Every value can be overridden with an environment variable so the
same code runs on the Windows dev box and on the Jetson without edits."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent

# Pipeline layers (see pipeline/paths.py): bidintel is the GOLD layer and only reads SILVER.
DATA = PROJECT / "data"
SILVER_BIDS = Path(os.getenv("BI_SILVER_BIDS", DATA / "silver" / "bids.jsonl"))
FIRMS_FILE = Path(os.getenv("BI_FIRMS_FILE", DATA / "reference" / "Caleprocure_businesses_final.xls"))
ATTACHMENTS_DIR = PROJECT / "attachments"
ATTACHMENT_ROOTS = [DATA / "bronze", ATTACHMENTS_DIR]
DB_PATH = Path(os.getenv("BI_DB", ROOT / "data" / "bidintel.db"))
CLIENTS_JSON = Path(os.getenv("BI_CLIENTS", ROOT / "clients.json"))
WEB_DIR = ROOT / "web"

# LLM backend. "ollama" (local / Jetson, free, unlimited) or "openai" (any OpenAI-compatible
# endpoint: Ollama's /v1, llama.cpp server, vLLM, Groq, OpenRouter, Gemini's compat endpoint).
LLM_BACKEND = os.getenv("BI_LLM_BACKEND", "ollama")
LLM_URL = os.getenv("BI_LLM_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("BI_LLM_MODEL", "gemma4:26b")
LLM_API_KEY = os.getenv("BI_LLM_API_KEY", "")
LLM_NUM_CTX = int(os.getenv("BI_LLM_NUM_CTX", "16384"))
LLM_TIMEOUT = int(os.getenv("BI_LLM_TIMEOUT", "900"))

# How much attachment text goes into one per-bid analysis prompt (characters, ~4 chars/token).
CONTEXT_CHARS = int(os.getenv("BI_CONTEXT_CHARS", "36000"))

HOST = os.getenv("BI_HOST", "127.0.0.1")
PORT = int(os.getenv("BI_PORT", "8765"))
