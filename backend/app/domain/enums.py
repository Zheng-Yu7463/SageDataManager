from enum import StrEnum


class AssetType(StrEnum):
    PAPER = "paper"
    DATASET = "dataset"
    LITERATURE = "literature"
    PROJECT = "project"
    MODEL = "model"


class Visibility(StrEnum):
    LAB = "lab"
    PROJECT = "project"
    RESTRICTED = "restricted"


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    MISSING = "missing"
    UNVERIFIED = "unverified"
