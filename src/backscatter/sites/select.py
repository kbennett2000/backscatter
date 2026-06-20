"""Nearest-site selection by great-circle distance (ADR-0005).

Single nearest radar = the active site (best resolution at a point). The full
distance-ranked list is kept for failover (Slice 5). No multi-radar blending.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

from backscatter.sites.table import Site, load_sites

# Mean Earth radius (km), IUGG.
EARTH_RADIUS_KM = 6371.0088
# Approximate usable reflectivity range of a WSR-88D (km). Used only to flag
# whether a ranked site plausibly "covers" a location.
COVERAGE_RANGE_KM = 230.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in kilometers."""
    rlat1, rlat2 = radians(lat1), radians(lat2)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


@dataclass(frozen=True)
class RankedSite:
    """A site paired with its distance from a query point."""

    site: Site
    distance_km: float

    @property
    def covers(self) -> bool:
        """Whether the query point is within the radar's usable range."""
        return self.distance_km <= COVERAGE_RANGE_KM


def rank_sites(lat: float, lon: float) -> list[RankedSite]:
    """All sites ranked by distance from ``(lat, lon)``, nearest first."""
    ranked = [
        RankedSite(site, haversine(lat, lon, site.lat, site.lon))
        for site in load_sites()
    ]
    ranked.sort(key=lambda r: r.distance_km)
    return ranked


def nearest_site(lat: float, lon: float) -> Site:
    """The single nearest site to ``(lat, lon)`` — the active radar."""
    return rank_sites(lat, lon)[0].site
