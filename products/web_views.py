from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Avg, Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Watchlist
from analysis.models import SummaryFlag
from analysis.tasks import queue_ai_overview
from reviews.forms import UserReviewForm
from reviews.models import ScrapeJob, UserReview
from scrapers.tasks import queue_live_search_fetch

from .models import Category, Product
from .rate_limit import cost_path_allowed

# Cap on how many search results trigger a live YouTube/Google Places fetch,
# so one broad query (e.g. a single common letter) can't fan out into
# dozens of API calls at once -- covers the top results, not the full match set.
MAX_LIVE_FETCH_PRODUCTS = 5

# Minimum length for a search query to be considered for auto-creating a new
# Product (see _is_meaningful_query) -- below this it's almost certainly a
# typo/probe, not worth spending API quota on.
MIN_MEANINGFUL_QUERY_LENGTH = 3


def _is_meaningful_query(query: str) -> bool:
    """
    Cheap sanity filter before auto-creating a Product from a search term:
    rejects too-short queries and single-character-repeated junk (e.g.
    "aaaaaa"). This is NOT real gibberish/spam detection -- that's not
    reliably solvable -- just a low-cost filter for the most obvious junk.
    """
    stripped = query.strip()
    if len(stripped) < MIN_MEANINGFUL_QUERY_LENGTH:
        return False
    return len(set(stripped.lower())) > 1


def _resolve_category_for_new_product(category_slug: str, main_category: str):
    """
    Category to attach an auto-created stub Product to, when a search
    matches nothing in the catalog. Only returns one when the search itself
    carried category context (a specific category or at least a main
    category) -- with neither filter set there's no sane way to guess which
    category a typed product name belongs to, so callers must not auto-create.
    """
    if category_slug:
        return Category.objects.filter(slug=category_slug).first()

    if main_category:
        try:
            label = Category.MainCategory(main_category).label
        except ValueError:
            return None
        category, _ = Category.objects.get_or_create(
            name=f"Inne ({label})", defaults={"main_category": main_category}
        )
        return category

    return None


def _get_or_create_stub_product(query: str, category: Category) -> Product:
    """
    Minimal Product for a search term that doesn't match the catalog, so a
    live YouTube/Google Places fetch has something to attach reviews to.
    brand/model_name are a rough split of the query, not curated data --
    just enough for reddit_scraper/youtube_scraper/etc. to search on.
    """
    parts = query.split(None, 1)
    brand = parts[0][:100]
    model_name = (parts[1] if len(parts) > 1 else query)[:100]
    try:
        return Product.objects.create(
            name=query, category=category, brand=brand, model_name=model_name
        )
    except IntegrityError:
        # brand+model_name already exists under a different category (rare,
        # e.g. the same words searched earlier under a different filter) --
        # reuse it rather than fail the search.
        return Product.objects.filter(brand=brand, model_name=model_name).select_related(
            "category"
        ).first()


def product_search(request):
    """Public search view: free catalog search, plus a cost-gated auto-create/live-fetch path."""
    query = request.GET.get("q", "").strip()
    category_slug = request.GET.get("category", "").strip()
    main_category = request.GET.get("main_category", "").strip()

    # Honeypot: a hidden form field real users never fill in (see
    # templates/base.html's search form) and some bots do. Filling it
    # silently disables the cost path for this request -- plain search
    # still works, so the bot has no obvious signal it was caught.
    is_honeypot_triggered = bool(request.GET.get("website"))

    products = Product.objects.select_related("category").all()

    if query:
        products = products.filter(
            Q(name__icontains=query)
            | Q(brand__icontains=query)
            | Q(model_name__icontains=query)
            | Q(category__name__icontains=query)
        )

    if category_slug:
        products = products.filter(category__slug=category_slug)

    if main_category:
        products = products.filter(category__main_category=main_category)

    # Auto-creating a Product and live-fetching YouTube/Google Places both
    # cost real external-API quota, unlike plain search over the existing
    # catalog (always free, always public, never gated below). Only
    # relevant for a non-empty query (an empty query never triggers either
    # mechanism), and computed at most once per request -- cost_path_allowed()
    # increments rate-limit counters as a side effect, so it must not be
    # called more than once per request or per-request costs would be
    # double-counted, and must not be called at all for a request that was
    # never going to spend anything anyway.
    cost_path_ok = bool(query) and not is_honeypot_triggered and cost_path_allowed(request)

    auto_created_product = None
    cost_path_limited = False
    if query and not products.exists() and (category_slug or main_category):
        if not _is_meaningful_query(query):
            pass  # too short/junk -- never worth auto-creating, no need to burn rate-limit budget
        elif not cost_path_ok:
            cost_path_limited = True
        else:
            category = _resolve_category_for_new_product(category_slug, main_category)
            if category is not None:
                auto_created_product = _get_or_create_stub_product(query, category)
                products = Product.objects.filter(pk=auto_created_product.pk).select_related(
                    "category"
                )

    if query and cost_path_ok:
        for product in products[:MAX_LIVE_FETCH_PRODUCTS]:
            queue_live_search_fetch(product)

    context = {
        "query": query,
        "selected_category": category_slug,
        "selected_main_category": main_category,
        "main_categories": Category.MainCategory.choices,
        "categories": Category.objects.order_by("main_category", "name"),
        "products": products,
        "result_count": products.count(),
        "auto_created_product": auto_created_product,
        "cost_path_limited": cost_path_limited,
    }
    return render(request, "products/search.html", context)


def product_detail(request, pk):
    """Product page: reviews by source tab, AI overview, watchlist/rating/flag state for the viewer."""
    product = get_object_or_404(Product.objects.select_related("category"), pk=pk)
    reviews = product.reviews.select_related("source").all()[:50]
    youtube_reviews = [r for r in reviews if r.source.parser_key == "youtube"]
    google_places_reviews = [r for r in reviews if r.source.parser_key == "google_places"]

    active_jobs = ScrapeJob.objects.filter(
        product=product, status__in=[ScrapeJob.Status.PENDING, ScrapeJob.Status.RUNNING]
    ).select_related("source")
    fetching_sources = [job.source.name for job in active_jobs]

    ai_overview_pending = False
    if not product.ai_overview:
        if not product.ai_overview_requested_at:
            queue_ai_overview(product)
        ai_overview_pending = True

    is_watched = (
        request.user.is_authenticated
        and Watchlist.objects.filter(user=request.user, product=product).exists()
    )

    user_reviews = product.user_reviews.select_related("user")[:50]
    rating_agg = product.user_reviews.aggregate(avg_rating=Avg("rating"), review_count=Count("id"))
    my_review = None
    if request.user.is_authenticated:
        my_review = UserReview.objects.filter(user=request.user, product=product).first()
    review_form = UserReviewForm(instance=my_review)

    context = {
        "product": product,
        "reviews": reviews,
        "youtube_reviews": youtube_reviews,
        "google_places_reviews": google_places_reviews,
        "fetching_sources": fetching_sources,
        "ai_overview_pending": ai_overview_pending,
        "needs_refresh": bool(fetching_sources) or ai_overview_pending,
        "is_watched": is_watched,
        "user_reviews": user_reviews,
        "avg_rating": rating_agg["avg_rating"],
        "review_count": rating_agg["review_count"],
        "my_review": my_review,
        "review_form": review_form,
    }
    return render(request, "products/detail.html", context)


@login_required
@require_POST
def toggle_watchlist(request, pk):
    """Add the product to the current user's watchlist, or remove it if already there."""
    product = get_object_or_404(Product, pk=pk)
    watchlist_entry, created = Watchlist.objects.get_or_create(user=request.user, product=product)
    if not created:
        watchlist_entry.delete()
        messages.success(request, "Usunięto z obserwowanych.")
    else:
        messages.success(request, "Dodano do obserwowanych.")
    return redirect("products:detail", pk=pk)


@login_required
@require_POST
def submit_user_review(request, pk):
    """Create or update (upsert) the current user's own rating+comment for this product."""
    product = get_object_or_404(Product, pk=pk)
    existing = UserReview.objects.filter(user=request.user, product=product).first()
    form = UserReviewForm(request.POST, instance=existing)
    if form.is_valid():
        review = form.save(commit=False)
        review.product = product
        review.user = request.user
        review.save()
        messages.success(request, "Zapisano Twoją ocenę.")
    else:
        messages.error(request, "Nie udało się zapisać oceny — sprawdź, czy ocena jest w zakresie 1-5.")
    return redirect("products:detail", pk=pk)


@login_required
@require_POST
def flag_summary(request, pk):
    """Report pros_summary or cons_summary as inaccurate (idempotent per user+product+target)."""
    product = get_object_or_404(Product, pk=pk)
    target = request.POST.get("target")
    if target not in SummaryFlag.Target.values:
        return HttpResponseBadRequest("Nieprawidłowa wartość 'target'.")
    _, created = SummaryFlag.objects.get_or_create(product=product, user=request.user, target=target)
    if created:
        messages.success(request, "Dziękujemy za zgłoszenie.")
    else:
        messages.info(request, "Już zgłoszono wcześniej.")
    return redirect("products:detail", pk=pk)
