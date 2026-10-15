from .base import (
    BLOCKED,
    ELIGIBLE,
    ELIGIBLE_WITH_CAUTION,
    Eligibility,
    EligibilityRules,
    RuleOutcome,
    check_eligibility,
    register_rules,
    rules_for,
)
from .sports import SportsRules
from .weather import WeatherRules

register_rules(WeatherRules())
register_rules(SportsRules())

__all__ = [
    "BLOCKED", "ELIGIBLE", "ELIGIBLE_WITH_CAUTION", "Eligibility", "EligibilityRules", "RuleOutcome",
    "SportsRules", "WeatherRules", "check_eligibility", "register_rules", "rules_for",
]
