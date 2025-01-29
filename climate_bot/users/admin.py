from django.contrib import admin
from django.contrib import messages
from django import forms
from django.http import JsonResponse
from django.shortcuts import render
from .models import TelegramUser
import telebot
import os
from django.urls import path
from .views import send_message_to_users_view


# Assuming you have your Telegram Bot Token stored in an environment variable
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Custom form for writing the message
class SendMessageForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea)

class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ('telegram_id', 'first_name', 'last_name', 'location', 'joined_at')
    actions = ['send_message_to_users']
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('hello/', self.admin_site.admin_view(send_message_to_users_view), name='analytics_data'),
        ]
        return custom_urls + urls
   
    # Custom admin action to send a message
    def send_message_to_users(self, request, queryset):
        print("send_message_to_users function called")
        print("Request Headers:", request.POST)

        # Handle AJAX request
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            print("AJAX request detected")
            form = SendMessageForm(request.POST)

            if form.is_valid():
                print("Form is valid")
                message = form.cleaned_data['message']
                bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

                success_count = 0
                failure_count = 0

                for user in queryset:
                    try:
                        bot.send_message(chat_id=user.telegram_id, text=message)
                        print(f"Message sent to {user.telegram_id}")
                        success_count += 1
                    except Exception as e:
                        failure_count += 1
                        print(f"Failed to send message to {user.telegram_id}: {e}")

                return JsonResponse({
                    "success": True,
                    "message": f"Successfully sent message to {success_count} users. Failed: {failure_count}"
                })

            print("Form is invalid:", form.errors)
            return JsonResponse({"success": False, "message": "Invalid form data"}, status=400)

        # Handle normal (non-AJAX) request
        return render(
            request,
            'admin/send_message.html',
            context={'users': queryset, 'form': SendMessageForm()},
        )

    send_message_to_users.short_description = "Send a message to selected users"

# Register the model with the custom admin class
admin.site.register(TelegramUser, TelegramUserAdmin)
