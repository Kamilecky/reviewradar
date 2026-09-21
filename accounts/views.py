from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from products.rate_limit import increment_and_check

from .forms import EmailChangeForm, RegistrationForm


def _register_allowed(request) -> bool:
    """Per-IP, per-hour registration throttle, reusing products.rate_limit's shared counter."""
    ip = request.META.get("REMOTE_ADDR", "unknown")
    key = f"register:ip:{ip}:{timezone.now():%Y%m%d%H}"
    return increment_and_check(key, settings.REGISTER_IP_RATE_PER_HOUR, 3600)


def register(request):
    """Registration form: creates a User, logs them in immediately, and redirects to search."""
    if request.method == "POST":
        if not _register_allowed(request):
            messages.error(
                request, "Zbyt wiele prób rejestracji z tego adresu. Spróbuj ponownie później."
            )
            return redirect("accounts:register")
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, f"Witaj, {user.username}! Konto zostało utworzone.")
            return redirect("products:search")
    else:
        form = RegistrationForm()
    return render(request, "accounts/register.html", {"form": form})


@login_required
def watchlist_view(request):
    """List the current user's watched products."""
    entries = request.user.watchlist.select_related("product__category")
    products = [entry.product for entry in entries]
    return render(request, "accounts/watchlist.html", {"products": products})


@login_required
def profile_view(request):
    """Account summary page: watched-product count and own review count."""
    context = {
        "watchlist_count": request.user.watchlist.count(),
        "review_count": request.user.product_reviews.count(),
    }
    return render(request, "accounts/profile.html", context)


@login_required
def change_email(request):
    """Let the logged-in user change their own email address (no re-authentication required)."""
    if request.method == "POST":
        form = EmailChangeForm(request.POST, user=request.user)
        if form.is_valid():
            request.user.email = form.cleaned_data["email"]
            request.user.save(update_fields=["email"])
            messages.success(request, "Adres e-mail został zaktualizowany.")
            return redirect("accounts:profile")
    else:
        form = EmailChangeForm(user=request.user, initial={"email": request.user.email})
    return render(request, "accounts/change_email.html", {"form": form})
