"""Views orchestrating the enhanced leave management workflow."""
from __future__ import annotations

import calendar
import csv
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, Iterable, List, Tuple

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import ValidationError
from django.forms import modelformset_factory
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import FormView, TemplateView

from .forms import (
    AllocationSelectionForm,
    AnalyticsFilterForm,
    LeaveRequestForm,
    ManagerDecisionForm,
    QuotaAdjustmentForm,
)
from .models import (
    CompanyHoliday,
    LeaveApproval,
    LeaveBalance,
    LeaveQuota,
    LeaveRequest,
    LeaveType,
)
from .notifications import (
    notify_next_approver,
    notify_request_approved,
    notify_request_rejected,
    notify_request_submitted,
)
from .utils import generate_pdf_report


def _staff_check(user) -> bool:
    return user.is_staff


class StaffRequiredMixin(UserPassesTestMixin):
    """Gatekeeper for staff-only views."""

    def test_func(self):
        return _staff_check(self.request.user)

    def handle_no_permission(self):
        messages.error(self.request, "Staff access required for this section.")
        return redirect("leave_requests:dashboard")


class DashboardView(LoginRequiredMixin, TemplateView):
    """Employees land here to review balances, requests, and policy notes."""

    template_name = "leave_requests/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        balance = LeaveBalance.ensure_for_user(self.request.user)
        quotas = balance.quotas.select_related("leave_type").all()
        requests = (
            self.request.user.leave_requests.select_related("reviewed_by", "leave_type")
            .prefetch_related("approvals")
            .all()
        )
        context.update(
            {
                "balance": balance,
                "quotas": quotas,
                "pending_requests": requests.filter(
                    status__in=[LeaveRequest.Status.PENDING, LeaveRequest.Status.IN_REVIEW]
                ),
                "approved_requests": requests.filter(status=LeaveRequest.Status.APPROVED),
                "rejected_requests": requests.filter(status=LeaveRequest.Status.REJECTED),
                "available_days": balance.remaining_days,
            }
        )
        return context


class ApplyForLeaveView(LoginRequiredMixin, FormView):
    """Handles the apply-for-leave form flow."""

    template_name = "leave_requests/apply.html"
    form_class = LeaveRequestForm
    success_url = reverse_lazy("leave_requests:dashboard")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        balance = LeaveBalance.ensure_for_user(self.request.user)
        context.update(
            {
                "balance": balance,
                "quota_map": {quota.leave_type.id: quota for quota in balance.quotas.select_related("leave_type")},
            }
        )
        return context

    def form_valid(self, form: LeaveRequestForm):
        leave_request = form.save()
        notify_request_submitted(leave_request)
        messages.success(
            self.request,
            f"{leave_request.leave_type.name} request for {leave_request.total_days} day(s) submitted for review.",
        )
        return super().form_valid(form)


class ManagerDashboardView(StaffRequiredMixin, LoginRequiredMixin, TemplateView):
    """Managers triage and act on pending leave approvals here."""

    template_name = "leave_requests/manager_dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        pending_approvals = (
            LeaveApproval.objects.select_related("request", "request__user", "request__leave_type")
            .for_user(self.request.user)
            .filter(status=LeaveApproval.Status.PENDING)
            .order_by("request__start_date", "sequence")
        )
        recent_decisions = (
            LeaveApproval.objects.select_related("request", "request__user", "reviewed_by")
            .filter(reviewed_by=self.request.user)
            .order_by("-decided_at")[:10]
        )
        context.update(
            {
                "pending_approvals": pending_approvals,
                "recent_decisions": recent_decisions,
                "decision_form": ManagerDecisionForm(),
            }
        )
        return context


@login_required
@user_passes_test(_staff_check, login_url="leave_requests:dashboard")
def review_leave_approval(request, pk: int, action: str):
    """Approve or reject an individual approval step."""

    approval = get_object_or_404(
        LeaveApproval.objects.select_related("request", "request__user", "request__leave_type"), pk=pk
    )
    if not approval.is_user_eligible(request.user):
        messages.error(request, "You are not authorised to act on this approval.")
        return redirect("leave_requests:manager_dashboard")

    decision_map = {
        "approve": LeaveApproval.Status.APPROVED,
        "reject": LeaveApproval.Status.REJECTED,
    }
    decision = decision_map.get(action)
    if not decision:
        messages.error(request, "Unknown approval action.")
        return redirect("leave_requests:manager_dashboard")

    form = ManagerDecisionForm(request.POST or None)
    if request.method != "POST" or not form.is_valid():
        messages.error(request, "Submit your decision using the provided form.")
        return redirect("leave_requests:manager_dashboard")

    comment = form.cleaned_data["manager_comment"]
    if decision == LeaveApproval.Status.REJECTED and not comment:
        messages.error(request, "Please provide a comment when rejecting a request.")
        return redirect("leave_requests:manager_dashboard")

    try:
        outcome = approval.request.record_decision(request.user, decision, comment)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("leave_requests:manager_dashboard")

    if outcome == LeaveRequest.Status.APPROVED:
        notify_request_approved(approval.request)
        messages.success(
            request,
            f"Approved {approval.request.user.get_username()}'s {approval.request.leave_type.name} request.",
        )
    elif outcome == LeaveRequest.Status.REJECTED:
        notify_request_rejected(approval.request)
        messages.warning(
            request,
            f"Rejected {approval.request.user.get_username()}'s {approval.request.leave_type.name} request.",
        )
    else:
        next_step = approval.request.current_approval
        if next_step:
            notify_next_approver(next_step)
        messages.success(
            request,
            f"Recorded your approval. {approval.request.leave_type.name} request is awaiting the next reviewer.",
        )

    return redirect("leave_requests:manager_dashboard")


class CalendarView(LoginRequiredMixin, TemplateView):
    """Visualise approved leave alongside company holidays."""

    template_name = "leave_requests/calendar.html"

    def get_month_year(self) -> Tuple[int, int]:
        today = date.today()
        try:
            year = int(self.request.GET.get("year", today.year))
            month = int(self.request.GET.get("month", today.month))
        except ValueError:
            year, month = today.year, today.month
        month = max(1, min(12, month))
        return year, month

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        year, month = self.get_month_year()
        cal = calendar.Calendar(firstweekday=0)
        days_iter = list(cal.itermonthdates(year, month))
        first_visible_day = days_iter[0]
        last_visible_day = days_iter[-1]

        events_by_day: Dict[date, List[dict]] = defaultdict(list)
        holidays = CompanyHoliday.objects.filter(date__range=(first_visible_day, last_visible_day))
        for holiday in holidays:
            events_by_day[holiday.date].append({"type": "holiday", "label": holiday.name})

        approved_requests = LeaveRequest.objects.filter(
            status=LeaveRequest.Status.APPROVED,
            start_date__lte=last_visible_day,
            end_date__gte=first_visible_day,
        ).select_related("user", "leave_type")
        for request_obj in approved_requests:
            day = request_obj.start_date
            while day <= request_obj.end_date:
                if first_visible_day <= day <= last_visible_day:
                    events_by_day[day].append(
                        {
                            "type": "leave",
                            "label": f"{request_obj.user.get_username()} · {request_obj.leave_type.code}",
                        }
                    )
                day += timedelta(days=1)

        weeks = []
        week = []
        for day in days_iter:
            week.append(
                {
                    "date": day,
                    "in_month": day.month == month,
                    "events": events_by_day.get(day, []),
                }
            )
            if len(week) == 7:
                weeks.append(week)
                week = []

        month_name = calendar.month_name[month]
        context.update(
            {
                "weeks": weeks,
                "year": year,
                "month": month,
                "month_name": month_name,
                "previous_month_url": self._adjacent_month_url(year, month, direction=-1),
                "next_month_url": self._adjacent_month_url(year, month, direction=1),
            }
        )
        return context

    def _adjacent_month_url(self, year: int, month: int, direction: int) -> str:
        month += direction
        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1
        return f"{reverse('leave_requests:calendar')}?year={year}&month={month}"


class AllocationManagementView(StaffRequiredMixin, LoginRequiredMixin, TemplateView):
    """Allows managers/HR to adjust employee leave quotas."""

    template_name = "leave_requests/manage_allocations.html"
    quota_formset_class = modelformset_factory(
        LeaveQuota,
        form=QuotaAdjustmentForm,
        extra=0,
    )

    def get_employee_id(self) -> str | None:
        return self.request.GET.get("employee") or self.request.POST.get("employee")

    def get(self, request, *args, **kwargs):
        employee_id = self.get_employee_id()
        select_form = AllocationSelectionForm(request.GET or None)
        quota_formset = None
        employee_balance = None

        if employee_id and select_form.is_valid():
            employee = select_form.cleaned_data["employee"]
            employee_balance = LeaveBalance.ensure_for_user(employee)
            quota_formset = self.quota_formset_class(
                queryset=employee_balance.quotas.select_related("leave_type"),
            )

        context = self.get_context_data(
            select_form=select_form,
            quota_formset=quota_formset,
            employee_balance=employee_balance,
        )
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        select_form = AllocationSelectionForm(request.POST)
        if not select_form.is_valid():
            messages.error(request, "Please choose a valid employee.")
            return self.render_to_response(
                self.get_context_data(select_form=select_form, quota_formset=None, employee_balance=None)
            )

        employee = select_form.cleaned_data["employee"]
        balance = LeaveBalance.ensure_for_user(employee)
        quota_formset = self.quota_formset_class(
            request.POST,
            queryset=balance.quotas.select_related("leave_type"),
        )
        if quota_formset.is_valid():
            quota_formset.save()
            balance.last_adjusted_by = request.user
            balance.last_adjusted_at = timezone.now()
            balance.emergency_days = sum(quota.emergency_grant for quota in balance.quotas.all())
            balance.save(update_fields=["last_adjusted_by", "last_adjusted_at", "emergency_days"])
            messages.success(request, f"Updated allocations for {employee.get_username()}.")
            redirect_url = f"{reverse('leave_requests:manage_allocations')}?employee={employee.pk}"
            return redirect(redirect_url)

        messages.error(request, "Please correct the highlighted allocation fields.")
        context = self.get_context_data(
            select_form=select_form,
            quota_formset=quota_formset,
            employee_balance=balance,
        )
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(kwargs)
        return context


def _derive_period(filters: dict) -> Tuple[date, date]:
    today = date.today()
    year = filters.get("year") or today.year
    month = filters.get("month")
    if month:
        start = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        end = date(year, month, last_day)
    else:
        start = date(year, 1, 1)
        end = date(year, 12, 31)
    return start, end


def _build_analytics_dataset(filters: dict) -> dict:
    start, end = _derive_period(filters)
    queryset = (
        LeaveRequest.objects.filter(
            status=LeaveRequest.Status.APPROVED,
            start_date__lte=end,
            end_date__gte=start,
        )
        .select_related("leave_type", "user")
        .order_by("start_date")
    )

    type_totals: Dict[str, int] = defaultdict(int)
    user_totals: Dict[str, int] = defaultdict(int)
    monthly_totals: Dict[str, int] = defaultdict(int)
    total_days = 0

    for request_obj in queryset:
        days = request_obj.total_days
        type_totals[request_obj.leave_type.name] += days
        display_name = request_obj.user.get_full_name() or request_obj.user.get_username()
        user_totals[display_name] += days
        month_key = request_obj.start_date.strftime("%Y-%m")
        monthly_totals[month_key] += days
        total_days += days

    return {
        "requests": queryset,
        "type_totals": sorted(type_totals.items(), key=lambda item: item[0]),
        "user_totals": sorted(user_totals.items(), key=lambda item: item[0]),
        "monthly_totals": sorted(monthly_totals.items(), key=lambda item: item[0]),
        "total_days": total_days,
        "period_start": start,
        "period_end": end,
    }


class AnalyticsDashboardView(StaffRequiredMixin, LoginRequiredMixin, TemplateView):
    """Aggregated insights for HR and leadership."""

    template_name = "leave_requests/analytics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filter_form = AnalyticsFilterForm(self.request.GET or None)
        filters = {}
        if filter_form.is_valid():
            filters = filter_form.cleaned_data
        dataset = _build_analytics_dataset(filters)
        context.update(dataset)
        context["filter_form"] = filter_form
        context["filters"] = {k: v for k, v in filters.items() if v}
        context["export_params"] = self.request.GET.urlencode()
        return context


class AnalyticsExportView(StaffRequiredMixin, LoginRequiredMixin, View):
    """Exports analytics data as CSV or PDF."""

    format: str = "csv"

    def get(self, request, *args, **kwargs):
        filter_form = AnalyticsFilterForm(request.GET or None)
        filters = {}
        if filter_form.is_valid():
            filters = filter_form.cleaned_data
        dataset = _build_analytics_dataset(filters)

        if self.format == "csv":
            return self._export_csv(dataset)
        if self.format == "pdf":
            return self._export_pdf(dataset)
        messages.error(request, "Unsupported export format.")
        return redirect("leave_requests:analytics")

    def _export_csv(self, dataset: dict) -> HttpResponse:
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=leave-analytics.csv"
        writer = csv.writer(response)
        writer.writerow(["Leave Analytics Report"])
        writer.writerow(
            [
                f"Period: {dataset['period_start']} - {dataset['period_end']}",
            ]
        )
        writer.writerow([])
        writer.writerow(["Breakdown by Leave Type"])
        writer.writerow(["Leave Type", "Total Days"])
        for leave_type, days in dataset["type_totals"]:
            writer.writerow([leave_type, days])
        writer.writerow([])
        writer.writerow(["Breakdown by Employee"])
        writer.writerow(["Employee", "Total Days"])
        for employee, days in dataset["user_totals"]:
            writer.writerow([employee, days])
        writer.writerow([])
        writer.writerow(["Monthly Totals"])
        writer.writerow(["Month", "Total Days"])
        for month, days in dataset["monthly_totals"]:
            writer.writerow([month, days])
        writer.writerow([])
        writer.writerow(["Detailed Requests"])
        writer.writerow(["Employee", "Leave Type", "Start", "End", "Days"])
        for request_obj in dataset["requests"]:
            writer.writerow(
                [
                    request_obj.user.get_username(),
                    request_obj.leave_type.name,
                    request_obj.start_date,
                    request_obj.end_date,
                    request_obj.total_days,
                ]
            )
        return response

    def _export_pdf(self, dataset: dict) -> HttpResponse:
        lines = [
            f"Leave analytics report: {dataset['period_start']} to {dataset['period_end']}",
            "",
            "Breakdown by leave type:",
        ]
        for leave_type, days in dataset["type_totals"]:
            lines.append(f"  • {leave_type}: {days} day(s)")
        lines.append("")
        lines.append("Breakdown by employee:")
        for employee, days in dataset["user_totals"]:
            lines.append(f"  • {employee}: {days} day(s)")
        lines.append("")
        lines.append("Monthly totals:")
        for month, days in dataset["monthly_totals"]:
            lines.append(f"  • {month}: {days} day(s)")
        lines.append("")
        lines.append("Detailed requests:")
        for request_obj in dataset["requests"]:
            lines.append(
                f"{request_obj.user.get_username()} · {request_obj.leave_type.code} · "
                f"{request_obj.start_date} → {request_obj.end_date} ({request_obj.total_days} day(s))"
            )
        pdf_bytes = generate_pdf_report("Leave Analytics", lines)
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = "attachment; filename=leave-analytics.pdf"
        response.write(pdf_bytes)
        return response
