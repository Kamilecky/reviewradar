from rest_framework import viewsets

from .models import Review
from .serializers import ReviewSerializer


class ReviewViewSet(viewsets.ReadOnlyModelViewSet):
    """
    List/detail for reviews. Supports filtering via query params, e.g.:
    /api/reviews/?product=1&sentiment=positive&source=2
    """

    queryset = Review.objects.select_related("source", "product").all()
    serializer_class = ReviewSerializer
    filterset_fields = ["product", "sentiment", "source"]
