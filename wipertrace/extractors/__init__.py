"""Artifact extractors — stage 2 of the pipeline."""

from wipertrace.extractors.amcache import AmcacheExtractor
from wipertrace.extractors.base import Extractor, ExtractorError
from wipertrace.extractors.logfile import LogFileExtractor
from wipertrace.extractors.mft import MftExtractor
from wipertrace.extractors.prefetch import PrefetchExtractor
from wipertrace.extractors.usnjrnl import UsnJrnlExtractor

REGISTRY: dict[str, type[Extractor]] = {
    "usnjrnl": UsnJrnlExtractor,
    "mft": MftExtractor,
    "logfile": LogFileExtractor,
    "prefetch": PrefetchExtractor,
    "amcache": AmcacheExtractor,
}

__all__ = ["REGISTRY", "Extractor", "ExtractorError"]
