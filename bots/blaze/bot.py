"""
blaze.py — RIGHT HAND zone. Feeds memory.db with real context from:
1. Shane's Weaviate DailyNote entries
2. LegacyKnowledge voice dumps
3. System logs + bot health
4. Recent conversations from Weaviate
Interval: 20 min
"""
import os
import sqlite3
import json
import urllib.request
from datetime import datetime
from pathlib import Path
from bot_base import BaseBot
from knowledge import BlazeKnowledge
import mega_client

_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
CORE_DIR     = _MEGA.parent
MEMORY_DB    = _MEGA / "memory.db"
WEAVIATE_URL = "http://localhost:8080"

SUMMARIZE_SYSTEM = (
    "You are a context ingestion agent for MEGA-SHANEBRAIN. "
    "Summarize the following content into 2-3 sentences of key facts "
    "that MEGA should know about Shane, his system, or his life. Be concise and specific."
)


class BlazeBot(BaseBot):
    def __init__(self):
        super().__init__("blaze", "rightHand", 1200)
        self.knowledge = BlazeKnowledge(self)
        self._seen_file = _MEGA / "status/blaze_seen.json"
        self._seen = self._load_seen()

    def _load_seen(self) -> set:
        if self._seen_file.exists():
            try:
                return set(json.loads(self._seen_file.read_text()))
            except Exception:
                pass
        return set()

    def _save_seen(self):
        seen_list = list(self._seen)[-500:]  # keep last 500
        self._seen_file.write_text(json.dumps(seen_list))
        self._seen = set(seen_list)

    def tick(self):
        injected = 0

        # 1. Pull DailyNote entries from Weaviate
        injected += self._ingest_weaviate("DailyNote", "note", 3)

        # 2. Pull voice dumps from LegacyKnowledge
        injected += self._ingest_weaviate_query(
            "LegacyKnowledge", "voice dump transcript Shane", "voice", 3
        )

        # 3. Pull recent conversations
        injected += self._ingest_weaviate("Conversation", "conversation", 2)

        # 4. Bot health snapshot
        injected += self._ingest_bot_health()

        self._save_seen()
        self.log(f"Injected {injected} new context entries into memory.db")
        self.heartbeat(f"Injected {injected} context entries (DailyNote+voice+conv+health)")

    def _ingest_weaviate(self, collection: str, category: str, limit: int) -> int:
        """Fetch recent objects from a Weaviate collection and inject into memory.db."""
        try:
            query = json.dumps({
                "query": f'{{ Get {{ {collection}(limit: {limit}) {{ content }} }} }}'
            }).encode()
            req = urllib.request.Request(
                f"{WEAVIATE_URL}/v1/graphql",
                data=query,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                objects = data.get("data", {}).get("Get", {}).get(collection, [])
                count = 0
                for obj in objects:
                    content = obj.get("content", "").strip()
                    if not content or len(content) < 30:
                        continue
                    key = f"{collection}:{hash(content[:100])}"
                    if key in self._seen:
                        continue
                    summary = mega_client.ask_bot(
                        f"Content from {collection}:\n\n{content[:1200]}",
                        system=self.build_system_prompt(SUMMARIZE_SYSTEM),
                        temperature=0.3,
                        num_predict=120,
                    )
                    self._inject_memory(f"[{collection}] {summary}", category, 0.6)
                    self._seen.add(key)
                    count += 1
                return count
        except Exception as e:
            self.log(f"Weaviate ingest ({collection}) failed: {e}", "error")
            return 0

    def _ingest_weaviate_query(self, collection: str, query_text: str, category: str, limit: int) -> int:
        """Semantic query into a Weaviate collection."""
        try:
            query = json.dumps({
                "query": (
                    f'{{ Get {{ {collection}(limit: {limit}, nearText: {{concepts: ["{query_text}"]}}) '
                    f'{{ content source }} }} }}'
                )
            }).encode()
            req = urllib.request.Request(
                f"{WEAVIATE_URL}/v1/graphql",
                data=query,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                objects = data.get("data", {}).get("Get", {}).get(collection, [])
                count = 0
                for obj in objects:
                    content = obj.get("content", "").strip()
                    if not content or len(content) < 30:
                        continue
                    key = f"{collection}:{hash(content[:100])}"
                    if key in self._seen:
                        continue
                    summary = mega_client.ask_bot(
                        f"Voice/knowledge content:\n\n{content[:1200]}",
                        system=self.build_system_prompt(SUMMARIZE_SYSTEM),
                        temperature=0.3,
                        num_predict=120,
                    )
                    self._inject_memory(f"[{obj.get('source','?')}] {summary}", category, 0.7)
                    self._seen.add(key)
                    count += 1
                return count
        except Exception as e:
            self.log(f"Weaviate query ({collection}) failed: {e}", "error")
            return 0

    def _ingest_bot_health(self) -> int:
        """Inject current bot health snapshot as system context."""
        try:
            status_file = _MEGA / "bot_status.json"
            if not status_file.exists():
                return 0
            data = json.loads(status_file.read_text())
            key = f"health:{data.get('timestamp','')[:16]}"
            if key in self._seen:
                return 0
            active = sum(1 for b in data.get("bots", []) if b.get("status") == "ACTIVE")
            total  = len(data.get("bots", []))
            iq     = data.get("mega_iq", "?")
            summary = f"MEGA crew health snapshot: {active}/{total} bots active, MEGA IQ={iq}. Updated {data.get('timestamp','')[:19]}."
            self._inject_memory(f"[System] {summary}", "system", 0.4)
            self._seen.add(key)
            return 1
        except Exception as e:
            self.log(f"Bot health ingest failed: {e}", "error")
            return 0

    def _inject_memory(self, content: str, category: str = "context", importance: float = 0.5):
        conn = sqlite3.connect(str(MEMORY_DB), timeout=5)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memories "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, category TEXT, "
                "content TEXT, importance REAL)"
            )
            conn.execute(
                "INSERT INTO memories (ts, category, content, importance) VALUES (?,?,?,?)",
                (datetime.utcnow().isoformat(), category, content, importance),
            )
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    BlazeBot().run()
