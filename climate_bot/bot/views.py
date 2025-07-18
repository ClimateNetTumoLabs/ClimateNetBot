from django.http import JsonResponse
from django.views import View
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
from users.utils import save_telegram_user, save_users_locations
from BotAnalytics.views import log_command_decorator, save_selected_device_to_db
import uuid
from string import Template
import math
import logging
import asyncio
from playwright.async_api import async_playwright
import traceback
from datetime import datetime, timezone
import pytz

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


load_dotenv()


TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not set in environment variables")
    raise ValueError("TELEGRAM_BOT_TOKEN not set")


bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)


def get_device_data():
    url = "https://dev.climatenet.am/device_inner/list/"
    logger.debug(f"Fetching device data from {url}")
    try:
        response = requests.get(url)
        response.raise_for_status()
        devices = response.json()
        locations = defaultdict(list)
        device_ids = {}
        device_issues = {}
        for device in devices:
            device_ids[device["name"]] = device["generated_id"]
            locations[device.get("parent_name", "Unknown")].append(device["name"])
            issues = device.get("issues", [])
            if issues:
                device_issues[device["name"]] = issues
        logger.debug(f"Loaded {len(device_ids)} devices")
        return locations, device_ids, device_issues
    except requests.RequestException as e:
        logger.error(f"Error fetching device data: {e}")
        return {}, {}, {}

locations, device_ids, device_issues = get_device_data()
user_context = {}
devices_with_issues = set(device_issues.keys())


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


def run_bot():
    while True:
        try:
            start_bot()
        except Exception as e:
            logger.error(f"Bot polling error: {e}")
            time.sleep(15)


def start_bot_thread():
    bot_thread = threading.Thread(target=run_bot)
    bot_thread.start()


def send_location_selection(chat_id):
    location_markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    for country in locations.keys():
        location_markup.add(types.KeyboardButton(country))
    bot.send_message(chat_id, 'Please choose a Region: 📍', reply_markup=location_markup)


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


@bot.message_handler(func=lambda message: message.text in locations.keys())
@log_command_decorator
def handle_country_selection(message):
    selected_country = message.text
    chat_id = message.chat.id
    logger.debug(f"Country selected: {selected_country} for chat_id: {chat_id}")
    if chat_id in user_context and user_context[chat_id].get('compare_mode'):
        compare_devices = user_context[chat_id].get('compare_devices', [])
        device_number = len(compare_devices) + 1
        user_context[chat_id][f'compare_country_{device_number}'] = selected_country
        send_device_selection_for_compare(chat_id, selected_country, device_number)
        return
    user_context[chat_id] = {'selected_country': selected_country}
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
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
    if device_name not in device_issues:
        return ""
    issues = device_issues[device_name]
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
            issue_name = issue.get('name', 'Unknown Issue')
            issue_text +=f"<b>⚠️ {issue_name}</b>\n"
    return issue_text


def uv_index(uv, with_emoji = True):
    if uv is None:
        return "N/A"
    if uv < 3:
        label, emoji = "Low","🟢"
    elif 3 <= uv <= 5:
        label, emoji = "Moderate","🟡"
    elif 6 <= uv <= 7:
        label, empoji = "High","🟠"
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
            now = datetime.now(timezone.utc)
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            timestamp = timestamp.replace(tzinfo=timezone.utc)
            time_diff = now - timestamp
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
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            template = Template(f.read())
    except FileNotFoundError as e:
        logger.error(f"Error: {template_path} not found: {e}")
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


@bot.message_handler(func=lambda message: message.text in [device for devices in locations.values() for device in devices])
@log_command_decorator
def handle_device_selection(message):
    selected_device = message.text
    chat_id = message.chat.id
    logger.debug(f"Device selected: {selected_device} for chat_id: {chat_id}")

    _initialize_user_context(chat_id)

    device_id = device_ids.get(selected_device)
    if not device_id:
        _handle_device_not_found(chat_id, selected_device)
        return

    if user_context[chat_id].get('compare_mode'):
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
    markup = types.ReplyKeyboardMarkup(row_width=3, resize_keyboard=True)
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
        types.KeyboardButton('/Compare 🆚')
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
            formatted_data = get_formatted_data(measurement=measurement, selected_device=selected_device)
            bot.send_message(chat_id, formatted_data, reply_markup=command_markup, parse_mode='HTML')
            bot.send_message(chat_id, '''For the next measurement, select\t
/Current 📍 every quarter of the hour. 🕒''')
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
<b>/Compare🆚:</b> Compare data from multiple devices side by side.\n
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
    location_markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
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
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
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



