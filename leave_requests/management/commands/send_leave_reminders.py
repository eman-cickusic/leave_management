from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from ...models import LeaveRequest
from ...notifications import notify_upcoming_leave


class Command(BaseCommand):
    help = "Send email reminders for approved leaves that are about to start."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=2,
            help="Number of days in advance to send reminders (default: 2).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        today = date.today()
        window_end = today + timedelta(days=days)
        upcoming = LeaveRequest.objects.filter(
            status=LeaveRequest.Status.APPROVED,
            start_date__range=(today, window_end),
        )
        count = 0
        for request_obj in upcoming:
            notify_upcoming_leave(request_obj)
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {count} leave reminder(s)."))
