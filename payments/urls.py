from django.urls import path

from .views import OrderCreateView, OrderDetailView, ProductListView, paddle_webhook

urlpatterns = [
    path("products/", ProductListView.as_view(), name="payment-products"),
    path("orders/", OrderCreateView.as_view(), name="payment-orders"),
    path("orders/<uuid:order_id>/", OrderDetailView.as_view(), name="payment-order-detail"),
    path("webhook/", paddle_webhook, name="paddle-webhook"),
]