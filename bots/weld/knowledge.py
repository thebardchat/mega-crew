"""Weld knowledge layer — change application insights."""

from _knowledge_template import BotKnowledge


class WeldKnowledge(BotKnowledge):

    def get_commit_history(self, limit=10):
        """Get history of commits and applied changes."""
        return self.bot.recall("commit applied written saved", limit=limit, memory_type="decision")

    def get_applied_changes(self, limit=10):
        """Get details of what changes were applied to the system."""
        return self.bot.recall("change applied update modification", limit=limit, memory_type="pattern")
