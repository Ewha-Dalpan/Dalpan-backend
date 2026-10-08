from django.urls import path
from .views import VerdictRequestDetailView

urlpatterns = [
    path('requests/<int:request_id>/', VerdictRequestDetailView.as_view(), name='verdict-request-detail'),
]
