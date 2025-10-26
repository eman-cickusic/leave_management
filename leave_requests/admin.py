"""Admin configuration for leave management."""
from django.contrib import admin

from .models import (
    CompanyHoliday,
    Department,
    DepartmentApprovalRule,
    LeaveApproval,
    LeaveBalance,
    LeaveQuota,
    LeaveRequest,
    LeaveType,
)


class LeaveQuotaInline(admin.TabularInline):
    model = LeaveQuota
    extra = 0
    readonly_fields = ("used",)


class DepartmentApprovalInline(admin.TabularInline):
    model = DepartmentApprovalRule
    extra = 0


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "team_lead", "hr_approver")
    search_fields = ("name", "team_lead__username", "hr_approver__username")
    autocomplete_fields = ("team_lead", "hr_approver")
    inlines = [DepartmentApprovalInline]


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "default_allocation", "max_days_per_request", "min_notice_days", "is_paid")
    search_fields = ("name", "code")


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "leave_type",
        "start_date",
        "end_date",
        "status",
        "reviewed_by",
        "decision_date",
    )
    list_filter = ("status", "leave_type", "start_date")
    search_fields = ("user__username", "reason")
    autocomplete_fields = ("user", "reviewed_by", "department", "leave_type")
    readonly_fields = ("created_at", "updated_at", "decision_date", "policy_notes")
    ordering = ("-created_at",)


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "department",
        "remaining_days_display",
        "emergency_days",
        "last_adjusted_by",
        "last_adjusted_at",
    )
    search_fields = ("user__username",)
    autocomplete_fields = ("user", "department", "last_adjusted_by")
    inlines = [LeaveQuotaInline]

    def remaining_days_display(self, obj):
        return obj.remaining_days

    remaining_days_display.short_description = "Remaining days"


@admin.register(LeaveApproval)
class LeaveApprovalAdmin(admin.ModelAdmin):
    list_display = ("request", "role", "sequence", "status", "assigned_to", "reviewed_by", "decided_at")
    list_filter = ("status", "role")
    search_fields = ("request__user__username", "request__leave_type__name")
    autocomplete_fields = ("request", "assigned_to", "reviewed_by")


@admin.register(CompanyHoliday)
class CompanyHolidayAdmin(admin.ModelAdmin):
    list_display = ("name", "date")
    ordering = ("date",)
    search_fields = ("name",)
