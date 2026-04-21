"""Forge knowledge layer — tool drafting insights."""

from _knowledge_template import BotKnowledge


class ForgeKnowledge(BotKnowledge):

    def get_tools_drafted(self, limit=10):
        """Get history of tools that were drafted and shipped."""
        return self.bot.recall("tool drafted created shipped deployed", limit=limit, memory_type="decision")

    def get_tool_proposals(self, limit=10):
        """Get pending or past tool proposals and their status."""
        return self.bot.recall("tool proposal suggested spec design", limit=limit, memory_type="pattern")
