from django.http import JsonResponse
from django.views import View
import requests
import telebot
from telebot import types
import threading
import os
from dotenv import load_dotenv
from bot.models import Device, TelegramSchedule
from collections import defaultdict
import django
from django.conf import settings
from users.utils import save_telegram_user, save_users_locations
from BotAnalytics.views import log_command_decorator, save_selected_device_to_db
import uuid
from string import Template
import math
import logging
import asyncio
from playwright.async_api import async_playwright
import traceback
from bot.device_manager import DeviceManager
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
import re

import time as time_module
from datetime import datetime, time, timedelta


logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


load_dotenv()


TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not set in environment variables")
    raise ValueError("TELEGRAM_BOT_TOKEN not set")


bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

device_manager = DeviceManager(
    api_url="https://climatenet.am/device_inner/list/",  
    refresh_interval= 86400,  #day  
    max_retries=3
)

device_manager.start_auto_update()


database_url = f"sqlite:///{os.path.join(settings.BASE_DIR, 'scheduler.db')}"
jobstores = {
    'default': SQLAlchemyJobStore(url=database_url)
}
executors = {
    'default': ThreadPoolExecutor(20),
}
job_defaults = {
    'coalesce': False,
    'max_instances': 3
}

scheduler = BackgroundScheduler(
    jobstores=jobstores, 
    executors=executors, 
    job_defaults=job_defaults
)
scheduler.start()

YEREVAN_TZ = pytz.timezone('Asia/Yerevan')

user_context = {}

def get_user_schedules(chat_id):
    return TelegramSchedule.objects.filter(chat_id=chat_id, is_active=True)

def create_schedule_record(chat_id, device_name, device_id, frequency, custom_times=None, job_ids=None):
    schedule, created = TelegramSchedule.objects.get_or_create(
        chat_id=chat_id,
        device_name=device_name,
        defaults={
            'device_id': device_id,
            'frequency': frequency,
            'custom_times': custom_times,
            'job_ids': job_ids or [],
            'is_active': True
        }
    )
    if not created:
        schedule.device_id = device_id
        schedule.frequency = frequency
        schedule.custom_times = custom_times
        schedule.job_ids = job_ids or []
        schedule.is_active = True
        schedule.save()
    return schedule

def delete_schedule_record(chat_id, device_name):
    try:
        schedule = TelegramSchedule.objects.get(chat_id=chat_id, device_name=device_name, is_active=True)
        schedule.is_active = False
        schedule.save()
        return schedule
    except TelegramSchedule.DoesNotExist:
        return None


def fetch_latest_measurement(device_id):
    url = f"https://climatenet.am/device_inner/{device_id}/latest/"
    logger.debug(f"Fetching measurement for device ID: {device_id}, URL: {url}")
    try:
        response = requests.get(url, timeout=10)
        logger.debug(f"API response status: {response.status_code}, content: {response.text}")
        if response.status_code == 200:
            data = response.json()
            if data:
                latest_measurement = data[0]
                timestamp = latest_measurement["time"].replace("T", " ")
                measurement = {
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
                logger.debug(f"Measurement fetched: {measurement}")
                return measurement
            else:
                logger.warning(f"No data returned for device ID: {device_id}")
                return None
        else:
            logger.error(f"API request failed with status: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"Error in fetch_latest_measurement: {e}")
        return None


def start_bot():
    logger.info("Starting bot polling")
    bot.polling(none_stop=True)
    logger.info('Bot polling stopped')

def run_bot():
    while True:
        try:
            start_bot()
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            break
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            logger.info("Restarting bot in 15 seconds...")
            time_module.sleep(15)

def start_bot_thread():
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()


def send_location_selection(chat_id):
    location_markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    locations = device_manager.get_locations()
    for country in locations.keys():
        location_markup.add(types.KeyboardButton(country))
    bot.send_message(chat_id, 'Please choose a Region: 📍', reply_markup=location_markup)

def has_power_internet_issue(device_name):
    try:
        device_issues = device_manager.get_device_issues()
        if device_name not in device_issues:
            return False
        
        issues = device_issues[device_name]
        if not isinstance(issues, list):
            return False
        
        for issue in issues:
            issue_name = issue.get('name', '') if isinstance(issue, dict) else str(issue)
            if 'Power' in issue_name or 'Internet' in issue_name:
                return True
        return False
    except Exception as e:
        logger.error(f"Error checking device issues for {device_name}: {e}")
        return False

@bot.message_handler(commands=['start'])
@log_command_decorator
def start(message):
    bot.send_message(
        message.chat.id,
        '🌤️ Welcome to ClimateNet! 🌧️'
    )
    save_telegram_user(message.from_user)
    bot.send_message(
        message.chat.id,
        f'''Hello {message.from_user.first_name}! 👋 I am your personal climate assistant.
With me, you can:
    🔹 Access current measurements of temperature, humidity, wind speed, and more, which are refreshed every 15 minutes for reliable updates.
'''
    )
    send_location_selection(message.chat.id)


@bot.message_handler(commands=['Compare'])
@log_command_decorator
def start_compare(message):
    chat_id = message.chat.id
    logger.debug(f"/Compare triggered for chat_id: {chat_id}")
    try:
        if chat_id not in user_context:
            user_context[chat_id] = {}
        user_context[chat_id]['compare_mode'] = True
        user_context[chat_id]['compare_devices'] = []
        send_location_selection_for_compare(chat_id, device_number=1)
    except Exception as e:
        logger.error(f"Error starting comparison: {e}")
        bot.send_message(chat_id, f"Error starting comparison: {e}")


@bot.message_handler(func=lambda message: message.text in device_manager.get_locations().keys())
@log_command_decorator
def handle_country_selection(message):
    selected_country = message.text
    chat_id = message.chat.id
    logger.debug(f"Country selected: {selected_country} for chat_id: {chat_id}")
    
    if (chat_id in user_context and 
        user_context[chat_id].get('schedule_state') == 'selecting_location'):
        logger.debug(f'Schedule country: {selected_country} chat_id: {chat_id}')
        
        user_context[chat_id]['schedule_country'] = selected_country
        user_context[chat_id]['schedule_state'] = 'selecting_device'

        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        locations = device_manager.get_locations()
        for device in locations[selected_country]:
            markup.add(types.KeyboardButton(device))
        markup.add(types.KeyboardButton('/Cancel_Schedule ❌'))
        
        bot.send_message(
            chat_id, 
            f'📅 Please choose a Location for Schedule: ✅', 
            reply_markup=markup
        )
        return
    
    if chat_id in user_context and user_context[chat_id].get('compare_mode'):
        compare_devices = user_context[chat_id].get('compare_devices', [])
        device_number = len(compare_devices) + 1
        user_context[chat_id][f'compare_country_{device_number}'] = selected_country
        send_device_selection_for_compare(chat_id, selected_country, device_number)
        return
    
    user_context[chat_id] = {'selected_country': selected_country}
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    locations = device_manager.get_locations()
    for device in locations[selected_country]:
        markup.add(types.KeyboardButton(device))
    markup.add(types.KeyboardButton('/Change_location'))
    bot.send_message(chat_id, 'Please choose a Location: ✅', reply_markup=markup)




def uv_index(uv):
    if uv is None:
        return "N/A"
    if uv < 3:
        return "Low 🟢"
    elif 3 <= uv <= 5:
        return "Moderate 🟡"
    elif 6 <= uv <= 7:
        return "High 🟠"
    elif 8 <= uv <= 10:
        return "Very High 🔴"
    else:
        return "Extreme 🟣"


def pm_level(pm, pollutant):
    if pm is None:
        return "N/A"
    thresholds = {
        "PM1.0": [50, 100, 150, 200, 300],
        "PM2.5": [12, 36, 56, 151, 251],
        "PM10": [54, 154, 254, 354, 504]
    }
    levels = [
        "Good 🟢",
        "Moderate 🟡",
        "Unhealthy for Sensitive Groups 🟠",
        "Unhealthy 🟠",
        "Very Unhealthy 🔴",
        "Hazardous 🔴"
    ]
    thresholds = thresholds.get(pollutant, [])
    for i, limit in enumerate(thresholds):
        if pm <= limit:
            return levels[i]
    return levels[-1]

def format_device_issues(device_name, html_format=False):
    try:
        device_issues = device_manager.get_device_issues()
        if device_name not in device_issues:
            return ""
        issues = device_issues[device_name]
        if not isinstance(issues, list):
            logger.error(f"Invalid issues format fpr {device_name}")
            return ""
        if not issues:
            return ""
        if html_format:
            issue_text = ""
            for issue in issues:
                issue_name = issue.get('name', 'Unknown Issue')
                issue_text += f"<p class=\"warning\">⚠️ {issue_name}</p>\n"
        else:    
            issue_text = "<b>𝗧𝗲𝗰𝗵𝗻𝗶𝗰𝗮𝗹 𝗜𝘀𝘀𝘂𝗲𝘀</b>\n"
            for issue in issues:
                issue_name = issue.get('name', 'Unknown Issue') if isinstance(issue, dict) else str(issue)
                issue_text +=f"<b>⚠️ {issue_name}</b>\n"
        return issue_text
    except Exception as e:
        logger.error(f"Error in format_device_issues for {device_name}: {e}")


def uv_index(uv, with_emoji = True):
    if uv is None:
        return "N/A"
    if uv < 3:
        label, emoji = "Low","🟢"
    elif 3 <= uv <= 5:
        label, emoji = "Moderate","🟡"
    elif 6 <= uv <= 7:
        label, emoji = "High","🟠"
    elif 8 <= uv <= 10:
        label, emoji = "Very High","🔴"
    else:
        label, emoji = "Extreme","🟣"

    return f"{label} {emoji}" if with_emoji else label


def pm_level(pm, pollutant, with_emoji = True):
    if pm is None:
        return "N/A"
    thresholds = {
        "PM1.0": [50, 100, 150, 200, 300],
        "PM2.5": [12, 36, 56, 151, 251],
        "PM10": [54, 154, 254, 354, 504]
    }
    levels = [
        ("Good", "🟢"),
        ("Moderate", "🟡"),
        ("Unhealthy for Sensitive Groups", "🟠"),
        ("Unhealthy", "🟠"),
        ("Very Unhealthy", "🔴"),
        ("Hazardous", "🔴")
    ]
    thresholds = thresholds.get(pollutant, [])
    for i, limit in enumerate(thresholds):
        if pm <= limit:
            label, emoji = levels[i]
            return f"{label} {emoji}" if with_emoji else label
    label, emoji = levels[-1]
    return f"{label} {emoji}" if with_emoji else label


def get_formatted_data(measurement, selected_device):
    logger.debug(f"Formatting data for device: {selected_device}")
    def safe_value(value, unit="", is_round=False):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "N/A"
        return f"{round(value)}{unit}" if is_round else f"{value}{unit}"

    uv_description = uv_index(measurement.get('uv'))
    pm1_description = pm_level(measurement.get('pm1'), 'PM1.0')
    pm2_5_description = pm_level(measurement.get('pm2_5'), 'PM2.5')
    pm10_description = pm_level(measurement.get('pm10'), 'PM10')

    technical_issues_message = format_device_issues(selected_device)
    logger.debug(f"{technical_issues_message}")

    return (
        f"<b>𝗟𝗮𝘁𝗲𝘀𝘁 𝗠𝗲𝗮𝘀𝘂𝗿𝗲𝗺𝗲𝗻𝘁</b>\n"
        f"🔹 <b>Location:</b> <b>{selected_device}</b>\n"
        f"🔹 <b>Timestamp:</b> {safe_value(measurement.get('timestamp'))}\n\n"
        f"<b> 𝗟𝗶𝗴𝗵𝘁 𝗮𝗻𝗱 𝗨𝗩 𝗜𝗻𝗳𝗼𝗿𝗺𝗮𝘁𝗶𝗼𝗻</b>\n"
        f"☀️ <b>UV Index:</b> {safe_value(measurement.get('uv'))} ({uv_description})\n"
        f"🔆 <b>Light Intensity:</b> {safe_value(measurement.get('lux'))} lux\n\n"
        f"<b> 𝗘𝗻𝘃𝗶𝗿𝗼𝗻𝗺𝗲𝗻𝘁𝗮𝗹 𝗖𝗼𝗻𝗱𝗶𝘁𝗶𝗼𝗻𝘀</b>\n"
        f"🌡️ <b>Temperature:</b> {safe_value(measurement.get('temperature'), is_round=True)}°C\n"
        f"⏲️ <b>Atmospheric Pressure:</b> {safe_value(measurement.get('pressure'))} hPa\n"
        f"💧 <b>Humidity:</b> {safe_value(measurement.get('humidity'))}%\n\n"
        f"<b> 𝗔𝗶𝗿 𝗤𝘂𝗮𝗹𝗶𝘁𝘆 𝗟𝗲𝘃𝗲𝗹𝘀</b>\n"
        f"🫁 <b>PM1.0:</b> {safe_value(measurement.get('pm1'))} µg/m³  ({pm1_description})\n"
        f"💨 <b>PM2.5:</b> {safe_value(measurement.get('pm2_5'))} µg/m³ ({pm2_5_description})\n"
        f"🌫️ <b>PM10:</b> {safe_value(measurement.get('pm10'))} µg/m³ ({pm10_description})\n\n"
        f"<b>𝗪𝗲𝗮𝘁𝗵𝗲𝗿 𝗖𝗼𝗻𝗱𝗶𝘁𝗶𝗼𝗻 </b>\n"
        f"🌪️ <b>Wind Speed:</b> {safe_value(measurement.get('wind_speed'))} m/s\n"
        f"🌧️ <b>Rainfall:</b> {safe_value(measurement.get('rain'))} mm\n"
        f"🧭 <b>Wind Direction:</b> {safe_value(measurement.get('wind_direction'))}\n\n"
        f"{technical_issues_message}"
    )

def get_comparison_formatted_data(devices, measurements):
    logger.debug(f"Generating comparison data for {len(devices)} devices")
    def safe_value(value, is_round=False):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "N/A"
        return f"{round(value)}" if is_round else f"{value}"

    def get_uv_desc(uv):
        return uv_index(uv, with_emoji=False) if uv is not None else "N/A"

    def get_pm_desc(pm, pollutant):
        return pm_level(pm, pollutant, with_emoji=False) if pm is not None else "N/A"

    def get_status_class(description):
        if "Very High" in description or "Extreme" in description or "Hazardous" in description:
            return "status-dangerous"
        elif "Unhealthy" in description or "High" in description:
            return "status-unhealthy"
        elif "Moderate" in description:
            return "status-moderate"
        elif "Good" in description or "Low" in description:
            return "status-good"
        return ""

    def get_timestamp_class(timestamp_str):
        if not timestamp_str or timestamp_str == "N/A":
            return "timestamp-outdated"
        try:
            now = datetime.now(YEREVAN_TZ)
            
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            timestamp = timestamp.replace(tzinfo=pytz.UTC)  
            
            timestamp_yerevan = timestamp.astimezone(YEREVAN_TZ)
            
            time_diff = now - timestamp_yerevan
            time_diff_min = time_diff.total_seconds() / 60
            
            if time_diff_min <= 15:
                return "timestamp-uptodate"
            else:
                return "timestamp-outdated"
        except Exception as e:
            logger.warning(f"Error handling {timestamp_str} data (outdated or not): {e}")
            return "timestamp-outdated"

    template_path = os.path.join(settings.BASE_DIR, 'bot', 'templates', 'bot', 'comparison.html')
    logger.debug(f"Template path: {os.path.abspath(template_path)}, Exists: {os.path.exists(template_path)}")
    
    if not os.path.exists(template_path):
        logger.error(f"Template file not found: {template_path}")
        return None
    
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            template = Template(f.read())
    except FileNotFoundError as e:
        logger.error(f"Error: {template_path} not found: {e}")
        return None
    except Exception as e:
        logger.error(f"Error reading template: {e}")
        return None

    template_data = {}
    device_headers = ""
    timestamp_row = ""
    uv_row = ""
    lux_row = ""
    temperature_row = ""
    humidity_row = ""
    pressure_row = ""
    pm1_row = ""
    pm2_5_row = ""
    pm10_row = ""
    wind_speed_row = ""
    rain_row = ""
    wind_direction_row = ""
    technical_issues_row = ""

    has_issues = False
    issues_cells = ""
    for device in devices:
        device_name = device['name']
        issues = format_device_issues(device_name, html_format=True)
        if issues:
            has_issues = True
        issues_cells += f'<td class="device-cell"><div>{issues if issues else ""}</div></td>\n'

    if has_issues:
        technical_issues_row = f'<tr><td class="metric-cell">⚠️ Technical Problems</td>{issues_cells}</tr>'

    for idx, (device, measurement) in enumerate(zip(devices, measurements)):
        device_name = device['name']
        device_headers += f'<th class="device-header">🔹{device_name}</th>\n'

        timestamp_value = safe_value(measurement.get('timestamp'))
        timestamp_class = get_timestamp_class(timestamp_value)
        cell_class = f"timestamp-cell-{timestamp_class.replace('timestamp-', '')}"
        timestamp_row += f'<td class="device-cell {cell_class}"><div class="timestamp {timestamp_class}">{timestamp_value}</div></td>\n'
        uv_row += f'<td class="device-cell"><div class="value {get_status_class(get_uv_desc(measurement.get("uv")))}">{safe_value(measurement.get("uv"))}</div><div class="description">{get_uv_desc(measurement.get("uv"))}</div></td>\n'
        lux_row += f'<td class="device-cell"><div class="value">{safe_value(measurement.get("lux"))} lux</div></td>\n'
        temperature_row += f'<td class="device-cell"><div class="value">{safe_value(measurement.get("temperature"), is_round=True)}°C</div></td>\n'
        humidity_row += f'<td class="device-cell"><div class="value">{safe_value(measurement.get("humidity"))}%</div></td>\n'
        pressure_row += f'<td class="device-cell"><div class="value">{safe_value(measurement.get("pressure"))} hPa</div></td>\n'
        pm1_row += f'<td class="device-cell"><div class="value {get_status_class(get_pm_desc(measurement.get("pm1"), "PM1.0"))}">{safe_value(measurement.get("pm1"))} µg/m³</div><div class="description">{get_pm_desc(measurement.get("pm1"), "PM1.0")}</div></td>\n'
        pm2_5_row += f'<td class="device-cell"><div class="value {get_status_class(get_pm_desc(measurement.get("pm2_5"), "PM2.5"))}">{safe_value(measurement.get("pm2_5"))} µg/m³</div><div class="description">{get_pm_desc(measurement.get("pm2_5"), "PM2.5")}</div></td>\n'
        pm10_row += f'<td class="device-cell"><div class="value {get_status_class(get_pm_desc(measurement.get("pm10"), "PM10"))}">{safe_value(measurement.get("pm10"))} µg/m³</div><div class="description">{get_pm_desc(measurement.get("pm10"), "PM10")}</div></td>\n'
        wind_speed_row += f'<td class="device-cell"><div class="value">{safe_value(measurement.get("wind_speed"))} m/s</div></td>\n'
        rain_row += f'<td class="device-cell"><div class="value">{safe_value(measurement.get("rain"))} mm</div></td>\n'
        wind_direction_row += f'<td class="device-cell"><div class="value">{safe_value(measurement.get("wind_direction"))}</div></td>\n'

    template_data = {
        'device_headers': device_headers,
        'timestamp_row': timestamp_row,
        'uv_row': uv_row,
        'lux_row': lux_row,
        'temperature_row': temperature_row,
        'humidity_row': humidity_row,
        'pressure_row': pressure_row,
        'pm1_row': pm1_row,
        'pm2_5_row': pm2_5_row,
        'pm10_row': pm10_row,
        'wind_speed_row': wind_speed_row,
        'rain_row': rain_row,
        'wind_direction_row': wind_direction_row,
        'technical_issues_row': technical_issues_row,
    }
    
    logger.debug(f"Template data keys: {list(template_data.keys())}")
    try:
        html_content = template.substitute(template_data)
        logger.debug("HTML content generated successfully")
        return html_content
    except KeyError as e:
        logger.error(f"Template substitution error: Missing key {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected template error: {e}")
        return None
    
async def render_html_to_image(html_content, output_path):
    logger.debug(f"Rendering HTML to image at {output_path}")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            temp_html_path = f"temp_comparison_{uuid.uuid4()}.html"
            with open(temp_html_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            css_path = os.path.join(settings.BASE_DIR, 'bot', 'templates', 'bot', 'comparison.css')
            logger.debug(f"CSS path: {os.path.abspath(css_path)}, Exists: {os.path.exists(css_path)}")
            if not os.path.exists(css_path):
                raise FileNotFoundError(f"CSS file {css_path} not found")
            await page.goto(f"file://{os.path.abspath(temp_html_path)}")
            await page.set_viewport_size({"width": 1000, "height": 800})
            await page.screenshot(path=output_path, full_page=True)
            await browser.close()
            logger.debug(f"Screenshot saved to {output_path}")
            os.remove(temp_html_path)
    except Exception as e:
        logger.error(f"Playwright rendering error: {e}")
        raise

def inline_css_into_html(html, css_path):
    with open(css_path, 'r', encoding='utf-8') as f:
        css = f.read()
    return html.replace('<link rel="stylesheet" href="INLINE_CSS_HERE">', f"<style>{css}</style>")



def send_comparison_image(chat_id, html_content):
    if html_content is None:
        logger.error("HTML content is None")
        bot.send_message(chat_id, "⚠️ Error generating comparison table. Please try again.")
        return
    try:
        css_path = os.path.join(os.path.dirname(__file__), 'templates', 'bot', 'comparison.css')
        html_content = inline_css_into_html(html_content, css_path)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        temp_image_path = f"temp_comparison_{uuid.uuid4()}.png"
        loop.run_until_complete(render_html_to_image(html_content, temp_image_path))

        with open(temp_image_path, 'rb') as photo:
            bot.send_photo(chat_id, photo)

        os.remove(temp_image_path)
        logger.debug(f"Image sent and temporary file {temp_image_path} removed")
    except FileNotFoundError as e:
        logger.error(f"File error: {e}")
        bot.send_message(chat_id, "⚠️ CSS file missing. Please contact the administrator.")
    except Exception as e:
        logger.error(f"Error generating/sending image: {e}")
        traceback.print_exc()
        bot.send_message(chat_id, "⚠️ Error generating comparison image. Please try again.")


@bot.message_handler(func=lambda message: message.text in [device for devices in device_manager.get_locations().values() for device in devices]) 
@log_command_decorator
def handle_device_selection(message):
    selected_device = message.text
    chat_id = message.chat.id
    logger.debug(f"Device selected: {selected_device} for chat_id: {chat_id}")

    _initialize_user_context(chat_id)

    device_id = device_manager.get_device_id(selected_device)
    if not device_id:
        _handle_device_not_found(chat_id, selected_device)
        return

    if (chat_id in user_context and 
        user_context[chat_id].get('schedule_state') == 'selecting_device'):
        handle_schedule_device_selection_logic(chat_id, selected_device, device_id)
        return

    elif user_context[chat_id].get('compare_mode'):
        _handle_comparison_mode(chat_id, selected_device, device_id, message.from_user.id)
    else:
        _handle_normal_mode(chat_id, selected_device, device_id, message.from_user.id)
        
        
def _initialize_user_context(chat_id):
    if chat_id not in user_context:
        user_context[chat_id] = {}


def _handle_device_not_found(chat_id, selected_device):
    logger.error(f"Device ID not found for {selected_device}")
    bot.send_message(chat_id, "⚠️ Device not found. ❌")


def _handle_normal_mode(chat_id, selected_device, device_id, user_id):
    if has_power_internet_issue(selected_device):
        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add(types.KeyboardButton('/Back_to_Menu 🔙'))
        
        bot.send_message(
            chat_id,
            "⚠️ <b>No Recent Data Found</b>\n\n"
            "This device hasn't reported any measurements recently. Please check back later.",
            reply_markup=markup,
            parse_mode='HTML'
        )
        return
    
    user_context[chat_id]['selected_device'] = selected_device
    user_context[chat_id]['device_id'] = device_id

    save_selected_device_to_db(user_id=user_id, context=user_context[chat_id], device_id=device_id)

    _send_device_data_and_menu(chat_id, selected_device, device_id)


def _handle_comparison_mode(chat_id, selected_device, device_id, user_id):
    compare_devices = user_context[chat_id].get('compare_devices', [])

    if _is_device_already_selected(compare_devices, selected_device):
        bot.send_message(chat_id, f"❗Device {selected_device} is already selected.")
        return

    _add_device_to_comparison(chat_id, selected_device, device_id)
    device_count = len(user_context[chat_id]['compare_devices'])

    logger.debug(f"Added device {selected_device} (number {device_count}) to comparison")

    if device_count >= 5:
        _execute_comparison(chat_id)
    elif device_count >= 2:
        _prompt_for_more_devices(chat_id, selected_device, device_count)
    else:
        send_location_selection_for_compare(chat_id, device_number=device_count + 1)


def _is_device_already_selected(compare_devices, device_name):
    return any(device['name'] == device_name for device in compare_devices)


def _add_device_to_comparison(chat_id, device_name, device_id):
    compare_devices = user_context[chat_id].get('compare_devices', [])
    compare_devices.append({
        'name': device_name,
        'id': device_id
    })
    user_context[chat_id]['compare_devices'] = compare_devices


def _execute_comparison(chat_id):
    compare_devices = user_context[chat_id]['compare_devices']

    try:
        logger.debug(f"Comparing {len(compare_devices)} devices: {[d['name'] for d in compare_devices]}")

        measurements = _fetch_all_measurements(compare_devices)

        html_content = get_comparison_formatted_data(compare_devices, measurements)
        if html_content is None:
            raise Exception("Failed to generate HTML content")

        send_comparison_image(chat_id, html_content)

        command_markup = get_command_menu()
        bot.send_message(
            chat_id,
            "Comparison table sent as image above.",
            reply_markup=command_markup
        )

    except Exception as e:
        _handle_comparison_error(chat_id, e)
    finally:
        _clear_comparison_context(chat_id)


def _fetch_all_measurements(compare_devices):
    measurements = []
    for device in compare_devices:
        measurement = fetch_latest_measurement(device['id'])
        if not measurement:
            error_msg = f"Failed to fetch data for {device['name']} (ID: {device['id']})"
            logger.error(error_msg)
            raise Exception(error_msg)
        measurements.append(measurement)
    return measurements


def _handle_comparison_error(chat_id, error):
    logger.error(f"Comparison error: {error}")
    traceback.print_exc()

    error_msg = f"Error during comparison: {str(error)}"
    command_markup = get_command_menu()
    bot.send_message(chat_id, error_msg, reply_markup=command_markup)


def _clear_comparison_context(chat_id):
    user_context[chat_id].pop('compare_mode', None)
    user_context[chat_id].pop('compare_devices', None)

    keys_to_remove = [key for key in user_context[chat_id].keys() if key.startswith('compare_')]
    for key in keys_to_remove:
        user_context[chat_id].pop(key, None)

    logger.debug(f"Cleared comparison context for chat_id: {chat_id}")


def _prompt_for_more_devices(chat_id, selected_device, device_count):
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(types.KeyboardButton('/One_More ➕'))
    markup.add(types.KeyboardButton('/Cancel_Compare ❌'))
    markup.add(types.KeyboardButton('/Start_Comparing ✅'))

    bot.send_message(
        chat_id,
        f"Location {device_count} ({selected_device}) added. Want to add another device?",
        reply_markup=markup
    )


def _send_device_data_and_menu(chat_id, selected_device, device_id):
    command_markup = get_command_menu(cur=selected_device)
    measurement = fetch_latest_measurement(device_id)

    if measurement:
        formatted_data = get_formatted_data(measurement=measurement, selected_device=selected_device)
        bot.send_message(chat_id, formatted_data, reply_markup=command_markup, parse_mode='HTML')
        bot.send_message(chat_id, '''For the next measurement, select\t
/Current 📍 every quarter of the hour. 🕒''')
    else:
        logger.error(f"Failed to fetch measurement for {selected_device}")
        bot.send_message(chat_id, "⚠️ Error retrieving data. Please try again later.", reply_markup=command_markup)
@bot.message_handler(commands=['One_More'])
@log_command_decorator
def add_one_more_device(message):
    chat_id = message.chat.id
    logger.debug(f"/One_More triggered for chat_id: {chat_id}")
    if chat_id not in user_context or not user_context[chat_id].get('compare_mode'):
        bot.send_message(chat_id, "⚠️ Please start comparison with /Compare first.")
        return
    compare_devices = user_context[chat_id].get('compare_devices', [])
    if len(compare_devices) >= 5:
        return

    device_number = len(compare_devices) + 1
    send_location_selection_for_compare(chat_id, device_number=device_number)


@bot.message_handler(commands=['Start_Comparing'])
@log_command_decorator
def start_comparing(message):
    chat_id = message.chat.id
    logger.debug(f"/Start_Comparing triggered for chat_id: {chat_id}")
    if chat_id not in user_context or not user_context[chat_id].get('compare_mode'):
        bot.send_message(chat_id, "⚠️ Please start comparison with /Compare first.")
        return
    compare_devices = user_context[chat_id].get('compare_devices', [])
    if len(compare_devices) < 2:
        bot.send_message(chat_id, "⚠️ Please select at least two devices to compare.")
        return
    try:
        logger.debug(f"Comparing {len(compare_devices)} devices: {[d['name'] for d in compare_devices]}")
        measurements = []
        for device in compare_devices:
            measurement = fetch_latest_measurement(device['id'])
            if not measurement:
                logger.error(f"Failed to fetch data for {device['name']} (ID: {device['id']})")
                raise Exception(f"Failed to fetch data for {device['name']} (ID: {device['id']})")
            measurements.append(measurement)

        html_content = get_comparison_formatted_data(compare_devices, measurements)
        if html_content is None:
            logger.error("Failed to generate HTML content")
            raise Exception("Failed to generate HTML content")

        send_comparison_image(chat_id, html_content)
        command_markup = get_command_menu()
        bot.send_message(
            chat_id,
            "Comparison table sent as image above.",
            reply_markup=command_markup
        )
    except Exception as e:
        logger.error(f"Comparison error: {e}")
        traceback.print_exc()
        error_msg = f"⚠️ Error during comparison: {str(e)}. Please try again."
        command_markup = get_command_menu()
        bot.send_message(chat_id, error_msg, reply_markup=command_markup)
    finally:
        user_context[chat_id].pop('compare_mode', None)
        user_context[chat_id].pop('compare_devices', None)
        for key in list(user_context[chat_id].keys()):
            if key.startswith('compare_'):
                user_context[chat_id].pop(key, None)
        logger.debug(f"Cleared comparison context for chat_id: {chat_id}")


def get_command_menu(cur=None):
    if cur is None:
        cur = ""
    command_markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    command_markup.add(
        types.KeyboardButton(f'/Current 📍{cur}'),
        types.KeyboardButton('/Change_device 🔄'),
        types.KeyboardButton('/Help ❓'),
        types.KeyboardButton('/Website 🌐'),
        types.KeyboardButton('/Map 🗺️'),
        # types.KeyboardButton('/Share_location 🌍'),
        types.KeyboardButton('/Compare  🆚'),
        types.KeyboardButton('/Schedule ⏰')
    )
    return command_markup


@bot.message_handler(commands=['Current'])
@log_command_decorator
def get_current_data(message):
    chat_id = message.chat.id
    command_markup = get_command_menu()
    save_telegram_user(message.from_user)
    logger.debug(f"/Current triggered for chat_id: {chat_id}, User context: {user_context.get(chat_id, 'No context')}")
    if chat_id in user_context and 'device_id' in user_context[chat_id]:
        device_id = user_context[chat_id]['device_id']
        selected_device = user_context[chat_id].get('selected_device')
        logger.debug(f"Device ID: {device_id}, Selected Device: {selected_device}")
        command_markup = get_command_menu(cur=selected_device)
        measurement = fetch_latest_measurement(device_id)
        if measurement:
            try:
                formatted_data = get_formatted_data(measurement=measurement, selected_device=selected_device)
                bot.send_message(chat_id, formatted_data, reply_markup=command_markup, parse_mode='HTML')
                bot.send_message(chat_id, '''For the next measurement, select\n/Current 📍 every quarter of the hour. 🕒''')
            except Exception as e:
                logger.error(f"Failed to send message for {selected_device}: {e}")
                bot.send_message(chat_id, "⚠️ Error sending data. Please try again later.", reply_markup = command_markup)
        else:
            logger.error(f"Failed to fetch measurement for {selected_device}")
            bot.send_message(chat_id, "⚠️ Error retrieving data. Please try again later.", reply_markup=command_markup)
    else:
        bot.send_message(chat_id, "⚠️ Please select a device first using /Change_device 🔄.", reply_markup=command_markup)


@bot.message_handler(commands=['Help'])
@log_command_decorator
def help(message):
    bot.send_message(message.chat.id, '''
<b>/Current 📍:</b> Get the latest climate data in selected location.\n
<b>/Change_device 🔄:</b> Change to another climate monitoring device.\n
<b>/Help ❓:</b> Show available commands.\n
<b>/Website 🌐:</b> Visit our website for more information.\n
<b>/Map 🗺️:</b> View the locations of all devices on a map.\n
<b>/Compare 🆚:</b> Compare data from multiple devices side by side.\n
<b>/Schedule ⏰:</b> Set up automatic data update schedules.\n
''', parse_mode='HTML')


@bot.message_handler(commands=['Change_device'])
@log_command_decorator
def change_device(message):
    chat_id = message.chat.id
    if chat_id in user_context:
        user_context[chat_id].pop('selected_device', None)
        user_context[chat_id].pop('device_id', None)
    send_location_selection(chat_id)


@bot.message_handler(commands=['Change_location'])
@log_command_decorator
def change_location(message):
    chat_id = message.chat.id
    send_location_selection(chat_id)


@bot.message_handler(commands=['Website'])
@log_command_decorator
def website(message):
    markup = types.InlineKeyboardMarkup()
    button = types.InlineKeyboardButton('Visit Website', url='https://climatenet.am/en/')
    markup.add(button)
    bot.send_message(
        message.chat.id,
        'For more information, click the button below to visit our official website: 🖥️',
        reply_markup=markup
    )


@bot.message_handler(commands=['Map'])
@log_command_decorator
def map(message):
    chat_id = message.chat.id
    image = 'https://images-in-website.s3.us-east-1.amazonaws.com/Bot/map.png'
    bot.send_photo(chat_id, photo=image)
    bot.send_message(chat_id,
'''📌 The highlighted locations indicate the current active climate devices. 🗺️ ''')


def send_location_selection_for_compare(chat_id, device_number):
    location_markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    locations = device_manager.get_locations()
    if not locations:
        logger.error("No locations available")
        bot.send_message(chat_id, "⚠️ No locations available. Please try again later.")
        return
    for country in locations.keys():
        location_markup.add(types.KeyboardButton(country))
    location_markup.add(types.KeyboardButton('/Cancel_Compare ❌'))
    if device_number <=5:
        bot.send_message(
            chat_id,
            f"Please choose a Region {device_number}: 📍",
            reply_markup=location_markup
        )
    else:
        bot.send_message(chat_id, "Maximum of 5 devices is reached.")


def send_device_selection_for_compare(chat_id, selected_country, device_number):
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    locations = device_manager.get_locations()
    for device in locations[selected_country]:
        markup.add(types.KeyboardButton(device))
    markup.add(types.KeyboardButton('/Cancel_Compare ❌'))
    bot.send_message(
        chat_id,
        f'Please choose Location {device_number}: ✅',
        reply_markup=markup
    )


@bot.message_handler(commands=['Cancel_Compare'])
@log_command_decorator
def cancel_compare(message):
    chat_id = message.chat.id
    if chat_id in user_context:
        user_context[chat_id].pop('compare_mode', None)
        user_context[chat_id].pop('compare_devices', None)
        for key in list(user_context[chat_id].keys()):
            if key.startswith('compare_'):
                user_context[chat_id].pop(key, None)
    command_markup = get_command_menu()
    bot.send_message(
        chat_id,
        "Comparison cancelled. Back to the main menu.",
        reply_markup=command_markup
    )


@bot.message_handler(commands=['Schedule'])
@log_command_decorator
def schedule_menu(message):
    chat_id = message.chat.id
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(
        types.KeyboardButton('/Add_Schedule ➕'),
        types.KeyboardButton('/My_Schedules 📋'),
        types.KeyboardButton('/Schedule_Help ❓'),
        types.KeyboardButton('/Back_to_Menu 🔙')
    )
    bot.send_message(
        chat_id,
        "📅 <b>Schedule Menu</b>\n\n"
        "🔹 <b>Add Schedule:</b> Create new scheduled data retrieval\n"
        "🔹 <b>My Schedules:</b> View and manage your current schedules\n"
        "🔹 <b>Schedule Help:</b> Learn how scheduling works",
        reply_markup=markup,
        parse_mode='HTML'
    )
    

@bot.message_handler(commands=['Schedule_Help'])
@log_command_decorator 
def schedule_help(message):
    chat_id = message.chat.id
    help_text = """
🗓️ <b>Schedule Help</b>

<b>Time Options:</b>
🕒 <b>15 minutes</b> – Data every 15 min (at HH:00, HH:15, HH:30, HH:45)  
🕐 <b>Hourly</b> – Data every hour (at HH:00)  
🕐 <b>Custom</b> – Set any time (HH:MM)

<b>Manage Your Schedule:</b>
• View: /My_Schedules  
• Delete one or all: /Delete_Schedule or /Delete_All_Schedules  
• Limit: Max 5 schedules per user
"""
    bot.send_message(chat_id, help_text, parse_mode='HTML')

@bot.message_handler(commands=['Add_Schedule'])
@log_command_decorator
def add_schedule(message):
    chat_id = message.chat.id
    
    current_schedules = get_user_schedules(chat_id)
    if current_schedules.count() >= 5:
        bot.send_message(
            chat_id,
            "⚠️ Maximum 5 schedules allowed. Please delete some schedules first.",
            reply_markup=get_schedule_menu_markup()
        )
        return

    if chat_id not in user_context:
        user_context[chat_id] = {}
    
    user_context[chat_id]['schedule_state'] = 'selecting_location'
    user_context[chat_id]['schedule_mode'] = True 
    
    send_location_selection_for_schedule(chat_id)
    
def send_location_selection_for_schedule(chat_id):
    location_markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    locations = device_manager.get_locations()
    for country in locations.keys():
        location_markup.add(types.KeyboardButton(country))
    location_markup.add(types.KeyboardButton('/Cancel_Schedule ❌'))
    
    bot.send_message(
        chat_id,
        '📅 <b>Schedule Setup</b>\n\nPlease choose a Region for your scheduled updates: 📍',
        reply_markup=location_markup,
        parse_mode='HTML'
    )



@bot.message_handler(func=lambda message: (
    message.text in device_manager.get_locations().keys() and
    message.chat.id in user_context and
    user_context[message.chat.id].get('schedule_state') == 'selecting_location'))
@log_command_decorator
def handle_schedule_country_selection(message):
    selected_country = message.text
    chat_id = message.chat.id
    logger.debug(f'Schedule country: {selected_country} chat_id: {chat_id}')
    
    user_context[chat_id]['schedule_country'] = selected_country
    user_context[chat_id]['schedule_state'] = 'selecting_device'

    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    locations = device_manager.get_locations()
    for device in locations[selected_country]:
        markup.add(types.KeyboardButton(device))
    markup.add(types.KeyboardButton('/Cancel_Schedule ❌'))
    
    bot.send_message(
        chat_id, 
        'Please choose a Location: ✅', 
        reply_markup=markup
    )


def handle_schedule_device_selection_logic(chat_id, selected_device, device_id):
    if has_power_internet_issue(selected_device):
        schedule_keys = ['schedule_state', 'schedule_frequency', 'schedule_device',
                        'schedule_device_id', 'schedule_country']
        for key in schedule_keys:
            user_context[chat_id].pop(key, None)
        
        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add(
            types.KeyboardButton('/Add_Schedule ➕'),
            types.KeyboardButton('/Back_to_Menu 🔙')
        )
        
        bot.send_message(
            chat_id,
            "⚠️ <b>No Recent Data Found</b>\n\n"
            "This device hasn't reported any measurements recently. Please check back later.\n\n"
            "❗Schedule cannot be created for this device at the moment.",
            reply_markup=markup,
            parse_mode='HTML'
        )
        return

    user_context[chat_id]['schedule_device'] = selected_device
    user_context[chat_id]['schedule_device_id'] = device_id
    user_context[chat_id]['schedule_state'] = 'awaiting_frequency'
    
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(
        types.KeyboardButton('Every 15 minutes ⏰'),
        types.KeyboardButton('Every hour 🕐'),
        types.KeyboardButton('Custom times ⚙️'),
        types.KeyboardButton('/Cancel_Schedule ❌')
    )
    
    bot.send_message(
        chat_id,
        f"How often would you like to receive data?",
        reply_markup=markup,
        parse_mode='HTML'
    )

@bot.message_handler(commands=['Edit'])
@log_command_decorator
def edit_schedule_frequency(message):
    chat_id = message.chat.id
    try:
        if 'editing_device' not in user_context.get(chat_id, {}):
            bot.send_message(chat_id, "⚠️ No device selected for editing.")
            return
    
        existing_schedule = user_context[chat_id]['existing_schedule']
        for job_id in existing_schedule.get('job_ids', []):
            try:
                scheduler.remove_job(job_id)
            except Exception as e:
                logger.warning(f"Error removing job {job_id}: {e}")
    
        user_schedules[chat_id] = [
            s for s in user_schedules[chat_id] 
            if s['device'] != existing_schedule['device']
        ]
    
        user_context[chat_id]['schedule_device'] = existing_schedule['device']
        user_context[chat_id]['schedule_device_id'] = existing_schedule['device_id']
        user_context[chat_id]['schedule_state'] = 'awaiting_frequency'
    
        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add(
            types.KeyboardButton('Every 30 minutes ⏰'),
            types.KeyboardButton('Every hour 🕐'),
            types.KeyboardButton('Custom times ⚙️'),
            types.KeyboardButton('/Cancel_Schedule ❌')
        )
    
        bot.send_message(
            chat_id,
            f"✏️ <b>Editing Schedule for {existing_schedule['device']}</b>\n\n"
            f"The previous schedule has been removed.\n"
            f"Please select a new frequency:",
            reply_markup=markup,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Error in edit_schedule_frequency: {e}")
        bot.send_message(
            chat_id,
            "⚠️ An error occurred while editing the schedule. Please try again.",
            reply_markup=get_schedule_menu_markup()
        )
        for key in ['editing_device', 'existing_schedule']:
            user_context[chat_id].pop(key, None)


@bot.message_handler(func=lambda message: (
    message.text in ['Every 15 minutes ⏰', 'Every hour 🕐', 'Custom times ⚙️'] and
    message.chat.id in user_context and 
    user_context[message.chat.id].get('schedule_state') == 'awaiting_frequency'
))
@log_command_decorator
def handle_schedule_frequency(message):
    chat_id = message.chat.id
    frequency = message.text
    user_context[chat_id]['schedule_frequency'] = frequency
    
    logger.debug(f"Frequency selected: {frequency} for chat_id: {chat_id}")
    
    if frequency == 'Custom times ⚙️':
        user_context[chat_id]['schedule_state'] = 'awaiting_custom_time'
        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add(types.KeyboardButton('/Cancel_Schedule ❌'))
        
        bot.send_message(
            chat_id,
            "🕐 <b>Custom Schedule Times</b>\n\n"
            "Please enter specific times when you want to receive data.\n\n"
            "<b>Format:</b> HH:MM (24-hour format)\n"
            "<b>Multiple times:</b> separate with commas\n\n"
            "<b>Examples:</b>\n"
            "• Single time: <code>09:00</code>\n"
            "• Multiple times: <code>09:14, 18:37</code>\n"
            "• Maximum 8 times per schedule",
            reply_markup=markup,
            parse_mode='HTML'
        )
    else:
        create_schedule(chat_id, frequency)

@bot.message_handler(func=lambda message: (
    message.chat.id in user_context and 
    user_context[message.chat.id].get('schedule_state') == 'awaiting_custom_time' and
    not message.text.startswith('/') and
    message.content_type == 'text'
))
@log_command_decorator
def handle_custom_time(message):
    chat_id = message.chat.id
    time_input = message.text.strip()
    
    logger.debug(f"Processing custom time input: '{time_input}' for chat_id: {chat_id}")
    
    time_pattern = r'^\s*(\d{1,2}):(\d{2})\s*(?:,\s*(\d{1,2}):(\d{2})\s*)*$'
    
    if not re.match(time_pattern, time_input):
        logger.warning(f"Invalid time format for chat_id: {chat_id}, input: '{time_input}'")
        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add(types.KeyboardButton('/Cancel_Schedule ❌'))
        
        bot.send_message(
            chat_id,
            "⚠️ <b>Invalid Time Format</b>\n\n"
            "Please use HH:MM format with commas between multiple times.\n\n"
            "<b>Valid Examples:</b>\n"
            "• <code>09:00</code>\n"
            "• <code>12:30</code>\n"
            "• <code>09:00, 12:30, 18:45</code>\n\n"
            "Try again:",
            reply_markup=markup,
            parse_mode='HTML'
        )
        return
    
    times = [t.strip() for t in time_input.split(',')]
    validated_times = []
    
    for time_str in times:
        try:
            if ':' not in time_str:
                raise ValueError(f"Missing colon in time: {time_str}")
            
            hour_str, minute_str = time_str.split(':')
            hour = int(hour_str)
            minute = int(minute_str)
            
            if not (0 <= hour <= 23):
                raise ValueError(f"Hour {hour} out of range (0-23)")
            if not (0 <= minute <= 59):
                raise ValueError(f"Minute {minute} out of range (0-59)")
            
            validated_times.append(f"{hour:02d}:{minute:02d}")
            logger.debug(f"Validated time: {time_str} -> {hour:02d}:{minute:02d}")
            
        except (ValueError, IndexError) as e:
            logger.warning(f"Invalid time for chat_id: {chat_id}: '{time_str}', error: {e}")
            markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
            markup.add(types.KeyboardButton('/Cancel_Schedule ❌'))
            bot.send_message(
                chat_id,
                f"⚠️ Invalid time: <code>{time_str}</code>\n\n"
                f"Please use 24-hour format:\n"
                f"• Hours: 00-23\n"
                f"• Minutes: 00-59\n"
                f"• Format: HH:MM\n\n"
                f"Try again:",
                reply_markup=markup,
                parse_mode='HTML'
            )
            return
    
    if len(validated_times) > 8:
        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add(types.KeyboardButton('/Cancel_Schedule ❌'))
        bot.send_message(
            chat_id,
            f"⚠️ Maximum 8 custom times allowed.\n"
            f"You entered {len(validated_times)} times.\n\n"
            f"Please reduce the number of times:",
            reply_markup=markup
        )
        return
    
    seen = set()
    unique_times = []
    for time_str in validated_times:
        if time_str not in seen:
            seen.add(time_str)
            unique_times.append(time_str)
    
    logger.debug(f"Creating schedule with validated times: {unique_times}")
    
    try:
        create_schedule(chat_id, 'Custom times ⚙️', custom_times=unique_times)
    except Exception as e:
        logger.error(f"Failed to create custom schedule: {e}")
        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add(types.KeyboardButton('/Cancel_Schedule ❌'))
        bot.send_message(
            chat_id,
            f"⚠️ Failed to create schedule: {str(e)}\n\n"
            f"Please try again or contact support.",
            reply_markup=markup
        )


def create_schedule(chat_id, frequency, custom_times=None):
    try:
        selected_device = user_context[chat_id]['schedule_device']
        device_id = user_context[chat_id]['schedule_device_id']
        
        existing_schedules = TelegramSchedule.objects.filter(
            chat_id=chat_id, 
            device_name=selected_device, 
            is_active=True
        )
        for existing in existing_schedules:
            for job_id in existing.job_ids:
                try:
                    scheduler.remove_job(job_id)
                    logger.debug(f"Removed existing job {job_id}")
                except Exception as e:
                    logger.warning(f"Error removing existing job {job_id}: {e}")
            existing.is_active = False
            existing.save()
        
        base_job_id = f"schedule_{chat_id}_{selected_device}_{int(datetime.now().timestamp())}"
        job_ids = []
        next_run_time = None
        time_display = ""
        
        now = datetime.now(YEREVAN_TZ)
        
        if frequency == 'Every 15 minutes ⏰':
            earliest_next_run = None
            for minute in [0, 15, 30, 45]:
                job_id = f"{base_job_id}_{minute}"
                job = scheduler.add_job(
                    send_scheduled_data,
                    CronTrigger(minute=minute, timezone=YEREVAN_TZ),
                    args=[chat_id, device_id, selected_device],
                    id=job_id,
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True
                )
                job_ids.append(job_id)
                logger.debug(f"Scheduled job {job_id} for Every 15 minutes at minute {minute}")
                
                current_hour = now.hour
                if minute > now.minute:
                    next_scheduled = now.replace(minute=minute, second=0, microsecond=0)
                else:
                    next_scheduled = now.replace(minute=minute, second=0, microsecond=0) + timedelta(hours=1)
                
                if earliest_next_run is None or next_scheduled < earliest_next_run:
                    earliest_next_run = next_scheduled
            
            time_display = "every 15 minutes (at HH:00, HH:15, HH:30, HH:45)"
            next_run_time = earliest_next_run
            freq_code = '15min'
            
        elif frequency == 'Every hour 🕐':
            job_id = base_job_id
            job = scheduler.add_job(
                send_scheduled_data,
                CronTrigger(minute=0, timezone=YEREVAN_TZ),
                args=[chat_id, device_id, selected_device],
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True
            )
            time_display = "every hour (at HH:00)"
            job_ids.append(job_id)
            freq_code = '1hour'
            
            if now.minute == 0 and now.second < 30:
                next_run_time = now.replace(minute=0, second=0, microsecond=0)
            else:
                next_run_time = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            
        elif frequency == 'Custom times ⚙️' and custom_times:
            earliest_next_run = None
            
            for i, time_str in enumerate(custom_times):
                time_parts = time_str.split(':')
                hour = int(time_parts[0])
                minute = int(time_parts[1])
                
                job_id = f"{base_job_id}_{i}_{hour:02d}{minute:02d}"
                
                job = scheduler.add_job(
                    send_scheduled_data,
                    CronTrigger(hour=hour, minute=minute, timezone=YEREVAN_TZ),
                    args=[chat_id, device_id, selected_device],
                    id=job_id,
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True
                )
                job_ids.append(job_id)
                logger.info(f"Created custom schedule job {job_id} for {time_str}")
                
                today = now.date()
                scheduled_time = datetime.combine(today, time(hour, minute))
                scheduled_time = YEREVAN_TZ.localize(scheduled_time)
                
                if scheduled_time <= now:
                    scheduled_time += timedelta(days=1)
                
                if earliest_next_run is None or scheduled_time < earliest_next_run:
                    earliest_next_run = scheduled_time
            
            if not job_ids:
                raise ValueError("No jobs were created for custom times")
            
            time_display = f"at {', '.join(custom_times)}"
            next_run_time = earliest_next_run
            freq_code = 'custom'
        
        schedule_record = create_schedule_record(
            chat_id=chat_id,
            device_name=selected_device,
            device_id=device_id,
            frequency=freq_code,
            custom_times=custom_times,
            job_ids=job_ids
        )
        
        schedule_keys = ['schedule_state', 'schedule_frequency', 'schedule_device',
                        'schedule_device_id', 'schedule_country', 'custom_times']
        for key in schedule_keys:
            user_context[chat_id].pop(key, None)
        
        next_run_display = "Soon"
        if next_run_time:
            next_run_display = next_run_time.strftime('%Y-%m-%d %H:%M')
        
        markup = get_schedule_menu_markup()
        bot.send_message(
            chat_id,
            f"✅ <b>Schedule Created Successfully!</b>\n\n"
            f"📍 <b>Device:</b> {selected_device}\n"
            f"⏰ <b>Frequency:</b> {time_display}\n"
            f"📅 <b>Next Update:</b> {next_run_display}\n\n"
            f"You will now receive automatic updates {time_display}.",
            reply_markup=markup,
            parse_mode='HTML'
        )
        
    except Exception as e:
        logger.error(f"Error creating schedule: {e}")
        traceback.print_exc()
        bot.send_message(
            chat_id,
            f"⚠️ Error creating schedule: {str(e)}",
            reply_markup=get_schedule_menu_markup()
        )
        
def send_scheduled_data(chat_id, device_id, device_name):
    try:
        logger.info(f"Sending scheduled data for device {device_name} to chat {chat_id}")
        
        time_module.sleep(2)
        
        max_retries = 3
        measurement = None
        
        for attempt in range(max_retries):
            measurement = fetch_latest_measurement(device_id)
            if measurement:
                try:
                    timestamp_str = measurement.get('timestamp', '')
                    if timestamp_str and timestamp_str != "N/A":
                        data_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                        data_time = data_time.replace(tzinfo=pytz.UTC).astimezone(YEREVAN_TZ)
                        current_time = datetime.now(YEREVAN_TZ)
                        
                        time_diff = current_time - data_time
                        if time_diff.total_seconds() <= 1200: 
                            break 
                        else:
                            logger.warning(f"Data is {time_diff.total_seconds()/60:.1f} minutes old, retrying...")
                except Exception as e:
                    logger.warning(f"Could not parse timestamp for freshness check: {e}")
                    break
            
            if attempt < max_retries - 1: 
                time_module.sleep(5)
        
        if measurement:
            current_time = datetime.now(YEREVAN_TZ).strftime('%Y-%m-%d %H:%M')
            formatted_data = get_formatted_data(measurement=measurement, selected_device=device_name)
            
            complete_message = (
                f"🔔 <b>Scheduled Update</b>\n"
                f"🕐 <b>Update Time:</b> {current_time}\n\n"
                f"{formatted_data}"
            )
            
            bot.send_message(
                chat_id,
                complete_message,
                parse_mode='HTML'
            )
            
        else:
            bot.send_message(
                chat_id,
                f"⚠️ Scheduled update failed for {device_name}. Data unavailable at this time."
            )
    except Exception as e:
        logger.error(f"Error sending scheduled data: {e}")
        traceback.print_exc()
        try:
            bot.send_message(
                chat_id,
                f"⚠️ Error sending scheduled update for {device_name}. Please try manually."
            )
        except:
            pass

@bot.message_handler(commands=['My_Schedules'])
@log_command_decorator
def my_schedules(message):
    chat_id = message.chat.id
    
    schedules = get_user_schedules(chat_id)
    
    if not schedules.exists():
        markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add(
            types.KeyboardButton('/Add_Schedule ➕'),
            types.KeyboardButton('/Back_to_Menu 🔙')
        )
        bot.send_message(
            chat_id,
            "📋 <b>My Schedules</b>\n\n"
            "You don't have any scheduled data yet.\n"
            "Use 'Add Schedule' to create your first schedule.",
            reply_markup=markup,
            parse_mode='HTML'
        )
        return
    
    schedules_text = "📋 <b>My Schedules</b>\n\n"
    for i, schedule in enumerate(schedules, 1):
        if schedule.frequency == '15min':
            time_display = "Every 15 minutes (at HH:00, HH:15, HH:30, HH:45)"
        elif schedule.frequency == '1hour':
            time_display = "Every hour (at HH:00)"
        elif schedule.frequency == 'custom':
            time_display = f"at {', '.join(schedule.custom_times)} "
        else:
            time_display = "Unknown frequency"
        
        next_run_display = get_next_run_time_from_jobs(schedule.job_ids)
        
        schedules_text += (
            f" <b>📍{schedule.device_name}</b>\n"
            f" ⏳ {time_display}\n"
            f" 👉🏼 Next Update: {next_run_display}\n\n"
        )
    
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(
        types.KeyboardButton('/Add_Schedule ➕'),
        types.KeyboardButton('/Delete_Schedule 🗑️'),
        types.KeyboardButton('/Delete_All_Schedules ❌'),
        types.KeyboardButton('/Back_to_Menu 🔙')
    )
    
    bot.send_message(
        chat_id,
        schedules_text,
        reply_markup=markup,
        parse_mode='HTML'
    )

def get_next_run_time_from_jobs(job_ids):
    try:
        earliest_next_run = None
        
        for job_id in job_ids:
            try:
                job = scheduler.get_job(job_id)
                if job and hasattr(job, 'next_run_time') and job.next_run_time:
                    next_run_yerevan = job.next_run_time.astimezone(YEREVAN_TZ)
                    if earliest_next_run is None or next_run_yerevan < earliest_next_run:
                        earliest_next_run = next_run_yerevan
            except Exception as e:
                logger.warning(f"Could not get job {job_id}: {e}")
                continue
        
        if earliest_next_run:
            return earliest_next_run.strftime('%Y-%m-%d %H:%M')
        else:
            return "Scheduled"
            
    except Exception as e:
        logger.error(f"Error getting next run time: {e}")
        return "Unknown"
@bot.message_handler(commands=['Delete_Schedule'])
@log_command_decorator
def delete_schedule_menu(message):
    chat_id = message.chat.id
    
    schedules = get_user_schedules(chat_id)
    
    if not schedules.exists():
        bot.send_message(
            chat_id,
            "📋 No schedules to delete.",
            reply_markup=get_schedule_menu_markup()
        )
        return
    
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    for i, schedule in enumerate(schedules, 1):
        markup.add(types.KeyboardButton(f"Delete {i}: {schedule.device_name} 🗑️"))
    markup.add(types.KeyboardButton('/Cancel_Delete ❌'))
    
    user_context[chat_id]['delete_state'] = 'awaiting_selection'
    
    bot.send_message(
        chat_id,
        "🗑️ <b>Delete Schedule</b>\n\n"
        "Select which schedule to delete:",
        reply_markup=markup,
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda message: message.text.startswith('Delete ') and message.text.endswith(' 🗑️'))
@log_command_decorator
def handle_delete_selection(message):
    chat_id = message.chat.id
    
    if (chat_id not in user_context or 
        user_context[chat_id].get('delete_state') != 'awaiting_selection'):
        return
    
    try:
        schedule_num = int(message.text.split(':')[0].replace('Delete ', '')) - 1
        schedules = list(get_user_schedules(chat_id))
        
        if 0 <= schedule_num < len(schedules):
            schedule = schedules[schedule_num]
            
            removed_jobs = []
            failed_jobs = []
            
            for job_id in schedule.job_ids:
                try:
                    scheduler.remove_job(job_id)
                    removed_jobs.append(job_id)
                    logger.info(f"Successfully removed job: {job_id}")
                except Exception as e:
                    failed_jobs.append(job_id)
                    logger.warning(f"Failed to remove job {job_id}: {e}")
            
            schedule.is_active = False
            schedule.save()
            
            user_context[chat_id].pop('delete_state', None)
            
            status_msg = f"✅ Schedule deleted successfully!\n\n" \
                        f"📍 Device: {schedule.device_name}\n"
            
            if failed_jobs:
                status_msg += f"\n⚠️ {len(failed_jobs)} job(s) were already inactive"
            
            bot.send_message(
                chat_id,
                status_msg,
                reply_markup=get_schedule_menu_markup()
            )
        else:
            bot.send_message(chat_id, "⚠️ Invalid selection.")
    except (ValueError, IndexError) as e:
        logger.error(f"Error in delete selection: {e}")
        bot.send_message(chat_id, "⚠️ Invalid selection.")

@bot.message_handler(commands=['Delete_All_Schedules'])
@log_command_decorator
def delete_all_schedules(message):
    chat_id = message.chat.id
    
    schedules = get_user_schedules(chat_id)
    count = schedules.count()
    
    if count == 0:
        bot.send_message(
            chat_id,
            "📋 No schedules to delete.",
            reply_markup=get_schedule_menu_markup()
        )
        return
    
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(
        types.KeyboardButton('/Confirm_Delete_All ✅'),
        types.KeyboardButton('/Cancel_Delete ❌')
    )
    
    bot.send_message(
        chat_id,
        f"⚠️ <b>Delete All Schedules</b>\n\n"
        f"Are you sure you want to delete all {count} schedules?\n"
        f"‼️ This action cannot be undone.",
        reply_markup=markup,
        parse_mode='HTML'
    )

@bot.message_handler(commands=['Confirm_Delete_All'])
@log_command_decorator
def confirm_delete_all(message):
    chat_id = message.chat.id
    
    schedules = get_user_schedules(chat_id)
    count = schedules.count()
    total_jobs_removed = 0
    
    for schedule in schedules:
        for job_id in schedule.job_ids:
            try:
                scheduler.remove_job(job_id)
                total_jobs_removed += 1
                logger.info(f"Successfully removed job: {job_id}")
            except Exception as e:
                logger.warning(f"Job removal warning for {job_id}: {e}")
        
        schedule.is_active = False
        schedule.save()

    bot.send_message(
        chat_id,
        f"✅ All {count} schedules deleted successfully!\n",
        reply_markup=get_schedule_menu_markup()
    )

def restore_schedules_on_startup():
    try:
        existing_jobs = scheduler.get_jobs()
        for job in existing_jobs:
            try:
                scheduler.remove_job(job.id)
                logger.debug(f"Cleared existing job: {job.id}")
            except Exception as e:
                logger.warning(f"Could not clear job {job.id}: {e}")
        
        active_schedules = TelegramSchedule.objects.filter(is_active=True)
        restored_count = 0
        
        for schedule in active_schedules:
            try:
                base_job_id = f"schedule_{schedule.chat_id}_{schedule.device_name}_{int(datetime.now().timestamp())}"
                job_ids = []
                
                if schedule.frequency == '15min':
                    for minute in [0, 15, 30, 45]:
                        job_id = f"{base_job_id}_{minute}"
                        scheduler.add_job(
                            send_scheduled_data,
                            CronTrigger(minute=minute, timezone=YEREVAN_TZ),
                            args=[schedule.chat_id, schedule.device_id, schedule.device_name],
                            id=job_id,
                            replace_existing=True,
                            max_instances=1, 
                            coalesce=True
                        )
                        job_ids.append(job_id)
                
                elif schedule.frequency == '1hour':
                    job_id = base_job_id
                    scheduler.add_job(
                        send_scheduled_data,
                        CronTrigger(minute=0, timezone=YEREVAN_TZ),
                        args=[schedule.chat_id, schedule.device_id, schedule.device_name],
                        id=job_id,
                        replace_existing=True,
                        max_instances=1,
                        coalesce=True
                    )
                    job_ids = [job_id]
                
                elif schedule.frequency == 'custom' and schedule.custom_times:
                    for i, time_str in enumerate(schedule.custom_times):
                        hour, minute = map(int, time_str.split(':'))
                        job_id = f"{base_job_id}_{i}_{hour:02d}{minute:02d}"
                        scheduler.add_job(
                            send_scheduled_data,
                            CronTrigger(hour=hour, minute=minute, timezone=YEREVAN_TZ),
                            args=[schedule.chat_id, schedule.device_id, schedule.device_name],
                            id=job_id,
                            replace_existing=True,
                            max_instances=1,
                            coalesce=True
                        )
                        job_ids.append(job_id)
                
                schedule.job_ids = job_ids
                schedule.save()
                restored_count += 1
                logger.info(f"Restored schedule for {schedule.device_name} (Chat: {schedule.chat_id}) with {len(job_ids)} jobs")
                
            except Exception as e:
                logger.error(f"Failed to restore schedule {schedule.id}: {e}")
                continue
        
        logger.info(f"Successfully restored {restored_count} schedules")
        
    except Exception as e:
        logger.error(f"Error during schedule restoration: {e}")


restore_schedules_on_startup()

@bot.message_handler(commands=['Cancel_Schedule'])
@log_command_decorator
def cancel_schedule_action(message):
    chat_id = message.chat.id
    logger.debug(f"Cancel schedule triggered for chat_id: {chat_id}")
    
    schedule_keys = ['schedule_state', 'schedule_frequency', 'schedule_device', 
                    'schedule_device_id', 'schedule_country', 'custom_times']
    for key in schedule_keys:
        user_context[chat_id].pop(key, None)
    
    markup = get_schedule_menu_markup()
    bot.send_message(
        chat_id,
        "❌ Schedule creation cancelled. Back to schedule menu.",
        reply_markup=markup
    )


@bot.message_handler(commands=['Cancel_Delete'])
@log_command_decorator
def cancel_delete_action(message):
    chat_id = message.chat.id
    logger.debug(f"Cancel delete triggered for chat_id: {chat_id}")
    
    user_context[chat_id].pop('delete_state', None)
    
    markup = get_schedule_menu_markup()
    bot.send_message(
        chat_id,
        "❌ Delete action cancelled. Back to schedule menu.",
        reply_markup=markup
    )
@bot.message_handler(commands=['Back_to_Menu'])
@log_command_decorator
def back_to_main_menu(message):
    chat_id = message.chat.id
    logger.debug("Back to menu schedule")
    user_context[chat_id].pop('schedule_state', None)
    user_context[chat_id].pop('schedule_frequency', None)
    user_context[chat_id].pop('schedule_device', None)
    user_context[chat_id].pop('schedule_device_id', None)
    user_context[chat_id].pop('schedule_country', None)
    user_context[chat_id].pop('custom_times', None)
    user_context[chat_id].pop('delete_state', None)
    selected_device = user_context[chat_id].get('selected_device', '')
    markup = get_command_menu(cur=selected_device)
    bot.send_message(
        chat_id,
        "🔙 Back to main menu. How can I assist you?",
        reply_markup=markup
    )

def get_schedule_menu_markup():
    markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add(
        types.KeyboardButton('/Add_Schedule ➕'),
        types.KeyboardButton('/My_Schedules 📋'),
        types.KeyboardButton('/Schedule_Help ❓'),
        types.KeyboardButton('/Back_to_Menu 🔙')
    )
    return markup



@bot.message_handler(content_types=['audio', 'document', 'photo', 'sticker', 'video', 'video_note', 'voice', 'contact', 'venue', 'animation'])
@log_command_decorator
def handle_media(message):
    bot.send_message(
        message.chat.id,
        '''❗ Please use a valid command.
You can see all available commands by typing /Help❓
'''
    )


@bot.message_handler(func=lambda message: not message.text.startswith('/'))
@log_command_decorator
def handle_text(message):
    bot.send_message(
        message.chat.id,
        '''❗ Please use a valid command.
You can see all available commands by typing /Help❓
'''
    )


# @bot.message_handler(commands=['Share_location'])
# @log_command_decorator
# def request_location(message):
#     location_button = types.KeyboardButton("📍 Share Location", request_location=True)
#     markup = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True, one_time_keyboard=True)
#     back_to_menu_button = types.KeyboardButton("/back 🔙")
#     markup.add(location_button, back_to_menu_button)
#     bot.send_message(
#         message.chat.id,
#         "Click the button below to share your location 🔽",
#         reply_markup=markup
#     )


@bot.message_handler(commands=['back'])
def go_back_to_menu(message):
    bot.send_message(
        message.chat.id,
        "You are back to the main menu. How can I assist you?",
        reply_markup=get_command_menu()
    )


@bot.message_handler(content_types=['location'])
@log_command_decorator
def handle_location(message):
    user_location = message.location
    if user_location:
        latitude = user_location.latitude
        longitude = user_location.longitude
        res = f"{longitude},{latitude}"
        save_users_locations(from_user=message.from_user.id, location=res)
        command_markup = get_command_menu()
        bot.send_message(
            message.chat.id,
            "Select other commands to continue ▶️",
            reply_markup=command_markup
        )
    else:
        logger.error("Failed to receive location")
        bot.send_message(
            message.chat.id,
            "Failed to get your location. Please try again."
        )

"""
def detect_weather_condition(measurement):
    temperature = measurement.get("temperature")
    humidity = measurement.get("humidity")
    lux = measurement.get("lux")
    pm2_5 = measurement.get("pm2_5")
    uv = measurement.get("uv")
    wind_speed = measurement.get("wind_speed")
    if temperature is not None and temperature < 1 and humidity and humidity > 85:
        return "Possibly Snowing ❄️"
    elif lux is not None and lux < 100 and humidity and humidity > 90 and pm2_5 and pm2_5 > 40:
        return "Foggy 🌫️"
    elif lux and lux < 50 and uv and uv < 2:
        return "Cloudy ☁️"
    elif lux and lux > 5 and uv and uv > 2:
        return "Sunny ☀️"
    else:
        return "Cloudy ☁️"
"""

if __name__ == "__main__":
    start_bot_thread()


def run_bot_view(request):
    start_bot_thread()
    return JsonResponse({'status': 'Bot is running in the background!'})