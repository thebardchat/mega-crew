"""
knowledge.py — Bot-specific Weaviate knowledge layer.
Each bot gets custom queries tailored to its role.
Inherits remember/recall from BaseBot but adds domain-specific methods.
"""


class BotKnowledge:
    """Mixin for bot-specific knowledge operations. Attach to bot instance."""

    def __init__(self, bot):
        self.bot = bot

    def get_learnings(self, limit=10):
        """Get this bot's learned patterns."""
        return self.bot.recall("patterns and learnings", limit=limit, memory_type="pattern")

    def get_decisions(self, limit=10):
        """Get this bot's past decisions."""
        return self.bot.recall("decisions made", limit=limit, memory_type="decision")

    def get_rejections(self, limit=10):
        """Get this bot's rejection history from Weaviate."""
        return self.bot.recall("arc rejected", limit=limit, memory_type="rejection")

    def get_observations(self, limit=10):
        """Get this bot's observations."""
        return self.bot.recall("observations", limit=limit, memory_type="observation")

    def summarize_history(self):
        """One-line summary of bot's memory state."""
        all_mem = self.bot.recall_all(limit=50)
        from collections import Counter
        types = Counter(m.get("memory_type", "?") for m in all_mem)
        return f"{len(all_mem)} memories: " + ", ".join(f"{c} {t}" for t, c in types.most_common())
