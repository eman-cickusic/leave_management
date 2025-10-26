"""Forms supporting the leave management workflow."""
from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth import get_user_model

from .models import LeaveBalance, LeaveQuota, LeaveRequest, LeaveType

User = get_user_model()


class LeaveRequestForm(forms.ModelForm):
    """Form an employee uses to submit a new leave request."""

    def __init__(self, *args: Any, user, **kwargs: Any) -> None:
        self.request_user = user
        super().__init__(*args, **kwargs)
        self.fields["leave_type"].queryset = LeaveType.objects.all()
        self.fields["leave_type"].empty_label = None
        self.fields["start_date"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["end_date"].widget = forms.DateInput(attrs={"type": "date"})
        self.fields["reason"].widget = forms.Textarea(attrs={"rows": 3})
        self.balance = LeaveBalance.ensure_for_user(user)

    class Meta:
        model = LeaveRequest
        fields = ["leave_type", "start_date", "end_date", "reason"]

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        leave_type = cleaned.get("leave_type")
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if not leave_type or not start or not end:
            return cleaned

        duration = (end - start).days + 1
        overlapping = LeaveRequest.objects.overlapping(self.request_user, start, end).exclude(
            pk=self.instance.pk or 0
        )
        if overlapping.exists():
            raise forms.ValidationError("You already have a leave request for those dates.")

        self.balance.refresh_from_db()
        quota: LeaveQuota = self.balance.get_quota_for_type(leave_type)
        if duration > quota.remaining_days:
            raise forms.ValidationError(
                f"You requested {duration} day(s) of {leave_type.name}, but only have {quota.remaining_days} remaining."
            )
        return cleaned

    def save(self, commit: bool = True) -> LeaveRequest:
        instance = super().save(commit=False)
        instance.user = self.request_user
        if commit:
            instance.save()
            instance.initialize_workflow()
        return instance


class ManagerDecisionForm(forms.Form):
    """Optional comment a manager can attach to an approval or rejection."""

    manager_comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Add an optional note for the employee"}),
        label="Comment",
    )


class AllocationSelectionForm(forms.Form):
    """Allows managers to choose which employee's quotas to adjust."""

    employee = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by("username"),
        label="Employee",
    )


class QuotaAdjustmentForm(forms.ModelForm):
    """Adjustable fields for a leave quota."""

    class Meta:
        model = LeaveQuota
        fields = ["allocation", "carried_over", "emergency_grant"]
        widgets = {
            "allocation": forms.NumberInput(attrs={"min": 0}),
            "carried_over": forms.NumberInput(attrs={"min": 0}),
            "emergency_grant": forms.NumberInput(attrs={"min": 0}),
        }


class AnalyticsFilterForm(forms.Form):
    """Filters analytics views by year and optional month."""

    year = forms.IntegerField(min_value=2000, max_value=2100, required=False, label="Year")
    month = forms.IntegerField(min_value=1, max_value=12, required=False, label="Month")

    def clean(self):
        cleaned = super().clean()
        month = cleaned.get("month")
        year = cleaned.get("year")
        if month and not year:
            raise forms.ValidationError("Select a year when filtering by month.")
        return cleaned
