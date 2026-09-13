"""
ABSTRACT
--------
Shared CompleteEASE-facing exception used by both the real driver and the
software simulator. Higher-level AutoMapper code only needs to understand this
single fatal communication error type.
"""


class CompleteEASECommunicationError(RuntimeError):
    """Raised when AutoMapper can no longer communicate with CompleteEASE."""
