"""Page-speed (Google PageSpeed Insights) detection package."""

from deckard.services.performance.client import (
    PERFORMANCE_DETECTOR_VERSION,
    detect_performance,
    fetch_pagespeed_insights,
)
from deckard.services.performance.parser import parse_pagespeed_response

__all__ = [
    "PERFORMANCE_DETECTOR_VERSION",
    "detect_performance",
    "fetch_pagespeed_insights",
    "parse_pagespeed_response",
]
