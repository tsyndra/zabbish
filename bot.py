import os
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from ping3 import ping

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_CHAT_ID = int(os.getenv('ADMIN_CHAT_ID'))

# Словарь для хранения последнего известного состояния роутеров
router_states = {}

# Создаем директорию для логов, если её нет
os.makedirs('logs', exist_ok=True)

def load_routers():
    """Загрузка конфигурации роутеров из JSON файла"""
    try:
        with open('routers.json', 'r', encoding='utf-8') as f:
            config = json.load(f)
            return config.get('routers', [])
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации роутеров: {e}")
        return []

def check_router_status(router):
    """Проверка статуса одного роутера"""
    try:
        # Отправляем 5 пакетов и берем среднее значение (увеличено количество пингов и таймаут)
        response_times = []
        for _ in range(5):
            response_time = ping(router['ip'], timeout=3)
            if response_time is not None:
                response_times.append(response_time)
        
        # Если хотя бы один пакет дошел, считаем роутер доступным
        is_available = len(response_times) > 0
        avg_response_time = sum(response_times) / len(response_times) if response_times else None
        
        return {
            'available': is_available,
            'error': None,
            'response_time': round(avg_response_time * 1000, 2) if avg_response_time is not None else None
        }
    except Exception as e:
        return {
            'available': False,
            'error': str(e),
            'response_time': None
        }

def format_status_message(router: dict, status: dict, offline_since: datetime = None) -> str:
    """Форматирует сообщение о статусе роутера"""
    status_emoji = "✅" if status['available'] else "❌"
    msg = f"{status_emoji} {router['name']} ({router['ip']})\nОписание: {router['description']}"
    if status['available'] and offline_since is not None:
        delta = datetime.now() - offline_since
        total_seconds = delta.total_seconds()
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        offline_parts = []
        if days > 0:
            offline_parts.append(f"{int(days)} дн")
        if hours > 0 or days > 0:
            offline_parts.append(f"{int(hours)} ч")
        if minutes > 0 or hours > 0 or days > 0:
            offline_parts.append(f"{int(minutes)} мин")
        offline_parts.append(f"{int(seconds)} сек")
        
        offline_str = ", ".join(offline_parts)
        msg += f"\nРоутер был офлайн: {offline_str}"
    return msg

async def monitor_routers(context: ContextTypes.DEFAULT_TYPE):
    """Функция мониторинга роутеров"""
    routers = load_routers()
    if not routers:
        return

    for router in routers:
        current_status = check_router_status(router)
        router_key = router['ip']
        
        if router_key not in router_states:
            router_states[router_key] = { **current_status, 'offline_since': None }
            continue
            
        if router_states[router_key]['available'] != current_status['available']:
            if not current_status['available']:
                # Если роутер стал офлайн, запоминаем время и отправляем сообщение (без offline_since)
                router_states[router_key]['offline_since'] = datetime.now()
                message = format_status_message(router, current_status)
            else:
                # Если роутер стал онлайн, передаем время офлайн в сообщение
                offline_since = router_states[router_key].get('offline_since')
                router_states[router_key]['offline_since'] = None
                message = format_status_message(router, current_status, offline_since)
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
                logger.info(f"Отправлено уведомление об изменении статуса для {router['name']}")
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления: {str(e)}")
        
        router_states[router_key] = { **current_status, 'offline_since': router_states[router_key].get('offline_since') }

async def send_initial_status(application: Application):
    """Отправка начального статуса при запуске бота"""
    try:
        routers = load_routers()
        if not routers:
            return

        # Формируем одно сообщение для всех роутеров
        message = "🚀 Бот запущен. Статус роутеров:\n\n"
        
        for router in routers:
            status = check_router_status(router)
            router_states[router['ip']] = status
            message += format_status_message(router, status) + "\n\n"
        
        # Отправляем одно сообщение со всеми роутерами
        await application.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
        logger.info("Отправлен начальный статус всех роутеров")
    except Exception as e:
        logger.error(f"Ошибка при отправке начального статуса: {str(e)}")

def main():
    """Запуск бота"""
    try:
        logger.info("Инициализация бота...")
        
        # Создаем приложение
        application = (
            Application.builder()
            .token(TELEGRAM_BOT_TOKEN)
            .build()
        )

        # Запускаем мониторинг каждые 10 секунд
        application.job_queue.run_repeating(
            monitor_routers,
            interval=10,
            first=1,
            name='monitor'
        )

        # Отправляем начальный статус
        async def post_init(app):
            await send_initial_status(app)
        application.post_init = post_init

        # Запуск бота
        logger.info("Бот запускается...")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {str(e)}")
        raise

if __name__ == '__main__':
    main() 