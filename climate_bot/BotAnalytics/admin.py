from django.contrib import admin
from django.db.models import Count, F
from .models import BotAnalytics
from django.utils.timezone import now
from datetime import timedelta

class BotAnalyticsAdmin(admin.ModelAdmin):
    change_list_template = "admin/botanalytics_changelist.html"

    def changelist_view(self, request, extra_context=None):
        # Total users
        total_users = BotAnalytics.objects.values('user_id').distinct().count()

        # Active users (last 7 days)
        active_users = BotAnalytics.objects.filter(timestamp__gte=now() - timedelta(days=7)).values('user_id').distinct().count()

        # New users (last 7 days)
        new_users = (
            BotAnalytics.objects.filter(timestamp__gte=now() - timedelta(days=7))
            .values('user_id')
            .annotate(first_seen=F('timestamp'))
            .distinct()
            .count()
        )

        # Inactive users (last 30 days)
        all_users = BotAnalytics.objects.values('user_id').distinct()
        inactive_users = all_users.exclude(
            user_id__in=BotAnalytics.objects.filter(
                timestamp__gte=now() - timedelta(days=30)
            ).values('user_id')
        ).count()

        # Engagement rate
        engagement_rate = (active_users / total_users) * 100 if total_users > 0 else 0

        # Total commands
        total_commands = BotAnalytics.objects.count()

        # Command usage
        command_usage = (
            BotAnalytics.objects.values('command')
            .annotate(total=Count('command'))
            .order_by('-total')
        )

        # ClimateNet-specific analytics
        popular_devices = (
            BotAnalytics.objects.values('device_location')
            .annotate(total=Count('id'))
            .order_by('-total')
        )

        # Add data to the context
        extra_context = extra_context or {}
        extra_context.update({
            'total_users': total_users,
            'active_users': active_users,
            'new_users': new_users,
            'inactive_users': inactive_users,
            'engagement_rate': engagement_rate,
            'total_commands': total_commands,
            'command_usage': list(command_usage),
            'popular_devices': list(popular_devices),
        })
        return super().changelist_view(request, extra_context=extra_context)

admin.site.register(BotAnalytics,BotAnalyticsAdmin)