"""Torch knowledge layer — persona editing insights."""

from _knowledge_template import BotKnowledge


class TorchKnowledge(BotKnowledge):

    def get_successful_edits(self, limit=10):
        """Get persona edits that were approved and applied."""
        return self.bot.recall("persona edit approved successful", limit=limit, memory_type="decision")

    def get_failed_proposals(self, limit=10):
        """Get persona edit proposals that were rejected."""
        return self.bot.recall("persona edit rejected failed proposal", limit=limit, memory_type="rejection")
