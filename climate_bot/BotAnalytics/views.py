import time
from django.db import models  # Import models for aggregation
from django.utils import timezone  # For accurate timestamping
from .models import BotAnalytics 

def log_command_decorator(func):
    def wrapper(message):
        start_time = time.perf_counter()  # Start timing
        try:
            func(message)
            success = True
        except Exception as e:
            success = False
        end_time = time.perf_counter()  # End timing
        latency = end_time - start_time

        # Save analytics data
        print(latency)
        BotAnalytics.objects.create(
            user_id=message.from_user.id,
            command=message.text,
            success=success,
            response_time=latency,
        )

        # Update min/max response times
        analytics = BotAnalytics.objects.filter(user_id=message.from_user.id)
        min_latency = analytics.aggregate(models.Min('response_time'))['response_time__min']
        max_latency = analytics.aggregate(models.Max('response_time'))['response_time__max']

        BotAnalytics.objects.filter(id=analytics.latest('timestamp').id).update(
            min_response_time=min_latency,
            max_response_time=max_latency,
        )

    return wrapper
