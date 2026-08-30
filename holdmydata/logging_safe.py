"""The only sanctioned logger.

Emits entity type, offsets and score. Never the matched value -- not in debug, not ever.
A redaction tool that leaks through its own logs has failed at its one job.
"""

import logging
import sys

_logger = logging.getLogger("holdmydata")
_configured = False


def configure(verbose: bool = False) -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    _configured = True


def info(msg: str) -> None:
    _logger.info(msg)


def warn(msg: str) -> None:
    _logger.warning(msg)


def debug(msg: str, *args) -> None:
    _logger.debug(msg, *args)


def log_finding(entity_type: str, start: int, end: int, score: float) -> None:
    """Log a hit by shape only. The span content is deliberately not an argument."""
    _logger.debug("found %s at [%d:%d] score=%.2f", entity_type, start, end, score)


def log_summary(counts: dict) -> None:
    if not counts:
        _logger.info("no entities found")
        return
    parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    _logger.info("redacted: %s", parts)
