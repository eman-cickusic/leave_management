"""Email notification helpers for leave workflow events."""
from __future__ import annotations

from datetime import date
from typing import Iterable

from django.conf import settings
from django.core.mail import send_mail

from .models import LeaveApproval, LeaveRequest


def _send(to_addresses: Iterable[str], subject: str, message: str) -> None:
    recipients = [email for email in to_addresses if email]
    if not recipients:
        return
    send_mail(
        subject=subject,
        message=message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.com"),
        recipient_list=recipients,
        fail_silently=True,
    )


def notify_request_submitted(request_obj: LeaveRequest) -> None:
    """Alert the employee and the first approver that a request was filed."""
    subject = f"Leave request submitted: {request_obj.leave_type.name}"
    message = (
        f"Hi {request_obj.user.get_username()},\n\n"
        f"You submitted a request for {request_obj.leave_type.name} from "
        f"{request_obj.start_date} to {request_obj.end_date} ({request_obj.total_days} day(s)).\n"
        "Your manager will review it shortly."
    )
    _send([request_obj.user.email], subject, message)

    first_approval = request_obj.current_approval
    if first_approval and first_approval.assigned_to:
        notify_next_approver(first_approval)


def notify_next_approver(approval: LeaveApproval) -> None:
    """Ping the next approver in the chain."""
    if not approval.assigned_to:
        return
    request_obj = approval.request
    subject = f"Approval needed: {request_obj.user.get_username()} {request_obj.leave_type.code}"
    message = (
        f"Hi {approval.assigned_to.get_username()},\n\n"
        f"{request_obj.user.get_username()} requested {request_obj.total_days} day(s) "
        f"of {request_obj.leave_type.name} leave "
        f"({request_obj.start_date} to {request_obj.end_date}).\n"
        "Please review the request in the leave management portal."
    )
    _send([approval.assigned_to.email], subject, message)


def notify_request_approved(request_obj: LeaveRequest) -> None:
    subject = f"Leave approved: {request_obj.leave_type.name}"
    message = (
        f"Hi {request_obj.user.get_username()},\n\n"
        f"Your {request_obj.leave_type.name} request for {request_obj.total_days} day(s) "
        f"({request_obj.start_date} to {request_obj.end_date}) was approved.\n"
        "Enjoy your time off!"
    )
    _send([request_obj.user.email], subject, message)


def notify_request_rejected(request_obj: LeaveRequest) -> None:
    subject = f"Leave decision: {request_obj.leave_type.name} request declined"
    message = (
        f"Hi {request_obj.user.get_username()},\n\n"
        f"Your {request_obj.leave_type.name} request for {request_obj.total_days} day(s) "
        f"({request_obj.start_date} to {request_obj.end_date}) was rejected.\n"
        f"Manager notes: {request_obj.manager_comment or 'No comment provided.'}"
    )
    _send([request_obj.user.email], subject, message)


def notify_upcoming_leave(request_obj: LeaveRequest) -> None:
    subject = f"Reminder: Upcoming {request_obj.leave_type.name} leave"
    message = (
        f"Hi {request_obj.user.get_username()},\n\n"
        f"This is a reminder that your {request_obj.leave_type.name} leave "
        f"starts on {request_obj.start_date} and ends on {request_obj.end_date}.\n"
        "Please ensure your tasks are handed over before you are away."
    )
    _send([request_obj.user.email], subject, message)
