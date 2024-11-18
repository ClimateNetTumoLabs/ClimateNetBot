# ClimateNet Telegram Bot

🌤️ **ClimateNet** is your personal climate assistant on Telegram, designed to keep you informed about climate conditions. With this bot, you can see current updates on temperature, humidity, wind speed, and much more!

## Features

- Get updates on climate conditions.
- User-friendly interface to choose locations and devices.
- Supports multiple devices and locations across Armenia and the USA.

## Technologies Used

- **Django**: A powerful web framework for building the backend.
- **Telegram Bot API**: To interact with users via Telegram.
- **Python**: The programming language used for development.

## Installation

To set up the ClimateNet bot locally, follow these steps:

1. **Clone the repository:**
   `git clone https://github.com/yourusername/climatenet-telegram-bot.git`
   `cd climatenet-telegram-bot`
   
2. **Create a virtual environment:**
   `python -m venv venv`
   `source venv/bin/activate`  # On Windows use `venv\Scripts\activate`
   
3. **Install dependencies:**
   `pip install -r requirements.txt`
   
4. **Set up environment variables:**
   Create a .env file in the root directory and add Telegram bot token and Secret Key:
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token and SECRET_KEY=secret_key_of_your_django_project 
   ##(if you don't have it generate by this command `python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')`

5. **Modify the code (Uncomment specific parts):**
   In climate_bot/bot/views.py, uncomment the following:
   
      Lines 2 and 3:
         `#from django.http import JsonResponse
         #from django.views import View`

   Line 256:
         `#def run_bot_view(request):
            #start_bot_thread()
            #return JsonResponse({'status': 'Bot is running in the background!'})`

6. **Run the Django server:**
    `python manage.py runserver`

**And see the result in development server at http://127.0.0.1:8000/**
