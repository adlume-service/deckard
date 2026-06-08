"""Marketing stack detection package."""

from deckard.services.marketing_stack.detector import (
    MARKETING_STACK_DETECTOR_VERSION,
    detect_marketing_stack,
)
from deckard.services.marketing_stack.gtm import (
    enrich_with_gtm_container,
    fetch_gtm_container,
    parse_gtm_container,
)
from deckard.services.marketing_stack.stage import run_marketing_stack_stage

__all__ = [
    "MARKETING_STACK_DETECTOR_VERSION",
    "detect_marketing_stack",
    "enrich_with_gtm_container",
    "fetch_gtm_container",
    "parse_gtm_container",
    "run_marketing_stack_stage",
]
