from keep_alive import keep_alive
keep_alive()  # Добавьте в начало, перед запуском бота
import os
import logging
import json
import aiohttp
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from aiogram.utils import executor

# Конфигурация
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "ВАШ_ТОКЕН")
ADMIN_ID = 7057452528  # Замените на ваш ID
API_BASE_URL = "https://scriptblox.com/api/script"
DATA_FILE = "bot_data.json"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
start_time = datetime.now()

# Состояния
class BroadcastState(StatesGroup):
    message = State()

class BanState(StatesGroup):
    user_id = State()
    reason = State()

class AdminStates(StatesGroup):
    waiting_for_channel = State()
    waiting_for_chat = State()

# База данных
class BotData:
    def __init__(self):
        self.user_data = {}
        self.search_history = {}
        self.banned_users = set()
        self.admin_channels = set()
        self.admin_chats = set()
        self.subscription_check = False
        self.current_searches = {}

    def save(self):
        with open(DATA_FILE, 'w') as f:
            json.dump({
                'user_data': self.user_data,
                'search_history': self.search_history,
                'banned_users': list(self.banned_users),
                'admin_channels': list(self.admin_channels),
                'admin_chats': list(self.admin_chats),
                'subscription_check': self.subscription_check
            }, f)

    def load(self):
        try:
            with open(DATA_FILE) as f:
                data = json.load(f)
                self.user_data = data.get('user_data', {})
                self.search_history = data.get('search_history', {})
                self.banned_users = set(data.get('banned_users', []))
                self.admin_channels = set(data.get('admin_channels', []))
                self.admin_chats = set(data.get('admin_chats', []))
                self.subscription_check = data.get('subscription_check', False)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.info("No data file found, starting fresh")

data = BotData()
data.load()

# Вспомогательные функции
async def safe_api_request(url: str) -> Optional[Dict]:
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    logger.warning(f"API request failed (attempt {attempt+1}): HTTP {response.status}")
                    await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"API request error (attempt {attempt+1}): {str(e)}")
            await asyncio.sleep(3)
    return None

async def check_subscription(user_id: int) -> bool:
    if not data.subscription_check:
        return True
    
    for channel_id in data.admin_channels:
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception as e:
            logger.error(f"Error checking channel {channel_id}: {str(e)}")
    
    for chat_id in data.admin_chats:
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception as e:
            logger.error(f"Error checking chat {chat_id}: {str(e)}")
    
    return True

def format_script(script: Dict, detailed: bool = False) -> str:
    text = [
        f"📌 <b>{script.get('title', 'Без названия')}</b>",
        f"🎮 Игра: {script.get('game', {}).get('name', 'Неизвестно')}",
        f"👁 Просмотры: {script.get('views', 0)}",
        f"⭐ Рейтинг: 👍 {script.get('likeCount', 0)} / 👎 {script.get('dislikeCount', 0)}"
    ]
    
    if detailed:
        features = script.get('features', 'Нет описания').replace('•', '  •')
        text.extend([
            f"\n🔧 <b>Характеристики:</b>\n{features}",
            f"\n👤 Автор: @{script.get('owner', {}).get('username', 'Неизвестен')}",
            f"🛡 Проверен: {'✅ Да' if script.get('verified') else '❌ Нет'}",
            f"🔑 Ключ: {'🔒 Требуется' if script.get('key') else '🔓 Не требуется'}",
            f"🔄 Обновлен: {script.get('updatedAt', script.get('createdAt', 'Неизвестно'))}"
        ])
    
    return "\n".join(text)

def create_script_keyboard(script_id: str, current_index: int, total: int) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup(row_width=3)
    
    # Кнопки навигации
    nav_buttons = []
    if current_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"prev_{current_index}"))
    
    nav_buttons.append(InlineKeyboardButton(f"{current_index + 1}/{total}", callback_data="position"))
    
    if current_index < total - 1:
        nav_buttons.append(InlineKeyboardButton("Далее ➡️", callback_data=f"next_{current_index}"))
    
    if nav_buttons:
        keyboard.row(*nav_buttons)
    
    # Основные кнопки
    keyboard.row(
        InlineKeyboardButton("📝 Получить скрипт", callback_data=f"get_{script_id}"),
        InlineKeyboardButton("ℹ️ Подробнее", callback_data=f"details_{script_id}")
    )
    
    return keyboard

# Основные команды
@dp.message_handler(commands=['start', 'help'])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    if user_id in data.banned_users:
        await message.answer("⛔ Ваш аккаунт заблокирован администратором")
        return
    
    data.user_data[user_id] = {
        "first_name": message.from_user.first_name,
        "username": message.from_user.username,
        "join_date": datetime.now().isoformat(),
        "last_active": datetime.now().isoformat()
    }
    data.save()
    
    await message.answer(
        "👋 Добро пожаловать в <b>Roblox Script Finder</b>!\n\n"
        "🔍 Я помогу найти скрипты для ваших любимых игр.\n"
        "Просто отправьте мне название игры или функции (например: 'авто ферма').",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔍 Начать поиск", switch_inline_query_current_chat="")
        )
    )

@dp.message_handler(commands=['menu'])
async def cmd_menu(message: types.Message):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔍 Поиск скриптов", callback_data="search"),
        InlineKeyboardButton("📖 История поисков", callback_data="history")
    )
    
    if message.from_user.id == ADMIN_ID:
        keyboard.add(InlineKeyboardButton("👑 Админ-панель", callback_data="admin"))
    
    await message.answer("📋 Главное меню:", reply_markup=keyboard)

@dp.message_handler(commands=['search'])
async def cmd_search(message: types.Message):
    user_id = message.from_user.id
    
    if user_id in data.banned_users:
        await message.answer("⛔ Ваш аккаунт заблокирован администратором")
        return
    
    if not await check_subscription(user_id):
        await message.answer("⚠️ Для использования бота необходимо подписаться на наши каналы!")
        return
    
    query = message.get_args()
    if not query:
        await message.answer("ℹ️ Укажите запрос для поиска. Пример: <code>/search авто ферма</code>")
        return
    
    await process_search(user_id, query, message.chat.id)

async def process_search(user_id: int, query: str, chat_id: int):
    try:
        msg = await bot.send_message(chat_id, f"🔍 Ищем скрипты по запросу: <code>{query}</code>...")
        
        url = f"{API_BASE_URL}/search?q={query}"
        api_data = await safe_api_request(url)
        
        if not api_data or not api_data.get('result', {}).get('scripts'):
            await msg.edit_text("❌ Не найдено скриптов по вашему запросу. Попробуйте изменить формулировку.")
            return
        
        scripts = api_data['result']['scripts']
        data.current_searches[user_id] = {
            "scripts": scripts,
            "query": query,
            "current_index": 0
        }
        
        if user_id not in data.search_history:
            data.search_history[user_id] = []
        data.search_history[user_id].append(query)
        data.save()
        
        await show_script(user_id, chat_id, msg.message_id)
        
    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        await bot.send_message(chat_id, "⚠️ Произошла ошибка при поиске. Пожалуйста, попробуйте позже.")

async def show_script(user_id: int, chat_id: int, edit_msg_id: int = None):
    if user_id not in data.current_searches:
        return
    
    search_data = data.current_searches[user_id]
    script = search_data["scripts"][search_data["current_index"]]
    script_id = script["_id"]
    
    caption = format_script(script)
    keyboard = create_script_keyboard(
        script_id,
        search_data["current_index"],
        len(search_data["scripts"])
    )
    
    try:
        if script.get('image'):
            if edit_msg_id:
                try:
                    await bot.edit_message_media(
                        InputMediaPhoto(
                            media=script['image'],
                            caption=caption
                        ),
                        chat_id=chat_id,
                        message_id=edit_msg_id,
                        reply_markup=keyboard
                    )
                    return
                except:
                    pass
            
            await bot.send_photo(
                chat_id,
                photo=script['image'],
                caption=caption,
                reply_markup=keyboard
            )
            if edit_msg_id:
                await bot.delete_message(chat_id, edit_msg_id)
        else:
            if edit_msg_id:
                await bot.edit_message_text(
                    caption,
                    chat_id=chat_id,
                    message_id=edit_msg_id,
                    reply_markup=keyboard
                )
            else:
                await bot.send_message(
                    chat_id,
                    caption,
                    reply_markup=keyboard
                )
    except Exception as e:
        logger.error(f"Error showing script: {str(e)}")
        raise

# Обработчики кнопок
@dp.callback_query_handler(lambda c: c.data.startswith('get_'))
async def cb_get_script(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    script_id = callback_query.data.split('_')[1]
    
    try:
        await bot.answer_callback_query(callback_query.id, "⏳ Получаем скрипт...")
        
        url = f"{API_BASE_URL}/fetch/{script_id}"
        script_data = await safe_api_request(url)
        
        if not script_data or not script_data.get('script'):
            await bot.answer_callback_query(callback_query.id, "❌ Скрипт не найден", show_alert=True)
            return
        
        script_text = script_data['script'].get('script', 'Содержимое скрипта недоступно')
        
        await bot.send_message(user_id, f"📝 <b>{script_data['script'].get('title', 'Скрипт')}</b>")
        
        chunk_size = 4000
        for i in range(0, len(script_text), chunk_size):
            chunk = script_text[i:i+chunk_size]
            await bot.send_message(
                user_id,
                f"<pre><code class=\"language-lua\">{chunk}</code></pre>",
                parse_mode="HTML"
            )
        
        await bot.answer_callback_query(callback_query.id, "✅ Скрипт отправлен в ЛС")
    except Exception as e:
        logger.error(f"Get script error: {str(e)}")
        await bot.answer_callback_query(callback_query.id, "⚠️ Ошибка при получении скрипта", show_alert=True)

@dp.callback_query_handler(lambda c: c.data.startswith('next_'))
async def cb_next_script(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in data.current_searches:
        await bot.answer_callback_query(callback_query.id, "❌ Сессия поиска не найдена")
        return
    
    search_data = data.current_searches[user_id]
    current_index = int(callback_query.data.split('_')[1])
    
    if current_index >= len(search_data["scripts"]) - 1:
        await bot.answer_callback_query(callback_query.id, "ℹ️ Это последний скрипт")
        return
    
    search_data["current_index"] = current_index + 1
    await show_script(user_id, callback_query.message.chat.id, callback_query.message.message_id)
    await bot.answer_callback_query(callback_query.id)

@dp.callback_query_handler(lambda c: c.data.startswith('prev_'))
async def cb_prev_script(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in data.current_searches:
        await bot.answer_callback_query(callback_query.id, "❌ Сессия поиска не найдена")
        return
    
    search_data = data.current_searches[user_id]
    current_index = int(callback_query.data.split('_')[1])
    
    if current_index <= 0:
        await bot.answer_callback_query(callback_query.id, "ℹ️ Это первый скрипт")
        return
    
    search_data["current_index"] = current_index - 1
    await show_script(user_id, callback_query.message.chat.id, callback_query.message.message_id)
    await bot.answer_callback_query(callback_query.id)

# Запуск бота
async def on_startup(dp):
    logger.info("Bot started")
    await bot.send_message(ADMIN_ID, "🤖 Бот запущен")

async def on_shutdown(dp):
    data.save()
    logger.info("Bot stopped")
    await bot.send_message(ADMIN_ID, "🛑 Бот остановлен")
    await dp.storage.close()
    await dp.storage.wait_closed()

if __name__ == '__main__':
    from keep_alive import keep_alive
    keep_alive()
    
    try:
        executor.start_polling(
            dp,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            skip_updates=True
        )
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        with open("crash.log", "a") as f:
            f.write(f"{datetime.now()} - {str(e)}\n")