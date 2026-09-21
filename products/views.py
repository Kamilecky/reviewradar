from urllib.parse import urlparse

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from reviews.models import ReviewSource
from scrapers.tasks import fetch_crawlbase_reddit_post as fetch_crawlbase_reddit_post_task
from scrapers.tasks import queue_fetch

from .models import Category, Product
from .serializers import CategorySerializer, ProductDetailSerializer, ProductListSerializer


def _is_reddit_url(url: str) -> bool:
    """
    Whitelists reddit.com (and subdomains, e.g. www./old.) so
    fetch-crawlbase-reddit-post can't be used as an open proxy to scrape an
    arbitrary URL through Crawlbase on this project's dime.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    netloc = parsed.netloc.lower()
    return netloc == "reddit.com" or netloc.endswith(".reddit.com")


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only, public REST endpoint for browsing Category rows."""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only, public REST endpoint for Product, plus two staff-only fetch-trigger actions."""

    queryset = Product.objects.select_related("category").all()
    filterset_fields = ["category", "brand", "category__main_category"]
    # APIView itself doesn't define this attribute -- ViewSet.as_view()'s
    # hasattr() check on extra @action kwargs would raise TypeError for
    # throttle_scope=... below without it being declared here first.
    throttle_scope = None

    def get_serializer_class(self):
        """Lighter serializer for list (category as slug), full detail otherwise."""
        if self.action == "list":
            return ProductListSerializer
        return ProductDetailSerializer

    @action(
        detail=True,
        methods=["post"],
        url_path="refresh-reviews",
        permission_classes=[IsAdminUser],
        throttle_classes=[ScopedRateThrottle],
        throttle_scope="refresh-reviews",
    )
    def refresh_reviews(self, request, pk=None):
        """Manually (re)trigger review fetching for this product across sources matching its category."""
        product = self.get_object()
        active_sources = [
            source
            for source in ReviewSource.objects.filter(is_active=True)
            if source.applies_to(product.category.main_category)
        ]
        if not active_sources:
            return Response(
                {"detail": "No active review sources configured for this product's category."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queued = []
        for source in active_sources:
            queue_fetch(source, product)
            queued.append(source.name)

        return Response({"queued_sources": queued}, status=status.HTTP_202_ACCEPTED)

    @action(
        detail=True,
        methods=["post"],
        url_path="fetch-crawlbase-reddit-post",
        permission_classes=[IsAdminUser],
        throttle_classes=[ScopedRateThrottle],
        throttle_scope="crawlbase-fetch",
    )
    def fetch_crawlbase_reddit_post(self, request, pk=None):
        """Fetch one specific Reddit post URL via Crawlbase for this product."""
        product = self.get_object()
        url = request.data.get("url")
        if not url:
            return Response(
                {"detail": "'url' is required."}, status=status.HTTP_400_BAD_REQUEST
            )
        if not _is_reddit_url(url):
            return Response(
                {"detail": "'url' must point to reddit.com."}, status=status.HTTP_400_BAD_REQUEST
            )

        source = ReviewSource.objects.filter(
            parser_key="crawlbase_reddit", is_active=True
        ).first()
        if source is None:
            return Response(
                {"detail": "No active ReviewSource configured for parser_key='crawlbase_reddit'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fetch_crawlbase_reddit_post_task.delay(product.id, source.id, url)
        return Response({"queued_source": source.name}, status=status.HTTP_202_ACCEPTED)
