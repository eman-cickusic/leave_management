from django.apps import AppConfig


class LeaveRequestsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "leave_requests"

    def ready(self) -> None:
        # Import signals so the handlers are registered when the app starts.
        from . import signals  # noqa: F401
