import requests
#from django.http import JsonResponse
#from django.views import View
import json
import telebot
from telebot import types
import threading
import time
import os
from dotenv import load_dotenv
from bot.models import Device
from collections import defaultdict
import unicodedata

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

TRANSLATIONS_PATH = TRANSLATIONS_PATH = '../climate_bot/translations/translations.json'

def normalize_text(text):
    return unicodedata.normalize('NFC', text)

def load_translations():
    with open(TRANSLATIONS_PATH, 'r', encoding='utf-8') as file:
        return json.load(file)

translations = load_translations()
user_context = {}

def get_dynamic_locations(chat_id):
    """Fetch locations and device IDs dynamically based on the user's language."""
    return get_device_data(chat_id)


def get_translation(chat_id, key):
    user_language = user_context.get(chat_id, {}).get('language', 'en')
    return translations.get(user_language, {}).get(key, key)

def get_device_data(chat_id=None):
    """
    Retrieve device data based on user-selected language.
    """
    user_language = user_context.get(chat_id, {}).get('language', 'en')

    # Use language-specific fields
    name_field = 'name_hy' if user_language == 'hy' else 'name_en'
    parent_name_field = 'parent_name_hy' if user_language == 'hy' else 'parent_name_en'

    locations = defaultdict(list)
    device_ids = {}
    devices = Device.objects.all()

    for device in devices:
        device_name = getattr(device, name_field, device.name_en)  # Default to English if missing
        parent_name = getattr(device, parent_name_field, device.parent_name_en)
        device_ids[device_name] = device.generated_id
        locations[parent_name].append(device_name)  # No need to normalize here for Armenian

    # Debugging: Log final locations and device IDs
    print(f"Locations: {dict(locations)}")
    print(f"Device IDs: {device_ids}")

    return locations, device_ids


locations, device_ids = get_device_data()
#user_context = {}

def fetch_latest_measurement(device_id):
    url = f"https://climatenet.am/device_inner/{device_id}/latest/"
    response = requests.get(url)

    if response.status_code == 200:
        data = response.json()
        if data:
            latest_measurement = data[0]  
            timestamp = latest_measurement["time"].replace("T", "  ")
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
    chat_id = message.chat.id
    language_markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)

    language_markup.add(
        types.KeyboardButton("English 🇺🇸"),
        types.KeyboardButton("Հայերեն 🇦🇲")
    )
    
    bot.send_message(
        chat_id,
        "Choose your preferred language 🇺🇸 / Ընտրել նախընտրելի լեզուն 🇦🇲",
        reply_markup=language_markup
    )

    user_context[chat_id] = {}

@bot.message_handler(func=lambda message: normalize_text(message.text) in 
                     [normalize_text("English 🇺🇸"), normalize_text("Հայերեն 🇦🇲")])
def handle_language_selection(message):
    chat_id = message.chat.id
    language = 'en' if normalize_text(message.text) == normalize_text("English 🇺🇸") else 'hy'
    user_context[chat_id]['language'] = language
    bot.send_message(
        chat_id,
        get_translation(chat_id, 'start_welcome')
    )
    bot.send_message(
        chat_id,
        get_translation(chat_id, 'start_intro').format(first_name=message.from_user.first_name)
    )
    
    send_location_selection(chat_id)
    
def send_location_selection(chat_id):
    locations, _ = get_device_data(chat_id)
    location_markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)

    for country in locations.keys():
        location_markup.add(types.KeyboardButton(country))
    
    bot.send_message(chat_id, get_translation(chat_id, 'choose_location'), reply_markup=location_markup)

# Handling country selection
@bot.message_handler(func=lambda message: message.text)
def handle_country_selection(message):
    chat_id = message.chat.id
    selected_country = message.text

    # Fetch language-specific locations and devices
    locations, device_ids = get_dynamic_locations(chat_id)

    if selected_country in locations:
        user_context[chat_id] = {'selected_country': selected_country}  # Storing selected country in context
        markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)

        # Add devices for the selected country
        for device in locations[selected_country]:
            markup.add(types.KeyboardButton(device))

        # Send the message with the devices and translation
        bot.send_message(chat_id, get_translation(chat_id, 'choose_device'), reply_markup=markup)
    else:
        # Send error message if country is not found in the locations
        bot.send_message(chat_id, get_translation(chat_id, 'error_data'))

# Handling device selection
@bot.message_handler(func=lambda message: message.text)
def handle_device_selection(message):
    chat_id = message.chat.id
    selected_device = message.text
    print(get_translation(chat_id, 'uv_index')) 
    # Ensure the user has selected a country before selecting a device
    if 'selected_country' not in user_context.get(chat_id, {}):
        bot.send_message(chat_id, get_translation(chat_id, 'error_select_country'))
        return

    # Fetch updated locations and device IDs
    locations, device_ids = get_dynamic_locations(chat_id)

    # Debugging: Print locations and device IDs to verify they are correct
    print(f"Available locations: {locations}")
    print(f"Device IDs: {device_ids}")

    # Check if the selected device exists in the device_ids mapping
    device_id = device_ids.get(selected_device)

    # Debugging: Check if device_id is None
    if device_id is None:
        bot.send_message(chat_id, get_translation(chat_id, 'device_not_found'))
        print(f"Device '{selected_device}' not found in device_ids.")
        return

    # Update user context with selected device and device ID
    user_context[chat_id]['selected_device'] = selected_device
    user_context[chat_id]['device_id'] = device_id

    # Prepare the command menu markup
    command_markup = get_command_menu()

    # Fetch the latest measurement for the selected device
    measurement = fetch_latest_measurement(device_id)
    
    # Debugging: Print the fetched measurement data
    print(f"Fetched measurement data: {measurement}")
    
    if measurement:
        # Prepare formatted data for the message
        formatted_data = (
            f"<b>{get_translation(chat_id, 'latest_measurements')}</b> <b>{selected_device}</b> "
            f"{measurement['timestamp']} ({get_translation(chat_id, 'last_update')})\n\n"
            f"☀️ {get_translation(chat_id, 'uv_index')}: {measurement['uv']}\n"
            f"🔆​ {get_translation(chat_id, 'light_intensity')}: {measurement['lux']} {get_translation(chat_id, 'lux')}\n"
            f"🌡️ {get_translation(chat_id, 'temperature')}: {measurement['temperature']}°C\n"
            f"⏲️ {get_translation(chat_id, 'pressure')}: {measurement['pressure']} hPa\n"
            f"💧 {get_translation(chat_id, 'humidity')}: {measurement['humidity']}%\n"
            f"🫁​​ {get_translation(chat_id, 'pm1')}: {measurement['pm1']} µg/m³\n"
            f"💨​ {get_translation(chat_id, 'pm2_5')}: {measurement['pm2_5']} µg/m³\n"
            f"🌫️​ {get_translation(chat_id, 'pm10')}: {measurement['pm10']} µg/m³\n"
            f"🌪️ {get_translation(chat_id, 'wind_speed')}: {measurement['wind_speed']} m/s\n"
            f"🌧️ {get_translation(chat_id, 'rainfall')}: {measurement['rain']} mm\n"
            f"🧭​ {get_translation(chat_id, 'wind_direction')}: {measurement['wind_direction']}\n\n"
        )

        # Send the formatted measurement data along with command options
        bot.send_message(chat_id, formatted_data, reply_markup=command_markup, parse_mode='HTML')
        bot.send_message(chat_id, get_translation(chat_id, 'next_measurement_info'))
    else:
        # If no measurements are found, notify the user with the translated error message
        bot.send_message(chat_id, get_translation(chat_id, 'error_data'), reply_markup=command_markup)

def fetch_latest_measurement(device_id):
    # Example of how to print or log the data from the measurement fetching process
    print(f"Fetching measurement data for device ID: {device_id}")
    
    # Assuming the function retrieves the latest measurement (or returns None if unavailable)
    measurement = some_api_call_or_database_fetch(device_id)
    
    print(f"Measurement data fetched: {measurement}")
    
    return measurement

 # Should print the Armenian translation


# @bot.message_handler(func=lambda message: True)
# def handle_country_or_device_selection(message):
#     """
#     Unified handler to process both country and device selections.
#     """
#     chat_id = message.chat.id
#     user_data = user_context.get(chat_id, {})
#     locations = user_data.get('locations', {})
#     device_ids = user_data.get('device_ids', {})

#     selected_text = message.text

#     # Check if the message matches a location
#     if selected_text in locations:
#         handle_country_selection(message)
#     # Check if the message matches a device
#     elif selected_text in device_ids:
#         handle_device_selection(message)
#     else:
#         bot.send_message(chat_id, get_translation(chat_id, 'device_not_found'))


def get_command_menu():
    command_markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    command_markup.add(
        types.KeyboardButton('/Current 📍'),
        types.KeyboardButton('/Change_device 🔄'),
        types.KeyboardButton('/Help ❓'),
        types.KeyboardButton('/Website 🌐'),
        types.KeyboardButton('/Map 🗺️'),
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
            formatted_data = (
                f"<b>{get_translation(chat_id, 'latest_measurements')}</b> <b>{selected_device}</b> "
                f"{measurement['timestamp']} ({get_translation(chat_id, 'last_update')})\n\n"
                f"☀️ {get_translation(chat_id, 'uv_index')}: {measurement['uv']}\n"
                f"🔆​ {get_translation(chat_id, 'light_intensity')}: {measurement['lux']} {get_translation(chat_id, 'lux')}\n"
                f"🌡️ {get_translation(chat_id, 'temperature')}: {measurement['temperature']}°C\n"
                f"⏲️ {get_translation(chat_id, 'pressure')}: {measurement['pressure']} hPa\n"
                f"💧 {get_translation(chat_id, 'humidity')}: {measurement['humidity']}%\n"
                f"🫁​​ {get_translation(chat_id, 'pm1')}: {measurement['pm1']} µg/m³\n"
                f"💨​ {get_translation(chat_id, 'pm2_5')}: {measurement['pm2_5']} µg/m³\n"
                f"🌫️​ {get_translation(chat_id, 'pm10')}: {measurement['pm10']} µg/m³\n"
                f"🌪️ {get_translation(chat_id, 'wind_speed')}: {measurement['wind_speed']} m/s\n"
                f"🌧️ {get_translation(chat_id, 'rainfall')}: {measurement['rain']} mm\n"
                f"🧭​ {get_translation(chat_id, 'wind_direction')}: {measurement['wind_direction']}\n\n"
            )
            bot.send_message(chat_id, formatted_data, reply_markup=command_markup, parse_mode='HTML')
            bot.send_message(chat_id, get_translation(chat_id, 'next_measurement_info'))
        else:
            bot.send_message(chat_id, get_translation(chat_id, 'error_data'), reply_markup=command_markup)
    else:
        bot.send_message(chat_id, get_translation(chat_id, 'select_device_first'), reply_markup=command_markup)

@bot.message_handler(commands=['Help'])
def help(message):
    bot.send_message(message.chat.id, '''
<b>/Current 📍:</b> Get the latest climate data in selected location.\n
<b>/Change_device 🔄:</b> Change to another climate monitoring device.\n
<b>/Help ❓:</b> Show available commands.\n
<b>/Website 🌐:</b> Visit our website for more information.\n
<b>/Map 🗺️​:</b> View the locations of all devices on a map.
''', parse_mode='HTML')

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


@bot.message_handler(commands=['Map'])
def map(message):
    chat_id = message.chat.id
    image = 'https://images-in-website.s3.us-east-1.amazonaws.com/Bot/map.png'
    bot.send_photo(chat_id, photo = image)
    bot.send_message(chat_id, 
'''📌 The highlighted locations indicate the current active climate devices. 🗺️ ''')

@bot.message_handler(content_types=['audio', 'document', 'photo', 'sticker', 'video', 'video_note', 'voice', 'contact', 'location', 'venue', 'animation'])
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

if __name__ == "__main__":
    start_bot_thread()
# def run_bot_view(request):
#     start_bot_thread()
#     return JsonResponse({'status': 'Bot is running in the background!'})