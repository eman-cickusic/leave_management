"""URL routing for leave request flows."""
from django.urls import path

from . import views

app_name = "leave_requests"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("apply/", views.ApplyForLeaveView.as_view(), name="apply"),
    path("calendar/", views.CalendarView.as_view(), name="calendar"),
    path(
        "manager/",
        views.ManagerDashboardView.as_view(),
        name="manager_dashboard",
    ),
    path(
        "manager/approval/<int:pk>/<str:action>/",
        views.review_leave_approval,
        name="review_approval",
    ),
    path(
        "manager/allocations/",
        views.AllocationManagementView.as_view(),
        name="manage_allocations",
    ),
    path(
        "manager/analytics/",
        views.AnalyticsDashboardView.as_view(),
        name="analytics",
    ),
    path(
        "manager/analytics/export/csv/",
        views.AnalyticsExportView.as_view(format="csv"),
        name="analytics_export_csv",
    ),
    path(
        "manager/analytics/export/pdf/",
        views.AnalyticsExportView.as_view(format="pdf"),
        name="analytics_export_pdf",
    ),
]
