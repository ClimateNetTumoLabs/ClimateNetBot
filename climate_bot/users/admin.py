from django.contrib import admin
from django.db.models import Count
from django.db.models.functions import TruncDay, TruncHour, TruncWeek
import json
from datetime import datetime, timedelta
from .models import TelegramUser


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ('telegram_id', 'first_name', 'last_name', 'coordinates', 'joined_at')

    def changelist_view(self, request, extra_context=None):
        now = datetime.now()

        # Generate labels for each range
        hourly_labels = [f"{minute:02d} min" for minute in range(60)]
        daily_labels = [f"{hour:02d}:00" for hour in range(24)]
        weekly_labels = [
            (now - timedelta(days=i)).strftime('%Y-%m-%d')
            for i in range(6, -1, -1)  # Last 7 days
        ]

        # Hourly Data
        hourly_data = (
            TelegramUser.objects.annotate(hour=TruncHour('joined_at'), minute=TruncHour('joined_at'))
            .values('hour')
            .annotate(count=Count('id'))
        )
        hourly_counts = {entry["hour"].strftime('%M'): entry["count"] for entry in hourly_data}
        hourly_counts_full = [hourly_counts.get(f"{minute:02d}", 0) for minute in range(60)]

        # Daily Data
        daily_data = (
            TelegramUser.objects.annotate(hour=TruncHour('joined_at'))
            .values('hour')
            .annotate(count=Count('id'))
        )
        daily_counts = {entry["hour"].strftime('%H'): entry["count"] for entry in daily_data}
        daily_counts_full = [daily_counts.get(f"{hour:02d}", 0) for hour in range(24)]

        # Weekly Data
        weekly_data = (
            TelegramUser.objects.annotate(week=TruncDay('joined_at'))
            .values('week')
            .annotate(count=Count('id'))
        )
        weekly_counts = {entry["week"].strftime('%Y-%m-%d'): entry["count"] for entry in weekly_data}
        weekly_counts_full = [weekly_counts.get(day, 0) for day in weekly_labels]

        # Prepare JSON Data for Chart.js
        chart_data = {
            "hourly": {"labels": hourly_labels, "counts": hourly_counts_full},
            "daily": {"labels": daily_labels, "counts": daily_counts_full},
            "weekly": {"labels": weekly_labels, "counts": weekly_counts_full},
        }

        # Add chart data to the context
        extra_context = extra_context or {}
        extra_context["chart_data"] = json.dumps(chart_data)

        return super().changelist_view(request, extra_context=extra_context)
