from django.shortcuts import render
from .models import BotAnalytics

# Create your views here.

def log_command(chat_id, command, success=True):
    """Log bot commands for analytics."""
    BotAnalytics.objects.create(
        user_id=chat_id,
        command=command,
        success=success
    )

def log_command_decorator(func):
    def wrapper(message, *args, **kwargs):
        chat_id = message.chat.id
        command = message.text
        try:
            result = func(message, *args, **kwargs)  # Execute the original handler
            log_command(chat_id, command, success=True)
            return result
        except Exception as e:
            log_command(chat_id, command, success=False)
            raise e
    return wrapper
