"""Operational wrappers around the frozen sports collector (RESEARCH_ONLY / NO_BET).

Nothing here changes a forecast, a protocol or a scoring rule. ``lock`` is the concurrency guard shared
by manual and scheduled sports runs; ``collector`` is the 5-minute cycle that calls the frozen
``sports_phase2_capture_v2`` capture and the ``sports_capture_v2_scoring_v1`` settlement code unchanged.
"""
