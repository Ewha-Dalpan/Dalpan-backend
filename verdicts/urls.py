from django.urls import path
from . import views
urlpatterns = [
    path("requests/<int:request_id>/", views.VerdictRequestDetailView.as_view(), name="verdict-request-detail"),
    path("cases/<int:case_id>/", views.CaseVerdictDetailView.as_view(), name="case-verdict-detail"),
    path("cases/<int:case_id>/share/", views.CaseShareView.as_view(), name="case-verdict-share"),
    path("cases/<int:case_id>/publish/", views.CasePublishView.as_view(), name="case-verdict-publish"),
    path("shared/<uuid:token>/", views.SharedVerdictDetailView.as_view(), name="verdict-shared-detail"),
]
