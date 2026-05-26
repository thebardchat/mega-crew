"""discord_bridge knowledge layer — Discord I/O routing insights."""

from _knowledge_template import BotKnowledge


class DiscordBridgeKnowledge(BotKnowledge):

    def get_inbound_patterns(self, limit=10):
        """Patterns in DMs Shane sends — which crew gets called most, what topics."""
        return self.bot.recall("discord DM inbound from shane", limit=limit, memory_type="observation")

    def get_routing_decisions(self, limit=10):
        """Past routing choices — who handled what."""
        return self.bot.recall("routed dispatch crew member", limit=limit, memory_type="decision")

    def get_webhook_failures(self, limit=10):
        """Webhook posts that failed — missing env, rate limits, malformed payloads."""
        return self.bot.recall("webhook failed missing rate limit", limit=limit, memory_type="rejection")

    def get_command_usage(self, limit=10):
        """How often /godmode /log /mood /recent /who get used."""
        return self.bot.recall("slash command godmode log mood recent", limit=limit, memory_type="pattern")
