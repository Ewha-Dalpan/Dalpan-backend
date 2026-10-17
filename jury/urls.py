from django.urls import path

from . import views

urlpatterns = [
    path("me/activities/", views.MyJuryActivityView.as_view(), name="jury-my-activities"),
    path("cases/", views.JuryCaseListView.as_view(), name="jury-case-list"),
    path("cases/<int:case_id>/", views.JuryCaseDetailView.as_view(), name="jury-case-detail"),
    path("cases/<int:case_id>/votes/", views.JuryVoteView.as_view(), name="jury-vote"),
    path("cases/<int:case_id>/comments/", views.JuryCommentListCreateView.as_view(), name="jury-comment-list"),
    path("cases/<int:case_id>/comments/<int:comment_id>/", views.JuryCommentDetailView.as_view(), name="jury-comment-detail"),
    path("cases/<int:case_id>/images/<int:image_id>/", views.JuryCaseImageView.as_view(), name="jury-case-image"),
    path("cases/<int:case_id>/reports/", views.JuryCaseReportView.as_view(), name="jury-case-report"),
    path("cases/<int:case_id>/comments/<int:comment_id>/reports/", views.JuryCommentReportView.as_view(), name="jury-comment-report"),
]