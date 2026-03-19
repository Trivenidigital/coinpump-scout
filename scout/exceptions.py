"""Domain exceptions for CoinPump Scout."""


class ScoutError(Exception):
    """Base exception for CoinPump Scout."""


class ScorerError(ScoutError):
    """Error in scoring logic."""


class MiroFishTimeoutError(ScoutError):
    """MiroFish simulation timed out."""


class MiroFishConnectionError(ScoutError):
    """Cannot connect to MiroFish service."""


class AlertDeliveryError(ScoutError):
    """Failed to deliver alert."""


class SafetyCheckError(ScoutError):
    """Error checking token safety."""
