#from django.http import JsonResponse
#from django.views import View
import requests
import telebot
from telebot import types
import threading
import time
import os
from dotenv import load_dotenv
from bot.models import Device
from collections import defaultdict
import django
from django.conf import settings
#from users.utils import save_telegram_user,save_users_locations
#import logging

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

django.setup()

def get_device_data():
    locations = defaultdict(list)
    device_ids = {}
    devices = Device.objects.all()
    for device in devices:
        device_ids[device.name] = device.generated_id
        locations[device.parent_name].append(device.name)

    return locations, device_ids

locations, device_ids = get_device_data()
user_context = {}

devices_with_issues = ["Maralik", "Ashotsk", "Gavar", "Yerazgavors", "Artsvaberd", 
                       "Chambarak", "Areni", "Amasia", "Panik"]

def fetch_latest_measurement(device_id):
    url = f"https://climatenet.am/device_inner/{device_id}/latest/"
    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()
        if data:
            latest_measurement = data[0]  
            timestamp = latest_measurement["time"].replace("T", " ")
            return {
                "timestamp": timestamp,
                "uv": latest_measurement.get("uv"),
                "lux": latest_measurement.get("lux"),
                "temperature": latest_measurement.get("temperature"),
                "pressure": latest_measurement.get("pressure"),
                "humidity": latest_measurement.get("humidity"),
                "pm1": latest_measurement.get("pm1"),
                "pm2_5": latest_measurement.get("pm2_5"),
                "pm10": latest_measurement.get("pm10"),
                "wind_speed": latest_measurement.get("speed"),
                "rain": latest_measurement.get("rain"),
                "wind_direction": latest_measurement.get("direction")
            }
        else:
            return None
    else:
        print(f"Failed to fetch data: {response.status_code}")
        return None

def start_bot():
    bot.polling(none_stop=True)

def run_bot():
    while True:
        try:
            start_bot()
        except Exception as e:
            print(f"Error occurred: {e}")
            time.sleep(15)

def start_bot_thread():
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()

def send_location_selection(chat_id):
    location_markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    for country in locations.keys():
        location_markup.add(types.KeyboardButton(country))
    
    bot.send_message(chat_id, 'Please choose a location: 📍', reply_markup=location_markup)

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id,
        '🌤️ Welcome to ClimateNet! 🌧️'
    )
    #save_telegram_user(message.from_user)
    bot.send_message(
        message.chat.id,
        f'''Hello {message.from_user.first_name}! 👋​ I am your personal climate assistant. 
With me, you can: 
    🔹​​​ Access current measurements of temperature, humidity, wind speed, and more, which are refreshed every 15 minutes for reliable updates.
'''
    )
    send_location_selection(message.chat.id)

@bot.message_handler(func=lambda message: message.text in locations.keys())
def handle_country_selection(message):
    selected_country = message.text
    chat_id = message.chat.id
    user_context[chat_id] = {'selected_country': selected_country}
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    for device in locations[selected_country]:
        markup.add(types.KeyboardButton(device))

    bot.send_message(chat_id, 'Please choose a device: ✅​', reply_markup=markup)

# logger = logging.getLogger(__name__)

# def telegram_webhook(request):
#     if request.method == "POST":
#         data = request.json()
#         message = data.get('message', {})
#         from_user = message.get('from', {})
#         logger.info(f"Received data: {data}")
#         if message.get('text') == "/start":
#             save_telegram_user(from_user)
#             return JsonResponse({"text": "Welcome! You have been registered."})
#         return JsonResponse({"text": "Command not recognized."})
#     return JsonResponse({"text": "This endpoint only supports POST requests."})

def uv_index(uv):
    if uv is None:
        return " "
    if uv < 3:
        return "Low 🟢"
    elif 3 <= uv <= 5:
        return "Moderate ​🟡​​"
    elif 6 <= uv <= 7:
        return "High ​🟠​​"
    elif 8 <= uv <= 10:
        return "Very High 🔴​"
    else:
        return "Extreme 🟣"

def pm_level(pm, pollutant):
    if pm is None:
        return " "

    thresholds = {
        "PM1.0": [50, 100, 150, 200, 300],
        "PM2.5": [12, 36, 56, 151, 251],
        "PM10": [54, 154, 254, 354, 504]
    }

    levels = [
        "Good 🟢​​",
        "Moderate ​🟡​​",
        "Unhealthy for Sensitive Groups ​🟠​​",
        "Unhealthy 🟠​​",
        "Very Unhealthy 🔴​",
        "Hazardous 🔴​"
    ]
    thresholds = thresholds.get(pollutant, [])
    for i, limit in enumerate(thresholds):
        if pm <= limit:
            return levels[i]
    
    return levels[-1]

@bot.message_handler(func=lambda message: message.text in [device for devices in locations.values() for device in devices])
def handle_device_selection(message):
    selected_device = message.text
    chat_id = message.chat.id
    device_id = device_ids.get(selected_device)
    
    if chat_id in user_context:
        user_context[chat_id]['selected_device'] = selected_device
        user_context[chat_id]['device_id'] = device_id

    if device_id:
        command_markup = get_command_menu()
        measurement = fetch_latest_measurement(device_id)
        if measurement:
            uv_description = uv_index(measurement['uv'])
            pm1_description = pm_level(measurement['pm1'], "PM1.0")
            pm2_5_description = pm_level(measurement['pm2_5'], "PM2.5")
            pm10_description = pm_level(measurement['pm10'], "PM10")
            if selected_device in devices_with_issues:
                technical_issues_message = "\n⚠️ Note: At this moment this device has technical issues."
            else:
                technical_issues_message = ""
            formatted_data = (
                f"Latest Measurements in <b>{selected_device}</b> {measurement['timestamp']}\n\n"
                f"☀️ UV Index: {measurement['uv']} ({uv_description})\n"
                f"🔆​ Light Intensity: {measurement['lux']} lux\n"
                f"🌡️ Temperature: {measurement['temperature']}°C\n"
                f"⏲️ Pressure: {measurement['pressure']} hPa\n"
                f"💧 Humidity: {measurement['humidity']}%\n"
                f"🫁 PM1: {measurement['pm1']} µg/m³ ({pm1_description})\n"
                f"💨 PM2.5: {measurement['pm2_5']} µg/m³ ({pm2_5_description})\n"
                f"🌫️ PM10: {measurement['pm10']} µg/m³ ({pm10_description})\n"
                f"🌪️ Wind Speed: {measurement['wind_speed']} m/s\n"
                f"🌧️ Rainfall: {measurement['rain']} mm\n"
                f"🧭​ Wind Direction: {measurement['wind_direction']}\n"
                f"{technical_issues_message}"
            )
            
            bot.send_message(chat_id, formatted_data, reply_markup=command_markup, parse_mode='HTML')
            bot.send_message(chat_id, '''For the next measurement, select\t
/Current 📍 every quarter of the hour. 🕒​''')
        else:
            bot.send_message(chat_id, "⚠️ Error retrieving data. Please try again later.", reply_markup=command_markup)
    else:
        bot.send_message(chat_id, "⚠️ Device not found. ❌​")


def get_command_menu():
    command_markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    command_markup.add(
        types.KeyboardButton('/Current 📍'),
        types.KeyboardButton('/Change_device 🔄'),
        types.KeyboardButton('/Help ❓'),
        types.KeyboardButton('/Website 🌐'),
        types.KeyboardButton('/Map 🗺️'),
        #types.KeyboardButton('/Share_location 🌍​'),
    )
    return command_markup

@bot.message_handler(commands=['Current'])
def get_current_data(message):
    chat_id = message.chat.id
    command_markup = get_command_menu()
    
    if chat_id in user_context and 'device_id' in user_context[chat_id]:
        device_id = user_context[chat_id]['device_id']
        selected_device = user_context[chat_id].get('selected_device')
        measurement = fetch_latest_measurement(device_id)
        if measurement:
            uv_description = uv_index(measurement['uv'])
            pm1_description = pm_level(measurement['pm1'], "PM1.0")
            pm2_5_description = pm_level(measurement['pm2_5'], "PM2.5")
            pm10_description = pm_level(measurement['pm10'], "PM10")
            if selected_device in devices_with_issues:
                technical_issues_message = "\n⚠️ Note: At this moment this device has technical issues."
            else:
                technical_issues_message = ""
            formatted_data = (
                f"Latest Measurement in <b>{selected_device}</b> {measurement['timestamp']}\n\n"
                f"☀️ UV Index: {measurement['uv']} ({uv_description})\n"
                f"🔆​ Light Intensity: {measurement['lux']} lux\n"
                f"🌡️ Temperature: {measurement['temperature']}°C\n"
                f"⏲️ Pressure: {measurement['pressure']} hPa\n"
                f"💧 Humidity: {measurement['humidity']}%\n"
                f"🫁 PM1: {measurement['pm1']} µg/m³ ({pm1_description})\n"
                f"💨 PM2.5: {measurement['pm2_5']} µg/m³ ({pm2_5_description})\n"
                f"🌫️ PM10: {measurement['pm10']} µg/m³ ({pm10_description})\n"
                f"🌪️ Wind Speed: {measurement['wind_speed']} m/s\n"
                f"🌧️ Rainfall: {measurement['rain']} mm\n"
                f"🧭​ Wind Direction: {measurement['wind_direction']}\n"
                f"{technical_issues_message}"
            )
            bot.send_message(chat_id, formatted_data, reply_markup=command_markup, parse_mode='HTML')
            bot.send_message(chat_id, '''For the next measurement, select\t
/Current 📍 every quarter of the hour. 🕒​''')
        else:
            bot.send_message(chat_id, "⚠️ Error retrieving data. Please try again later.", reply_markup=command_markup)
    else:
        bot.send_message(chat_id, "⚠️ Please select a device first using /Change_device 🔄.", reply_markup=command_markup)

@bot.message_handler(commands=['Help'])
def help(message):
    bot.send_message(message.chat.id, '''
<b>/Current 📍:</b> Get the latest climate data in selected location.\n
<b>/Change_device 🔄:</b> Change to another climate monitoring device.\n
<b>/Help ❓:</b> Show available commands.\n
<b>/Website 🌐:</b> Visit our website for more information.\n
<b>/Map 🗺️​:</b> View the locations of all devices on a map.\n
''', parse_mode='HTML')

#For future add <b>/Share_location 🌍​:</b> Share your location.\n in commands=['Help']

@bot.message_handler(commands=['Change_device'])
def change_device(message):
    chat_id = message.chat.id

    if chat_id in user_context:
        user_context[chat_id].pop('selected_device', None)
        user_context[chat_id].pop('device_id', None)
    send_location_selection(chat_id)

@bot.message_handler(commands=['Website'])
def website(message):
    markup = types.InlineKeyboardMarkup()
    button = types.InlineKeyboardButton('Visit Website', url='https://climatenet.am/en/')
    markup.add(button)

    bot.send_message(
        message.chat.id,
        'For more information, click the button below to visit our official website: 🖥️​',
        reply_markup=markup
    )

# @bot.message_handler(commands=['Check_safety'])
# def check_safety(message):
#     chat_id = message.chat.id

#     if chat_id not in user_context or 'device_id' not in user_context[chat_id]:
#         bot.send_message(chat_id, "⚠️ Please select a device first using /Change_device 🔄.")
#         return

#     device_id = user_context[chat_id]['device_id']
#     selected_device = user_context[chat_id].get('selected_device')
#     measurement = fetch_latest_measurement(device_id)

#     if not measurement:
#         bot.send_message(chat_id, "⚠️ Failed to retrieve data for the selected device.")
#         return
#     alerts_triggered = []
#     for measurement_type, threshold in thresholds.items():
#         if measurement_type == "PM2.5":
#             key = "pm2_5"
#         else:
#             key = measurement_type.lower().replace(' ', '_')
#         value = measurement.get(key)

#         if value is not None and value > threshold:
#             if measurement_type == "UV Index":
#                 alerts_triggered.append(f"💥​ UV Index: {value} is high! Use sunscreen and avoid direct sun exposure.")
#             elif measurement_type == "Temperature":
#                 alerts_triggered.append(f"🔥 Temperature: {value}°C is dangerously high! Stay hydrated and avoid outdoor activities.")
#             elif measurement_type == "PM1":
#                 alerts_triggered.append(f"🫁​ PM1: {value} µg/m³ exceeds safe limits! Air quality is poor, avoid outdoor activities.")
#             elif measurement_type == "PM2.5":
#                 alerts_triggered.append(f"😷​ PM2.5: {value} µg/m³ exceeds safe limits! Air quality is poor, consider wearing a mask.")
#             elif measurement_type == "PM10":
#                 alerts_triggered.append(f"🚨 PM10: {value} µg/m³ exceeds the recommended threshold! Stay indoors to reduce exposure to harmful air.")
#             elif measurement_type == "Wind Speed":
#                 alerts_triggered.append(f"​🌪️ Wind Speed: {value} m/s is dangerously high! Secure loose items and stay indoors.")
#             elif measurement_type == "Rainfall":
#                 alerts_triggered.append(f"🌧️ Rainfall: {value} mm/h is too high! Take shelter and avoid outdoor activities.")

#     if alerts_triggered:
#         alert_message = "<b>🚨 **WARNING!** 🚨</b>\n\n" + "\n".join(alerts_triggered)
#         bot.send_message(chat_id, alert_message, parse_mode='HTML')
#     else:
#         bot.send_message(chat_id, "✅ All measurements are within safe limits.")

@bot.message_handler(commands=['Map'])
def map(message):
    chat_id = message.chat.id
    image = 'https://images-in-website.s3.us-east-1.amazonaws.com/Bot/map.png'
    bot.send_photo(chat_id, photo = image)
    bot.send_message(chat_id, 
'''📌 The highlighted locations indicate the current active climate devices. 🗺️ ''')

@bot.message_handler(content_types=['audio', 'document', 'photo', 'sticker', 'video', 'video_note', 'voice', 'contact', 'venue', 'animation'])
def handle_media(message):
    bot.send_message(
        message.chat.id,
        '''❗ Please use a valid command.
You can see all available commands by typing /Help❓
''')

@bot.message_handler(func=lambda message: not message.text.startswith('/'))
def handle_text(message):
    bot.send_message(
        message.chat.id,
        '''❗ Please use a valid command.
You can see all available commands by typing /Help❓
''')

# @bot.message_handler(commands=['Share_location'])
# def request_location(message):
#     location_button = types.KeyboardButton("📍 Share Location", request_location=True)
#     markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
#     markup.add(location_button)
#     bot.send_message(
#         message.chat.id,
#         "Click the button below to share your location 🔽​",
#         reply_markup=markup
#     )

# @bot.message_handler(content_types=['location'])
# def handle_location(message):
#     user_location = message.location
#     if user_location:
#         latitude = user_location.latitude
#         longitude = user_location.longitude
#         res = f"{longitude},{latitude}"
#         save_users_locations(from_user=message.from_user.id, location=res)
#         command_markup = get_command_menu()
#         bot.send_message(
#             message.chat.id,
#             "Select other commands to continue ▶️​",
#             reply_markup=command_markup
#         )
#     else:
#         bot.send_message(
#             message.chat.id,
#             "Failed to get your location. Please try again."
#         )

if __name__ == "__main__":
    start_bot_thread()
# def run_bot_view(request):
#     start_bot_thread()
#     return JsonResponse({'status': 'Bot is running in the background!'})