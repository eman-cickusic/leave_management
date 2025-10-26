from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from .forms import LeaveRequestForm
from .models import Department, LeaveApproval, LeaveBalance, LeaveRequest, LeaveType

User = get_user_model()


class LeaveWorkflowTests(TestCase):
    def setUp(self):
        # Configure base leave types before creating users so balances pick them up.
        self.vacation, _ = LeaveType.objects.get_or_create(
            code="VAC",
            defaults={
                "name": "Vacation",
                "default_allocation": 20,
                "max_days_per_request": 10,
                "min_notice_days": 2,
                "requires_documentation": False,
            },
        )
        self.sick, _ = LeaveType.objects.get_or_create(
            code="SICK",
            defaults={
                "name": "Sick Leave",
                "default_allocation": 10,
                "max_days_per_request": 5,
                "min_notice_days": 0,
                "requires_documentation": True,
            },
        )

        self.team_lead = User.objects.create_user(username="lead", password="pass123", is_staff=True)
        self.hr = User.objects.create_user(username="hr", password="pass123", is_staff=True)
        self.employee = User.objects.create_user(username="employee", password="pass123", email="employee@example.com")

        self.department = Department.objects.create(
            name="Engineering",
            team_lead=self.team_lead,
            hr_approver=self.hr,
        )
        balance = LeaveBalance.ensure_for_user(self.employee)
        balance.department = self.department
        balance.save(update_fields=["department"])

    def _create_request(self, leave_type: LeaveType, days: int = 3) -> LeaveRequest:
        request_obj = LeaveRequest.objects.create(
            user=self.employee,
            leave_type=leave_type,
            start_date=date.today() + timedelta(days=5),
            end_date=date.today() + timedelta(days=4 + days),
            reason="Testing workflow",
        )
        request_obj.initialize_workflow()
        return request_obj

    def test_leave_balance_signal_creates_quotas(self):
        balance = LeaveBalance.objects.get(user=self.employee)
        self.assertGreater(balance.quotas.count(), 0)
        quota = balance.get_quota_for_type(self.vacation)
        self.assertEqual(quota.remaining_days, self.vacation.default_allocation)

    def test_leave_request_form_blocks_excessive_duration(self):
        form = LeaveRequestForm(
            data={
                "leave_type": self.vacation.pk,
                "start_date": date.today() + timedelta(days=1),
                "end_date": date.today() + timedelta(days=20),
                "reason": "Long vacation",
            },
            user=self.employee,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("cannot exceed", str(form.errors))

    def test_leave_request_overlap_is_rejected(self):
        existing = self._create_request(self.vacation, days=2)
        form = LeaveRequestForm(
            data={
                "leave_type": self.vacation.pk,
                "start_date": existing.start_date,
                "end_date": existing.end_date,
                "reason": "Overlap attempt",
            },
            user=self.employee,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("already have a leave request", str(form.errors))

    def test_multi_step_approval_deducts_quota(self):
        request_obj = self._create_request(self.vacation, days=3)
        balance = LeaveBalance.objects.get(user=self.employee)
        starting_remaining = balance.remaining_days_for_type(self.vacation)

        first_approval = request_obj.current_approval
        self.assertEqual(first_approval.role, "LEAD")
        outcome = request_obj.record_decision(self.team_lead, LeaveApproval.Status.APPROVED, "All good")
        self.assertEqual(outcome, LeaveRequest.Status.IN_REVIEW)

        second_approval = request_obj.current_approval
        self.assertEqual(second_approval.role, "HR")
        outcome = request_obj.record_decision(self.hr, LeaveApproval.Status.APPROVED, "HR cleared")
        self.assertEqual(outcome, LeaveRequest.Status.APPROVED)

        request_obj.refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual(request_obj.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(
            balance.remaining_days_for_type(self.vacation),
            starting_remaining - request_obj.total_days,
        )

    def test_rejection_in_pipeline_does_not_deduct(self):
        request_obj = self._create_request(self.sick, days=2)
        balance = LeaveBalance.ensure_for_user(self.employee)
        starting_remaining = balance.remaining_days_for_type(self.sick)

        #Team lead approves, HR rejects.
        request_obj.record_decision(self.team_lead, LeaveApproval.Status.APPROVED, "Feel better soon")
        outcome = request_obj.record_decision(self.hr, LeaveApproval.Status.REJECTED, "Need medical note")
        self.assertEqual(outcome, LeaveRequest.Status.REJECTED)

        request_obj.refresh_from_db()
        balance.refresh_from_db()
        self.assertEqual(balance.remaining_days_for_type(self.sick), starting_remaining)

    def test_insufficient_quota_prevents_final_approval(self):
        request_obj = self._create_request(self.vacation, days=8)
        quota = LeaveBalance.ensure_for_user(self.employee).get_quota_for_type(self.vacation)
        quota.allocation = 5
        quota.used = 0
        quota.save()

        request_obj.record_decision(self.team_lead, LeaveApproval.Status.APPROVED, "Manager ok")
        with self.assertRaises(ValidationError):
            request_obj.record_decision(self.hr, LeaveApproval.Status.APPROVED, "HR ok")
