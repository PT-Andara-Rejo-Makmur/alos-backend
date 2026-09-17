"""HTTP-only boundary to the GENESIS AI Control Plane."""

from alos.integrations.genesis.client import GenesisClient, GenesisClientError

__all__ = ["GenesisClient", "GenesisClientError"]
