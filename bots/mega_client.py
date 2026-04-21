"""
mega_client.py — Ollama wrapper for bot internal LLM calls (llama3.2:3b).
All bot judging/scoring/summarization goes through here.

llama3.1:8b is reserved exclusively for MEGA chat (/api/mega/chat in dashboard.py).
Do NOT change DEFAULT_MODEL back to llama3.1:8b — bots use 3b only.
"""
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

OLLAMA_LOCAL   = "http://localhost:11434"               # direct local Ollama
OLLAMA_CLUSTER = "http://localhost:11435"               # cluster proxy — set to your Tailscale IP if using multi-node
OLLAMA_URL     = f"{OLLAMA_CLUSTER}/api/generate"      # 3b generate → cluster, frees local RAM
OLLAMA_CHAT    = f"{OLLAMA_CLUSTER}/api/chat"          # 3b chat → cluster
DEFAULT_MODEL  = "llama3.2:3b"  # bot work — runs on cluster, NOT localhost
DEFAULT_TIMEOUT = 180

CLUSTER_NODES_FILE = Path("/mnt/shanebrain-raid/shanebrain-core/scripts/cluster-nodes.json")
_node_cache: dict = {}   # host → {"ok": bool, "t": float}


def _best_base_url(timeout: int = 3) -> str:
    """Always use local Ollama for bot work — fast, controlled, no network queue.
    Cluster nodes are for user-facing chat, not background bot inference."""
    return OLLAMA_LOCAL


BOT_MODEL = "llama3.2:1b"   # all internal bot work — 3-4x faster than 3b on CPU cluster


def ask_bot(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    num_predict: int = 256,
    timeout: int = 60,
) -> str:
    """Bot-internal LLM calls — direct to best cluster node, streaming to avoid idle timeout."""
    base = _best_base_url()
    url  = f"{base}/api/generate"
    body = {
        "model": BOT_MODEL,
        "prompt": prompt,
        "stream": True,   # each token flows immediately — no idle socket timeout
        "keep_alive": "5m",  # unload model after 5min idle — frees RAM for chat
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if system:
        body["system"] = system

    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    tokens = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    tokens.append(chunk.get("response", ""))
                    if chunk.get("done"):
                        break
                except Exception:
                    continue
    except Exception:
        # LLM unavailable or timeout — return whatever was collected, or empty string
        pass
    return "".join(tokens).strip()


def generate(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    num_predict: int = 256,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Single-turn generation. Returns response text or raises on failure."""
    body = {
        "model": DEFAULT_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if system:
        body["system"] = system

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read()).get("response", "").strip()


def chat(
    messages: list[dict],
    system: Optional[str] = None,
    temperature: float = 0.7,
    num_predict: int = 256,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """Multi-turn chat. messages = [{'role':'user','content':'...'}, ...]"""
    msg_list = []
    if system:
        msg_list.append({"role": "system", "content": system})
    msg_list.extend(messages)

    body = {
        "model": DEFAULT_MODEL,
        "messages": msg_list,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        OLLAMA_CHAT, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
        return result.get("message", {}).get("content", "").strip()


def embed(text: str, model: str = "nomic-embed-text", timeout: int = 60) -> list[float]:
    """Generate an embedding vector for text."""
    body = {"model": model, "prompt": text}
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        "http://localhost:11435/api/embeddings",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read()).get("embedding", [])


def list_models() -> list[str]:
    """Return list of model names available on local Ollama."""
    req = urllib.request.Request("http://localhost:11435/api/tags")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return [m["name"] for m in json.loads(resp.read()).get("models", [])]
