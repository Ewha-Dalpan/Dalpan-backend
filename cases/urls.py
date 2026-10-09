from django.urls import path
from . import views

urlpatterns = [
    path('', views.CaseListCreateView.as_view(), name='case-list'),
    path('<int:case_id>/', views.CaseDetailView.as_view(), name='case-detail'),
    path('<int:case_id>/payment/', views.CasePaymentView.as_view(), name='case-payment'),
    path('<int:case_id>/submit/', views.CaseSubmitView.as_view(), name='case-submit'),
    path('<int:case_id>/situation/', views.SituationView.as_view(), name='case-situation'),
    path('<int:case_id>/judgment/', views.JudgmentView.as_view(), name='case-judgment'),
]
