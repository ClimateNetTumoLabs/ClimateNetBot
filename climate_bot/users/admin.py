from django import forms
from django.contrib import admin
from .models import TelegramUser
import telebot
import os
import logging
from django.contrib import messages
from django.template.response import TemplateResponse
from django.shortcuts import render, redirect


logger = logging.getLogger(__name__)

# Define the form for broadcasting the message
class BroadcastMessageForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea, label="Broadcast Message", max_length=4096)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ('telegram_id', 'first_name', 'last_name', 'coordinates', 'joined_at')
    actions = ['send_broadcast_message']
    def send_broadcast_message(self, request, queryset):
        print('mtav')
        # Check if the form is being submitted
        if request.method == 'POST':
            form = BroadcastMessageForm(request.POST)
            if form.is_valid():
                message = form.cleaned_data['message']
                try:
                    selected_users_ids = request.POST.getlist('selected_users')
                    selected_users = TelegramUser.objects.filter(id__in=selected_users_ids)
                    for user in selected_users:
                        try:
                            bot.send_message(user.telegram_id, message)
                        except Exception as e:
                            logger.error(f"Failed to send message to {user.telegram_id}: {e}")
                    messages.success(request, "Broadcast message sent successfully!")
                except Exception as e:
                    messages.error(request, f"Failed to send message: {e}")
            else:
                messages.error(request, "Form is invalid")
        else:
            form = BroadcastMessageForm()

        # Get all Telegram users for selection in the form
        users = TelegramUser.objects.all()
        
        return render(request, 'send_broadcast_message.html', {
            'form': form,
            'users': users
        })
        
        # Return the custom template with the form
        # return TemplateResponse(request, 'admin/send_broadcast_message.html', extra_context)

    send_broadcast_message.short_description = "Send broadcast message to selected users"
