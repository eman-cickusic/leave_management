# Leave Management System (Django)

A Django-based employee attendance and leave management portal supporting multi-step approvals, per-type leave quotas, calendars, analytics, and email notifications.

## Prerequisites
- Python 3.11+
- Git

## Setup

git clone https://github.com/eman-cickusic/leave_management.git
cd YOUR-REPO
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
`

## Useful URLs
- /: Employee dashboard
- /apply/: Submit a leave request
- /calendar/: Calendar of approved leave + holidays
- /manager/: Manager console (Team Lead & HR approvals)
- /manager/allocations/: Adjust leave quotas
- /manager/analytics/: Usage analytics + exports
- /admin/: Django admin for configuration

## Notes
- Default email backend prints messages to the console.
- Seed leave types are created by migrations.
- Department routing determines approval order (Team Lead ? HR).
