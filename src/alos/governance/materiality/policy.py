"""Materiality classification used by the authoritative release gate."""

from enum import StrEnum


class Materiality(StrEnum):
    NON_MATERIAL = "NON_MATERIAL"
    MATERIAL = "MATERIAL"


def requires_director(materiality: Materiality) -> bool:
    return materiality is Materiality.MATERIAL
