# ClimateNet Telegram Bot

🌤️ **ClimateNet** is your personal climate assistant on Telegram, designed to keep you informed about climate conditions. With this bot, you can see current updates on temperature, humidity, wind speed, and much more!

## Features

- Get updates on climate conditions.
- User-friendly interface to choose locations and devices.
- Supports multiple devices and locations across Armenia and the USA.

## Technologies Used

- **Django**:  Serves as the framework to manage bot tasks, organize project structure.
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

   If you don't have it generate by this command `python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'`

5. **Modify the code (Uncomment specific parts):**
   In climate_bot/bot/views.py, uncomment the following:
   
      Lines 2 and 3:
         `#from django.http import JsonResponse
         #from django.views import View`

   Line 256:
         `#def run_bot_view(request):
            #start_bot_thread()
            #return JsonResponse({'status': 'Bot is running in the background!'})`

   In climate_bot/climate_bot/settings.py, change the following:

      Line 28:

      **FROM THIS:** 
         ```DEBUG=False```
   
      **TO THIS:**
         ```DEBUG=True```

   And also change this part: 

   Line 85:
                  
      **FROM THIS:**  
      
      ```
            DATABASES = {
               'default': {
               'ENGINE': 'django.db.backends.sqlite3',
               'NAME': '/path_to_db/db.sqlite3',
        }
      }
      ```

      **TO THIS:**
      ```
         DATABASES = {
            'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
      }
      ```
7. **Run the Django server:**
    `python manage.py runserver`

**And see the result in development server at http://127.0.0.1:8000/**


## Server Hosting

**To host the bot on a Linux server using a service file, follow these steps:**
   1. **Clone the repository on your instance:**
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

      If you don't have it generate by this command `python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'`

   5. **Create a systemd service file:**
      Create a systemd service file for the bot:
      `sudo vim /etc/systemd/system/climatenet.service`
      
      Add the following content (adjust paths as needed):
      ```
      [Unit]
      Description=Telegram Bot
      After=network.target
   
      [Service]
      User=ubuntu
      WorkingDirectory=/path_to_your_project_directory
      ExecStart=/path_to_your_project_venv/venv/bin/python3 /path_to_your_project_manage.py/manage.py start_bot
      Restart=always
      RestartSec=5
      TimeoutSec=300
      KillMode=process
   
      [Install]
      WantedBy=multi-user.target
      ```
      Save and exit the file.

   6. **Start and enable the service:**
   Reload systemd to recognize the new service:
      ```
      sudo systemctl daemon-reload
      sudo systemctl start climatenet.service
      sudo systemctl enable climatenet.service
      ```

   7. **Verify the service:**
   Check the status of your bot:
      `sudo systemctl status climatenet.service`


   **Your bot should now be running and accessible via Telegram!**

   If service is not active, see the issues with this command:
      `sudo journalctl -u telegram_bot.service -f`

## Happy Coding! 🚀


