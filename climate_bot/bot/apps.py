from django.apps import AppConfig
import os

class BotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bot'

    def ready(self):
        if os.environ.get('RUN_MAIN'):
            from .views import restore_schedules_on_startup
            restore_schedules_on_startup()
