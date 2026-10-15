"""Kalshi Market Explorer: cache-backed discovery across all categories.

Ordinary GETs read the local cache only. Network access happens only via the
explicit Refresh markets job. This package does not import collectors, models
or scoring.
"""
