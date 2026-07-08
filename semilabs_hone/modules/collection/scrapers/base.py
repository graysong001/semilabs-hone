"""BasePlatformScraper ABC + schema group constants."""

from abc import ABC, abstractmethod

from semilabs_hone.core.models.schemas import ItemRef, ScrapedPost, ScrapedComment

# ---------------------------------------------------------------------------
# Schema group constants (used by field_extract + engine)
# ---------------------------------------------------------------------------
GROUP_ITEM_REF = "ItemRef"
GROUP_POST_BODY = "Post.body"
GROUP_POST_INTERACTIONS = "Post.interactions"
GROUP_COMMENTS = "Comments"


class BasePlatformScraper(ABC):
    """Abstract base for all platform scrapers."""

    @abstractmethod
    async def search(self, keyword: str, sort: str = "general") -> list[ItemRef]:
        """Run search flow, return list of ItemRef."""
        ...

    @abstractmethod
    async def fetch_item(self, ref: ItemRef) -> ScrapedPost:
        """Run detail flow, return a ScrapedPost."""
        ...

    @abstractmethod
    async def fetch_comments(self, ref: ItemRef) -> list[ScrapedComment]:
        """Run comments flow, return list of ScrapedComment."""
        ...

    @abstractmethod
    async def login(self) -> dict:
        """Run login flow, return login result dict."""
        ...
