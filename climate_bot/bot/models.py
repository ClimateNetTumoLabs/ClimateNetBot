from django.db import models
from django.conf import settings
from django.contrib.auth.models import User

class Device(models.Model):
    generated_id = models.CharField(max_length=200, unique=True)
    name = models.CharField(max_length=200)
    parent_name = models.CharField(max_length=200)
    latitude = models.DecimalField(max_digits=18, decimal_places=15)
    longitude = models.DecimalField(max_digits=18, decimal_places=15)

    def __str__(self):
        return f"{self.generated_id}"

    class Meta:
        db_table = 'backend_device'


class TelegramSchedule(models.Model):
    FREQUENCY_CHOICES = [
        ('15min', 'Every 15 minutes'),
        ('1hour', 'Every hour'),
        ('custom', 'Custom times'),
    ]
    
    chat_id = models.BigIntegerField()
    device_name = models.CharField(max_length=200)
    device_id = models.CharField(max_length=100)
    frequency = models.CharField(max_length=20, choices=FREQUENCY_CHOICES)
    custom_times = models.JSONField(null=True, blank=True)  # Store as JSON array
    job_ids = models.JSONField(default=list)  # Store scheduler job IDs
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['chat_id', 'device_name']
    
    def __str__(self):
        return f"Schedule for {self.device_name} - Chat {self.chat_id}"
