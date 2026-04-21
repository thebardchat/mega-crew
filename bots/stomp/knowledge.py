"""Stomp knowledge layer — memory conflict resolution insights."""

from _knowledge_template import BotKnowledge


class StompKnowledge(BotKnowledge):

    def get_conflict_history(self, limit=10):
        """Get past memory conflicts detected."""
        return self.bot.recall("memory conflict contradiction inconsistency", limit=limit, memory_type="observation")

    def get_resolution_patterns(self, limit=10):
        """Get patterns in how conflicts were resolved."""
        return self.bot.recall("conflict resolved merged reconciled", limit=limit, memory_type="decision")
