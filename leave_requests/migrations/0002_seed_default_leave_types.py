# Generated manually to seed baseline leave types.
from __future__ import annotations

from django.db import migrations

DEFAULT_LEAVE_TYPES = [
    ("VAC", "Vacation", 20, 15, 2, False, True),
    ("SICK", "Sick Leave", 10, 7, 0, True, True),
    ("UNPAID", "Unpaid Leave", 999, 30, 5, False, False),
]


def seed_leave_types(apps, schema_editor):
    LeaveType = apps.get_model("leave_requests", "LeaveType")
    LeaveBalance = apps.get_model("leave_requests", "LeaveBalance")
    LeaveQuota = apps.get_model("leave_requests", "LeaveQuota")
    for code, name, allocation, max_days, min_notice, documentation, paid in DEFAULT_LEAVE_TYPES:
        LeaveType.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "default_allocation": allocation,
                "max_days_per_request": max_days,
                "min_notice_days": min_notice,
                "requires_documentation": documentation,
                "is_paid": paid,
            },
        )
    leave_types = list(LeaveType.objects.all())
    for balance in LeaveBalance.objects.all():
        for leave_type in leave_types:
            LeaveQuota.objects.get_or_create(
                balance=balance,
                leave_type=leave_type,
                defaults={"allocation": leave_type.default_allocation},
            )


def remove_leave_types(apps, schema_editor):
    LeaveType = apps.get_model("leave_requests", "LeaveType")
    LeaveType.objects.filter(code__in=[code for code, *_ in DEFAULT_LEAVE_TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("leave_requests", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_leave_types, remove_leave_types),
    ]
