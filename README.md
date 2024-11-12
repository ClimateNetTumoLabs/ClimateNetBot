# ClimateNet Telegram Bot

🌤️ **ClimateNet** is your personal climate assistant on Telegram, designed to keep you informed about real-time climate and environmental conditions. With this bot, you can receive live updates on temperature, humidity, wind speed, and much more!

## Features

- Get updates on climate conditions.
- Receive alerts for significant climate changes and potential hazards.
- Access personalized recommendations based on current conditions.
- User-friendly interface to choose locations and devices.
- Supports multiple devices and locations across Armenia and the USA.

## Technologies Used

- **Django**: A powerful web framework for building the backend.
- **Django REST Framework**: For building the RESTful API.
- **Telegram Bot API**: To interact with users via Telegram.
- **Python**: The programming language used for development.

## Installation

To set up the ClimateNet bot locally, follow these steps:

1. **Clone the repository**:
   `git clone https://github.com/yourusername/climatenet-telegram-bot.git`
   `cd climatenet-telegram-bot`
2. **Create a virtual environment:**
   `python -m venv venv`
   `source venv/bin/activate`  # On Windows use `venv\Scripts\activate`  
3. **Install dependencies:**
   `pip install -r requirements.txt`
4. **Set up environment variables:**
   Create a .env file in the root directory and add Telegram bot token:
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
5. **Run the Django server:**
    `python manage.py runserver`
   
**And see the result in development server at http://127.0.0.1:8000/**
