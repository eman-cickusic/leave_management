"""Database models for the leave management workflow."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Optional

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F, Sum
from django.db.models.functions import Greatest
from django.utils import timezone

User = get_user_model()


class Department(models.Model):
    """A company department used to route approvals."""

    name = models.CharField(max_length=120, unique=True)
    team_lead = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leading_departments",
    )
    hr_approver = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="hr_departments",
    )

    def __str__(self) -> str:
        return self.name

    @property
    def approval_sequence(self) -> Iterable["DepartmentApprovalRule"]:
        rules = list(self.approval_rules.order_by("sequence"))
        if rules:
            return rules
        # Default two-step flow if no explicit rules present.
        defaults = [
            DepartmentApprovalRule(department=self, role=DepartmentApprovalRule.Role.TEAM_LEAD, sequence=1),
            DepartmentApprovalRule(department=self, role=DepartmentApprovalRule.Role.HR, sequence=2),
        ]
        return defaults


class DepartmentApprovalRule(models.Model):
    """Defines the ordered approval steps for a department."""

    class Role(models.TextChoices):
        TEAM_LEAD = "LEAD", "Team Lead"
        HR = "HR", "HR"

    department = models.ForeignKey(
        Department,
        related_name="approval_rules",
        on_delete=models.CASCADE,
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    sequence = models.PositiveIntegerField()

    class Meta:
        ordering = ["sequence"]
        unique_together = ("department", "role")

    def __str__(self) -> str:
        return f"{self.department.name} · Step {self.sequence} ({self.get_role_display()})"

    def expected_reviewer(self) -> Optional[User]:
        if self.role == self.Role.TEAM_LEAD:
            return self.department.team_lead
        if self.role == self.Role.HR:
            return self.department.hr_approver
        return None


class LeaveType(models.Model):
    """Configuration for supported leave categories."""

    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    default_allocation = models.PositiveIntegerField(default=15)
    max_days_per_request = models.PositiveIntegerField(default=14)
    min_notice_days = models.PositiveIntegerField(default=0)
    requires_documentation = models.BooleanField(default=False)
    is_paid = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CompanyHoliday(models.Model):
    """Important company-wide holidays displayed on the calendar."""

    name = models.CharField(max_length=160)
    date = models.DateField(unique=True)

    class Meta:
        ordering = ["date"]

    def __str__(self) -> str:
        return f"{self.name} ({self.date})"


class LeaveBalance(models.Model):
    """Tracks an employee's annual leave allowance across leave types."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="leave_balance",
    )
    department = models.ForeignKey(
        Department,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="members",
    )
    annual_allocation = models.PositiveIntegerField(default=0)
    carried_over = models.PositiveIntegerField(default=0)
    used_days = models.PositiveIntegerField(default=0)
    emergency_days = models.PositiveIntegerField(default=0)
    last_adjusted_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    last_adjusted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "leave balance"
        verbose_name_plural = "leave balances"

    def __str__(self) -> str:
        return f"{self.user.get_username()} balance ({self.remaining_days} day(s) left)"

    @property
    def total_allocated_days(self) -> int:
        return (
            self.quotas.aggregate(
                total=Sum(F("allocation") + F("carried_over") + F("emergency_grant"))
            )["total"]
            or 0
        )

    @property
    def remaining_days(self) -> int:
        return sum(quota.remaining_days for quota in self.quotas.all())

    def remaining_days_for_type(self, leave_type: LeaveType) -> int:
        quota = self.get_quota_for_type(leave_type)
        return quota.remaining_days

    def get_quota_for_type(self, leave_type: LeaveType) -> "LeaveQuota":
        quota, _ = self.quotas.get_or_create(
            leave_type=leave_type,
            defaults={
                "allocation": leave_type.default_allocation,
            },
        )
        return quota

    def deduct_days(self, days: int) -> None:
        """Backwards compatibility – deducts from the primary leave type."""
        default_type = LeaveType.objects.order_by("id").first()
        if not default_type:
            raise ValidationError("No leave types configured.")
        self.deduct_days_for_type(default_type, days)

    def deduct_days_for_type(self, leave_type: LeaveType, days: int) -> None:
        quota = self.get_quota_for_type(leave_type)
        quota.deduct(days)

    def refund_days_for_type(self, leave_type: LeaveType, days: int) -> None:
        quota = self.get_quota_for_type(leave_type)
        quota.refund(days)

    @classmethod
    def ensure_for_user(cls, user: User) -> "LeaveBalance":
        balance, created = cls.objects.get_or_create(user=user)
        if created:
            balance.ensure_default_quotas()
        else:
            balance.ensure_default_quotas()
        return balance

    def ensure_default_quotas(self) -> None:
        for leave_type in LeaveType.objects.all():
            self.quotas.get_or_create(
                leave_type=leave_type,
                defaults={"allocation": leave_type.default_allocation},
            )


class LeaveQuota(models.Model):
    """Per-leave-type allocation and usage details."""

    balance = models.ForeignKey(
        LeaveBalance,
        on_delete=models.CASCADE,
        related_name="quotas",
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.CASCADE,
        related_name="quotas",
    )
    allocation = models.PositiveIntegerField(default=0)
    carried_over = models.PositiveIntegerField(default=0)
    emergency_grant = models.PositiveIntegerField(default=0)
    used = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("balance", "leave_type")
        verbose_name = "leave quota"
        verbose_name_plural = "leave quotas"

    def __str__(self) -> str:
        return f"{self.balance.user.get_username()} · {self.leave_type.name}"

    @property
    def total_available(self) -> int:
        return self.allocation + self.carried_over + self.emergency_grant

    @property
    def remaining_days(self) -> int:
        return max(self.total_available - self.used, 0)

    def deduct(self, days: int) -> None:
        if days <= 0:
            raise ValueError("Days to deduct must be positive.")
        self.refresh_from_db(fields=["used", "allocation", "carried_over", "emergency_grant"])
        if days > self.remaining_days:
            raise ValidationError("Not enough leave remaining for this type.")
        LeaveQuota.objects.filter(pk=self.pk).update(used=F("used") + days)
        self.refresh_from_db(fields=["used"])

    def refund(self, days: int) -> None:
        if days <= 0:
            raise ValueError("Days to refund must be positive.")
        LeaveQuota.objects.filter(pk=self.pk).update(used=Greatest(F("used") - days, 0))
        self.refresh_from_db(fields=["used"])


class LeaveRequestQuerySet(models.QuerySet):
    def overlapping(self, user: User, start: date, end: date) -> "LeaveRequestQuerySet":
        return self.filter(
            user=user,
            start_date__lte=end,
            end_date__gte=start,
        ).exclude(status=LeaveRequest.Status.REJECTED)

    def actionable(self) -> "LeaveRequestQuerySet":
        return self.filter(status__in=[LeaveRequest.Status.PENDING, LeaveRequest.Status.IN_REVIEW])


class LeaveRequest(models.Model):
    """A leave request lifecycle record."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        IN_REVIEW = "IN_REVIEW", "In Review"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="leave_requests",
    )
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leave_requests",
    )
    leave_type = models.ForeignKey(
        LeaveType,
        on_delete=models.PROTECT,
        related_name="leave_requests",
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    manager_comment = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_leave_requests",
    )
    decision_date = models.DateTimeField(null=True, blank=True)

    policy_notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LeaveRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.user.get_username()} {self.start_date}->{self.end_date} ({self.leave_type.code})"

    @property
    def total_days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def is_pending(self) -> bool:
        return self.status in {self.Status.PENDING, self.Status.IN_REVIEW}

    @property
    def current_approval(self) -> Optional["LeaveApproval"]:
        return (
            self.approvals.filter(status=LeaveApproval.Status.PENDING)
            .order_by("sequence")
            .first()
        )

    def clean(self) -> None:
        super().clean()
        if self.start_date > self.end_date:
            raise ValidationError("End date cannot be earlier than the start date.")
        if self.start_date < date.today():
            raise ValidationError("Leave cannot start in the past.")
        duration = self.total_days
        if duration <= 0:
            raise ValidationError("Leave duration must be at least one day.")
        if self.leave_type.max_days_per_request and duration > self.leave_type.max_days_per_request:
            raise ValidationError(
                f"{self.leave_type.name} leave cannot exceed {self.leave_type.max_days_per_request} day(s) per request."
            )
        notice_required = self.leave_type.min_notice_days
        if notice_required and (self.start_date - date.today()).days < notice_required:
            raise ValidationError(
                f"{self.leave_type.name} leave must be requested at least {notice_required} day(s) in advance."
            )
        if self.user_id:
            overlapping = LeaveRequest.objects.overlapping(self.user, self.start_date, self.end_date)
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                raise ValidationError("You already have a leave request covering those dates.")

    def _apply_policy_notes(self) -> None:
        notes = []
        if self.leave_type.requires_documentation:
            notes.append("Supporting documentation required for this leave type.")
        if not self.leave_type.is_paid:
            notes.append("This leave is unpaid.")
        self.policy_notes = "\n".join(notes)

    def save(self, *args, **kwargs):
        self._apply_policy_notes()
        if not self.department:
            balance = LeaveBalance.ensure_for_user(self.user)
            if balance.department:
                self.department = balance.department
        super().save(*args, **kwargs)

    def initialize_workflow(self) -> None:
        """Ensure approval steps exist for this request."""
        if self.approvals.exists():
            return
        department = self.department
        if not department:
            department = LeaveBalance.ensure_for_user(self.user).department
        if not department:
            raise ValidationError("Department must be set before submitting for approval.")
        for rule in department.approval_sequence:
            LeaveApproval.objects.create(
                request=self,
                role=rule.role,
                sequence=rule.sequence,
                assigned_to=rule.expected_reviewer(),
            )
        self.status = self.Status.IN_REVIEW
        self.save(update_fields=["status"])

    @transaction.atomic
    def record_decision(self, reviewer: User, decision: str, comment: str = "") -> str:
        approval = self.current_approval
        if not approval:
            raise ValidationError("There are no pending approvals for this request.")
        approval.mark(decision, reviewer, comment)
        if decision == LeaveApproval.Status.REJECTED:
            self.status = self.Status.REJECTED
            self.reviewed_by = reviewer
            self.manager_comment = comment
            self.decision_date = timezone.now()
            self.save(update_fields=["status", "reviewed_by", "manager_comment", "decision_date", "updated_at"])
            # Refund any previously deducted time if the request had already been approved.
            return self.Status.REJECTED

        # Approved this step - check if more steps remain.
        next_approval = self.current_approval
        if next_approval:
            self.status = self.Status.IN_REVIEW
            self.save(update_fields=["status", "updated_at"])
            return self.Status.IN_REVIEW

        # Final approval reached.
        self._finalize_approval(reviewer, comment)
        return self.Status.APPROVED

    def _finalize_approval(self, reviewer: User, comment: str) -> None:
        balance = LeaveBalance.ensure_for_user(self.user)
        balance.deduct_days_for_type(self.leave_type, self.total_days)
        self.status = self.Status.APPROVED
        self.reviewed_by = reviewer
        self.manager_comment = comment
        self.decision_date = timezone.now()
        self.save(
            update_fields=[
                "status",
                "reviewed_by",
                "manager_comment",
                "decision_date",
                "updated_at",
            ]
        )


class LeaveApprovalQuerySet(models.QuerySet):
    def for_user(self, user: User) -> "LeaveApprovalQuerySet":
        return self.filter(
            models.Q(assigned_to=user)
            | models.Q(
                assigned_to__isnull=True,
                role=DepartmentApprovalRule.Role.TEAM_LEAD,
                request__department__team_lead=user,
            )
            | models.Q(
                assigned_to__isnull=True,
                role=DepartmentApprovalRule.Role.HR,
                request__department__hr_approver=user,
            )
        )


class LeaveApproval(models.Model):
    """Captures an approval decision for a specific workflow step."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    request = models.ForeignKey(
        LeaveRequest,
        related_name="approvals",
        on_delete=models.CASCADE,
    )
    role = models.CharField(max_length=10, choices=DepartmentApprovalRule.Role.choices)
    sequence = models.PositiveIntegerField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_leave_approvals",
    )
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_leave_approvals",
    )
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    objects = LeaveApprovalQuerySet.as_manager()

    class Meta:
        ordering = ["sequence"]
        unique_together = ("request", "sequence")

    def __str__(self) -> str:
        return f"{self.request} · Step {self.sequence}"

    def clean(self):
        if self.sequence <= 0:
            raise ValidationError("Sequence must be positive.")

    def mark(self, decision: str, reviewer: User, comment: str = "") -> None:
        if decision not in {self.Status.APPROVED, self.Status.REJECTED}:
            raise ValidationError("Invalid decision.")
        if self.status != self.Status.PENDING:
            raise ValidationError("This approval step has already been completed.")
        self.status = decision
        self.reviewed_by = reviewer
        self.comment = comment
        self.decided_at = timezone.now()
        self.save(update_fields=["status", "reviewed_by", "comment", "decided_at"])

    def is_user_eligible(self, user: User) -> bool:
        if self.status != self.Status.PENDING:
            return False
        if self.assigned_to and self.assigned_to == user:
            return True
        if self.role == DepartmentApprovalRule.Role.TEAM_LEAD and self.request.department:
            return self.request.department.team_lead == user
        if self.role == DepartmentApprovalRule.Role.HR and self.request.department:
            return self.request.department.hr_approver == user
        return False
