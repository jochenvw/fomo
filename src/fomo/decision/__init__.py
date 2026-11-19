"""Microsoft-Decision-1 adapter (provider decision API, `choice` questions)."""

from .client import DecisionClient, DecisionResult
from .config import ClassifierConfig, load_classifier_config
from .policy import apply_policy

__all__ = ["ClassifierConfig", "DecisionClient", "DecisionResult", "apply_policy", "load_classifier_config"]
