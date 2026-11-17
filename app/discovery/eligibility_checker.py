"""Eligibility is unknown until a trusted source or contract check establishes it."""

from enum import Enum


class Eligibility(str, Enum):
    UNKNOWN = "unknown"
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"

