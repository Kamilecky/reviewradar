from django.urls import path

from . import web_views

app_name = "products"

urlpatterns = [
    path("", web_views.product_search, name="search"),
    path("products/<int:pk>/", web_views.product_detail, name="detail"),
    path("products/<int:pk>/watchlist/", web_views.toggle_watchlist, name="toggle_watchlist"),
    path("products/<int:pk>/review/", web_views.submit_user_review, name="submit_review"),
    path("products/<int:pk>/flag-summary/", web_views.flag_summary, name="flag_summary"),
]
