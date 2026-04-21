"""
grind.py — LEFT FOOT zone. Bulk re-embeds any Weaviate MEGABrain entries
older than 7 days that may lack fresh vectors. Keeps semantic search sharp.
Interval: 30 min
"""
from bot_base import BaseBot
from knowledge import GrindKnowledge
import weaviate_client
import mega_client


class GrindBot(BaseBot):
    def __init__(self):
        super().__init__("grind", "leftFoot", 1800)
        self.knowledge = GrindKnowledge(self)

    def tick(self):
        self.log("Fetching objects older than 7 days for re-embedding check")
        try:
            old_objects = weaviate_client.get_old_objects(days=7, limit=50)
        except Exception as e:
            self.log(f"Weaviate fetch failed: {e}", "error")
            self.heartbeat(f"Weaviate unavailable: {e}")
            return

        if not old_objects:
            self.heartbeat("No old objects needing re-embedding")
            return

        reembedded = 0
        for obj in old_objects:
            content = obj.get("content", "").strip()
            if not content:
                continue
            try:
                # Verify embedding is still valid by doing a quick similarity search
                # and checking the object appears in results
                results = weaviate_client.search(content[:200], limit=1)
                if results and results[0]["uuid"] == obj["uuid"]:
                    continue  # Already well-embedded

                # Re-embed by adding a new entry (Weaviate auto-embeds on insert)
                weaviate_client.add_object(
                    content=content,
                    source="grind-reembed",
                    category="reembedded",
                    bot="grind",
                )
                reembedded += 1
            except Exception as e:
                self.log(f"Re-embed failed for {obj['uuid']}: {e}", "error")

        self.log(f"Re-embedded {reembedded}/{len(old_objects)} old objects")
        self.heartbeat(f"Re-embedded {reembedded} objects — collection sharp")


if __name__ == "__main__":
    GrindBot().run()
