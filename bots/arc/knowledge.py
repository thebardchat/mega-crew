"""Arc knowledge layer — gatekeeper approval/rejection insights."""

from _knowledge_template import BotKnowledge


class ArcKnowledge(BotKnowledge):

    def get_approval_patterns(self, limit=10):
        """Get patterns in what gets approved."""
        return self.bot.recall("approved gate passed quality check", limit=limit, memory_type="decision")

    def get_rejection_patterns(self, limit=10):
        """Get patterns in what gets rejected and why."""
        return self.bot.recall("rejected gate blocked reason", limit=limit, memory_type="rejection")

    def get_confidence_trends(self, limit=10):
        """Get trends in gatekeeper confidence levels."""
        return self.bot.recall("confidence score approval certainty", limit=limit, memory_type="observation")
