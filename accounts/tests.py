import re

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client

from accounts.models import Watchlist
from products.models import Category, Product


def _product(name="XPS 13"):
    category = Category.objects.create(name=f"Laptopy-{name}")
    return Product.objects.create(name=name, category=category, brand="Dell", model_name=name)


@pytest.mark.django_db
class TestRegistration:
    def test_get_shows_form(self):
        client = Client()

        response = client.get("/accounts/register/")

        assert response.status_code == 200
        assert "form" in response.context

    def test_valid_post_creates_and_logs_in_user(self):
        client = Client()

        response = client.post(
            "/accounts/register/",
            {
                "username": "newuser",
                "email": "newuser@example.com",
                "password1": "a-very-strong-pass123",
                "password2": "a-very-strong-pass123",
            },
        )

        assert response.status_code == 302
        assert get_user_model().objects.filter(username="newuser").exists()
        assert client.session.get("_auth_user_id") is not None

    def test_duplicate_username_rejected(self):
        get_user_model().objects.create_user(username="taken", password="x")
        client = Client()

        response = client.post(
            "/accounts/register/",
            {
                "username": "taken",
                "email": "other@example.com",
                "password1": "a-very-strong-pass123",
                "password2": "a-very-strong-pass123",
            },
        )

        assert response.status_code == 200
        assert get_user_model().objects.filter(username="taken").count() == 1

    def test_duplicate_email_rejected(self):
        get_user_model().objects.create_user(
            username="existing", password="x", email="dup@example.com"
        )
        client = Client()

        response = client.post(
            "/accounts/register/",
            {
                "username": "brandnew",
                "email": "dup@example.com",
                "password1": "a-very-strong-pass123",
                "password2": "a-very-strong-pass123",
            },
        )

        assert response.status_code == 200
        assert not get_user_model().objects.filter(username="brandnew").exists()

    def test_throttled_after_limit_reached(self, settings):
        settings.REGISTER_IP_RATE_PER_HOUR = 1
        client = Client()

        first = client.post(
            "/accounts/register/",
            {
                "username": "first",
                "email": "first@example.com",
                "password1": "a-very-strong-pass123",
                "password2": "a-very-strong-pass123",
            },
        )
        assert first.status_code == 302
        assert get_user_model().objects.filter(username="first").exists()

        second = client.post(
            "/accounts/register/",
            {
                "username": "second",
                "email": "second@example.com",
                "password1": "a-very-strong-pass123",
                "password2": "a-very-strong-pass123",
            },
        )
        assert not get_user_model().objects.filter(username="second").exists()


@pytest.mark.django_db
class TestLoginLogout:
    def test_valid_login_authenticates_session(self):
        get_user_model().objects.create_user(username="alice", password="secretpass123")
        client = Client()

        response = client.post("/accounts/login/", {"username": "alice", "password": "secretpass123"})

        assert response.status_code == 302
        assert client.session.get("_auth_user_id") is not None

    def test_logout_requires_post(self):
        user = get_user_model().objects.create_user(username="bob", password="secretpass123")
        client = Client()
        client.force_login(user)

        get_response = client.get("/accounts/logout/")

        assert get_response.status_code == 405
        assert client.session.get("_auth_user_id") is not None

        post_response = client.post("/accounts/logout/")

        assert post_response.status_code == 302
        assert client.session.get("_auth_user_id") is None


@pytest.mark.django_db
class TestWatchlist:
    def test_toggle_requires_login(self):
        product = _product()
        client = Client()

        response = client.post(f"/products/{product.id}/watchlist/")

        assert response.status_code == 302
        assert "/accounts/login/" in response.url
        assert not Watchlist.objects.exists()

    def test_toggle_adds_then_removes(self):
        product = _product()
        user = get_user_model().objects.create_user(username="carol", password="x")
        client = Client()
        client.force_login(user)

        first = client.post(f"/products/{product.id}/watchlist/")
        assert Watchlist.objects.filter(user=user, product=product).exists()
        assert first.status_code == 302

        second = client.post(f"/products/{product.id}/watchlist/")
        assert not Watchlist.objects.filter(user=user, product=product).exists()
        assert second.status_code == 302

    def test_watchlist_view_shows_only_own_products(self):
        product_mine = _product("Mine")
        product_other = _product("Other")
        user = get_user_model().objects.create_user(username="dave", password="x")
        other_user = get_user_model().objects.create_user(username="erin", password="x")
        Watchlist.objects.create(user=user, product=product_mine)
        Watchlist.objects.create(user=other_user, product=product_other)
        client = Client()
        client.force_login(user)

        response = client.get("/accounts/watchlist/")

        assert response.context["products"] == [product_mine]


@pytest.mark.django_db
class TestPasswordReset:
    def test_request_sends_email(self):
        get_user_model().objects.create_user(
            username="frank", password="OldPass123!", email="frank@example.com"
        )
        client = Client()

        response = client.post("/accounts/password-reset/", {"email": "frank@example.com"})

        assert response.status_code == 302
        assert len(mail.outbox) == 1
        assert "frank@example.com" in mail.outbox[0].to

    def test_unknown_email_does_not_leak_account_existence(self):
        client = Client()

        response = client.post("/accounts/password-reset/", {"email": "nobody@example.com"})

        assert response.status_code == 302
        assert len(mail.outbox) == 0

    def test_full_reset_flow_sets_new_password(self):
        user = get_user_model().objects.create_user(
            username="frank2", password="OldPass123!", email="frank2@example.com"
        )
        client = Client()

        client.post("/accounts/password-reset/", {"email": "frank2@example.com"})
        assert len(mail.outbox) == 1

        match = re.search(r"http://[^/]+(/accounts/reset/\S+/\S+/)", mail.outbox[0].body)
        assert match, mail.outbox[0].body
        reset_path = match.group(1)

        confirm_get = client.get(reset_path, follow=True)
        assert confirm_get.status_code == 200
        confirm_path = confirm_get.request["PATH_INFO"]

        post_response = client.post(
            confirm_path,
            {"new_password1": "BrandNewPass456!", "new_password2": "BrandNewPass456!"},
        )
        assert post_response.status_code == 302

        user.refresh_from_db()
        assert user.check_password("BrandNewPass456!")
        assert not user.check_password("OldPass123!")


@pytest.mark.django_db
class TestPasswordChange:
    def test_anonymous_is_redirected_to_login(self):
        client = Client()

        response = client.get("/accounts/password-change/")

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_valid_change_keeps_session_and_updates_password(self):
        user = get_user_model().objects.create_user(username="grace", password="OldPass123!")
        client = Client()
        client.force_login(user)

        response = client.post(
            "/accounts/password-change/",
            {
                "old_password": "OldPass123!",
                "new_password1": "NewPass456!",
                "new_password2": "NewPass456!",
            },
        )

        assert response.status_code == 302
        assert client.session.get("_auth_user_id") is not None  # still logged in
        user.refresh_from_db()
        assert user.check_password("NewPass456!")

    def test_wrong_old_password_rejected(self):
        user = get_user_model().objects.create_user(username="heidi", password="OldPass123!")
        client = Client()
        client.force_login(user)

        client.post(
            "/accounts/password-change/",
            {
                "old_password": "WrongPassword!",
                "new_password1": "NewPass456!",
                "new_password2": "NewPass456!",
            },
        )

        user.refresh_from_db()
        assert user.check_password("OldPass123!")


@pytest.mark.django_db
class TestChangeEmail:
    def test_anonymous_is_redirected_to_login(self):
        client = Client()

        response = client.get("/accounts/change-email/")

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_valid_unique_email_is_saved(self):
        user = get_user_model().objects.create_user(
            username="ivan", password="x", email="old@example.com"
        )
        client = Client()
        client.force_login(user)

        response = client.post("/accounts/change-email/", {"email": "new@example.com"})

        assert response.status_code == 302
        user.refresh_from_db()
        assert user.email == "new@example.com"

    def test_duplicate_email_rejected(self):
        get_user_model().objects.create_user(username="judy", password="x", email="taken@example.com")
        user = get_user_model().objects.create_user(
            username="kim", password="x", email="kim@example.com"
        )
        client = Client()
        client.force_login(user)

        response = client.post("/accounts/change-email/", {"email": "taken@example.com"})

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.email == "kim@example.com"


@pytest.mark.django_db
class TestProfile:
    def test_anonymous_is_redirected_to_login(self):
        client = Client()

        response = client.get("/accounts/profile/")

        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_shows_watchlist_and_review_counts(self):
        from reviews.models import UserReview

        product_a = _product("A")
        product_b = _product("B")
        user = get_user_model().objects.create_user(username="liam", password="x")
        Watchlist.objects.create(user=user, product=product_a)
        Watchlist.objects.create(user=user, product=product_b)
        UserReview.objects.create(product=product_a, user=user, rating=5)
        client = Client()
        client.force_login(user)

        response = client.get("/accounts/profile/")

        assert response.status_code == 200
        assert response.context["watchlist_count"] == 2
        assert response.context["review_count"] == 1
