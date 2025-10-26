"""Signal handlers for leave_requests."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import LeaveBalance, LeaveType

User = get_user_model()


@receiver(post_save, sender=User)
def create_leave_balance(sender, instance: User, created: bool, **kwargs) -> None:
    """Ensure every user has an associated leave balance record."""
    if created:
        LeaveBalance.ensure_for_user(instance)


@receiver(post_save, sender=LeaveType)
def ensure_quotas_for_new_leave_type(sender, instance: LeaveType, created: bool, **kwargs) -> None:
    """Backfill quotas for all balances when a new leave type is introduced."""
    if not created:
        return
    for balance in LeaveBalance.objects.select_related("user").iterator():
        balance.ensure_default_quotas()
