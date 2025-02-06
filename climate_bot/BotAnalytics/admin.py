from django.contrib import admin
from django.db.models import Count, F
from .models import BotAnalytics,LocationsAnalytics
from django.utils.timezone import now
from datetime import timedelta
from django.db.models import Max, Min
from django.http import JsonResponse
from django.urls import path

class BotAnalyticsAdmin(admin.ModelAdmin):
    change_list_template = "admin/botanalytics_changelist.html"

    def changelist_view(self, request, extra_context=None):
        def get_min_response_time():
            min_response_time = BotAnalytics.objects.aggregate(Min('response_time'))['response_time__min']
            return round(min_response_time, 3) if min_response_time is not None else 'N/A'
        

        # Method to get the maximum response time
        def get_max_response_time():
            max_response_time = BotAnalytics.objects.aggregate(Max('response_time'))['response_time__max']
            return round(max_response_time, 3) if max_response_time is not None else 'N/A'
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
        
        max_res_time = get_max_response_time()
        min_res_time = get_min_response_time()

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
            'minimum_respone_time':min_res_time,
            'maximum_response_time':max_res_time,
        })
        return super().changelist_view(request, extra_context=extra_context)



from datetime import datetime, timedelta
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware
from django.db.models import Count
from django.http import JsonResponse
from django.urls import path
from django.contrib import admin
from .models import LocationsAnalytics

@admin.register(LocationsAnalytics)
class LocationsAnalyticsAdmin(admin.ModelAdmin):
    list_display = ('user_id', 'device_id', 'device_name', 'device_province')
    change_list_template = "admin/analytics_dashboard.html"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('analytics-data/', self.admin_site.admin_view(self.analytics_data), name='analytics_data'),
        ]
        return custom_urls + urls

    def analytics_data(self, request):
        # Get query parameters
        start_date_str = request.GET.get('startDate')
        end_date_str = request.GET.get('endDate')
        time_range = request.GET.get('timeRange')  # daily, weekly, yearly

        # Set default range to today
        today = datetime.now().date()
        start_date = make_aware(datetime.combine(today, datetime.min.time()))
        end_date = make_aware(datetime.combine(today, datetime.max.time()))

        # Handle explicit startDate and endDate if provided
        if start_date_str:
            parsed_start = parse_datetime(start_date_str)
            if parsed_start:
                start_date = parsed_start

        if end_date_str:
            parsed_end = parse_datetime(end_date_str)
            if parsed_end:
                end_date = parsed_end

        # If no explicit dates are given, use timeRange
        if not start_date_str and not end_date_str and time_range:
            if time_range == 'daily':
                start_date = make_aware(datetime.combine(today, datetime.min.time()))
                end_date = make_aware(datetime.combine(today, datetime.max.time()))
            elif time_range == 'weekly':
                start_date = make_aware(datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time()))
                end_date = make_aware(datetime.combine(today, datetime.max.time()))
            elif time_range == 'yearly':
                start_date = make_aware(datetime.combine(datetime(today.year, 1, 1), datetime.min.time()))
                end_date = make_aware(datetime.combine(datetime(today.year, 12, 31), datetime.max.time()))

        # Fetch province usage data with date range filtering
        query = LocationsAnalytics.objects.filter(timestamp__range=(start_date, end_date)) \
            .values('device_province').annotate(count=Count('device_province')).order_by('-count')

        # If a specific province is selected, fetch device data for it
        selected_province = request.GET.get('province')
        device_data = []
        if selected_province:
            device_query = LocationsAnalytics.objects.filter(
                device_province=selected_province,
                timestamp__range=(start_date, end_date)
            )

            device_data = (
                device_query.values('device_name')
                .annotate(count=Count('device_name'))
                .order_by('-count')
            )

        return JsonResponse({'province_data': list(query), 'device_data': list(device_data)})

    
admin.site.register(BotAnalytics, BotAnalyticsAdmin)

    