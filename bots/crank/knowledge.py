"""Crank knowledge layer — scheduling insights."""

from _knowledge_template import BotKnowledge


class CrankKnowledge(BotKnowledge):

    def get_scheduling_stats(self, limit=10):
        """Get scheduling run stats — timing, delays, and throughput."""
        return self.bot.recall("schedule run timing delay throughput queue", limit=limit, memory_type="pattern")
