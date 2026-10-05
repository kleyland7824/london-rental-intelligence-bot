from typing import Protocol

from ..models import RawListing


class ListingSource(Protocol):
    def fetch(self) -> list[RawListing]:
        """Fetch listings from an authorized source."""
