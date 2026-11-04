"""Re-export of the core ``Clock`` port for domain-layer use."""

from cloudbot.core.clock import Clock, FrozenClock, SystemClock

__all__ = ["Clock", "FrozenClock", "SystemClock"]
