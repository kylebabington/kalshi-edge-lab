"""Optional AI research layer (secondary evidence only).

The statistical models remain the source of truth for probability, edge, EV and confidence.
This package explains already-computed opportunities from supplied evidence. It never changes
probabilities, never makes trade decisions and the rest of the app works when it is disabled
(``AI_PROVIDER=none``) or the provider is unreachable.
"""
