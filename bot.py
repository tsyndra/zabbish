import os
import json
import logging
import subprocess
import pexpect
import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
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

# Минимальное время офлайн для отправки уведомления (в секундах)
# Можно настроить через переменную окружения MIN_OFFLINE_THRESHOLD
MIN_OFFLINE_THRESHOLD = int(os.getenv('MIN_OFFLINE_THRESHOLD', 30))

# Словарь для хранения последнего известного состояния роутеров
router_states = {}

# Словарь для отслеживания ежедневных проверок LTE-модемов
lte_daily_check = {}

# Создаем директорию для логов, если её нет
os.makedirs('logs', exist_ok=True)

# Функция для вычисления времени до следующего запуска в 11:00
def get_next_11am_time(tz):
    """Вычисляет время до следующего запуска в 11:00 по указанной таймзоне"""
    try:
        now = datetime.now(tz)
        today_11am = datetime.combine(now.date(), time(11, 0, tzinfo=tz))
        
        # Если 11:00 уже прошло сегодня, запускаем завтра
        if now >= today_11am:
            next_run = today_11am + timedelta(days=1)
        else:
            next_run = today_11am
        
        return next_run
    except Exception as e:
        logger.error(f"Ошибка при вычислении времени до 11:00: {e}")
        # Fallback: запускаем через 1 час
        return datetime.now(tz) + timedelta(hours=1)

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

def check_lte_modem_ssh(router):
    """Проверка LTE-модема через SSH для MikroTik роутеров"""
    try:
        # Проверяем, есть ли SSH доступ к роутеру
        if 'ssh_access' not in router:
            return {'available': False, 'error': 'SSH access not configured'}
        
        ssh_config = router['ssh_access']
        username = ssh_config.get('username', 'admin')
        # Используем key_path из конфигурации или путь по умолчанию для Docker
        key_path = ssh_config.get('key_path', '/app/router_ssh_key')
        
        # Упрощенная команда для быстрой проверки LTE интерфейса
        cmd = f"ssh -i {key_path} -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedKeyTypes=+ssh-rsa -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes {username}@{router['ip']} 'interface print where name=lte1'"
        
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        
        if result.returncode != 0:
            return {'available': False, 'error': f'SSH connection failed: {result.stderr}'}
        
        # Парсим результат
        output = result.stdout.strip()
        
        # Проверяем, есть ли LTE интерфейс и он активен
        lines = output.split('\n')
        lte_found = False
        lte_running = False
        
        for i, line in enumerate(lines):
            if 'lte1' in line:
                lte_found = True
                # Проверяем флаг R в текущей строке или в предыдущей строке
                if 'R' in line or (i > 0 and 'R' in lines[i-1]):
                    lte_running = True
                break
        
        if lte_found and lte_running:
            return {
                'available': True,
                'status': 'Connected',
                'interface': 'lte1'
            }
        elif lte_found:
            return {'available': False, 'status': 'Interface exists but not running'}
        else:
            return {'available': False, 'status': 'LTE interface not found'}
            
    except subprocess.TimeoutExpired:
        return {'available': False, 'error': 'SSH timeout'}
    except Exception as e:
        return {'available': False, 'error': str(e)}

def check_lte_modem_ping(router):
    """Проверка LTE-модема через SSH для Keenetic роутеров"""
    try:
        # Проверяем наличие lte_gateway в конфигурации
        if 'lte_gateway' not in router:
            return {'available': False, 'error': 'LTE gateway not configured'}
        
        lte_gateway = router['lte_gateway']
        
        # Сразу используем SSH для проверки интерфейса Keenetic роутера
        if 'ssh_access' in router:
            ssh_config = router['ssh_access']
            username = ssh_config.get('username', 'admin')
            password = ssh_config.get('password')
            key_path = ssh_config.get('key_path')
            
            # Используем SSH с паролем для Keenetic роутеров (40LET и VATUTINKI)
            if password and not key_path:
                try:
                    # Используем sshpass для автоматического ввода пароля
                    import subprocess
                    sshpass_cmd = f"sshpass -p '{password}' ssh -p 22 -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null {username}@{router['ip']} 'show interface CdcEthernet0'"
                    
                    result = subprocess.run(sshpass_cmd, shell=True, capture_output=True, text=True, timeout=10)
                    
                    if result.returncode == 0:
                        interface_output = result.stdout
                        
                        # Проверяем что интерфейс активен
                        if 'link: up' in interface_output and 'connected: yes' in interface_output:
                            # Получаем дополнительную информацию
                            signal_level = None
                            operator = None
                            mobile_type = None
                            
                            for line in interface_output.split('\n'):
                                if 'signal-level:' in line:
                                    signal_level = line.split(':')[1].strip()
                                elif 'operator:' in line:
                                    operator = line.split(':')[1].strip()
                                elif 'mobile:' in line:
                                    mobile_type = line.split(':')[1].strip()
                            
                            return {
                                'available': True,
                                'status': 'Connected',
                                'gateway': lte_gateway,
                                'signal_level': signal_level,
                                'operator': operator,
                                'mobile_type': mobile_type
                            }
                        else:
                            return {
                                'available': False,
                                'status': 'LTE interface down',
                                'gateway': lte_gateway
                            }
                    else:
                        return {
                            'available': False,
                            'status': f'SSH command failed: {result.stderr}',
                            'gateway': lte_gateway
                        }
                        
                except Exception as e:
                    logger.debug(f"sshpass failed: {e}")
                    
                    # Fallback: пробуем expect если sshpass не работает
                    try:
                        import pexpect
                        ssh_cmd = f"ssh -p 22 -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null {username}@{router['ip']}"
                        child = pexpect.spawn(ssh_cmd, timeout=8)
                        child.expect(['password:', 'Password:'], timeout=5)
                        child.sendline(password)
                        child.expect(['#', '$', '>', '%', 'config'], timeout=5)
                        
                        # Проверяем статус LTE интерфейса
                        child.sendline('show interface CdcEthernet0')
                        child.expect(['#', '$', '>', '%', 'config'], timeout=8)
                        interface_output = child.before.decode('utf-8')
                        
                        # Проверяем что интерфейс активен
                        if 'link: up' in interface_output and 'connected: yes' in interface_output:
                            child.close()
                            return {
                                'available': True,
                                'status': 'Connected via expect',
                                'gateway': lte_gateway
                            }
                        else:
                            child.close()
                            return {
                                'available': False,
                                'status': 'LTE interface down',
                                'gateway': lte_gateway
                            }
                            
                    except Exception as expect_error:
                        logger.debug(f"expect also failed: {expect_error}")
                        return {
                            'available': False,
                            'status': f'SSH error: {str(e)}',
                            'gateway': lte_gateway
                        }
            
            # Пробуем SSH с ключом
            elif key_path:
                # Используем key_path из конфигурации или путь по умолчанию для Docker
                ssh_key_path = ssh_config.get('key_path', '/app/router_ssh_key')
                cmd = f"ssh -i {ssh_key_path} -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes {username}@{router['ip']} 'ping {lte_gateway} -c 1 -W 2'"
                
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
                
                if result.returncode == 0 and 'ttl=' in result.stdout.lower():
                    return {
                        'available': True,
                        'status': 'Connected via router',
                        'gateway': lte_gateway
                    }
        
        return {'available': False, 'status': 'LTE gateway unreachable'}
            
    except Exception as e:
        return {'available': False, 'error': str(e)}

def check_router_status_enhanced(router):
    """Расширенная проверка роутера с поддержкой LTE-модемов"""
    # Базовая проверка ping
    basic_status = check_router_status(router)
    
    # Проверка LTE-модема
    lte_status = None
    
    # Определяем метод проверки LTE модема:
    # Для MikroTik роутеров (с username tsyndra) используем SSH команды
    # Для Keenetic роутеров (с username admin) используем ping
    if 'ssh_access' in router:
        ssh_config = router['ssh_access']
        username = ssh_config.get('username', 'admin')
        
        # Если username tsyndra - это MikroTik, используем SSH
        if username == 'tsyndra':
            lte_status = check_lte_modem_ssh(router)
        # Если username admin - это Keenetic, используем ping
        elif username == 'admin' and 'lte_gateway' in router:
            lte_status = check_lte_modem_ping(router)
        else:
            # Fallback для других случаев
            if 'lte_gateway' in router:
                lte_status = check_lte_modem_ping(router)
            else:
                lte_status = check_lte_modem_ssh(router)
    
    return {
        'router': basic_status,
        'lte_modem': lte_status,
        'overall_available': basic_status['available']
    }

def check_router_status_only(router):
    """Проверка только роутера без LTE-модема"""
    # Базовая проверка ping
    basic_status = check_router_status(router)
    
    return {
        'router': basic_status,
        'lte_modem': None,
        'overall_available': basic_status['available']
    }

def format_status_message(router: dict, status: dict, offline_since: datetime = None, offline_threshold: int = MIN_OFFLINE_THRESHOLD) -> str:
    """Форматирует сообщение о статусе роутера"""
    # Определяем основной статус
    if isinstance(status, dict) and 'overall_available' in status:
        # Новый формат с LTE
        router_status = status.get('router', {})
        lte_status = status.get('lte_modem')
        overall_available = status.get('overall_available', False)
        
        status_emoji = "✅" if overall_available else "❌"
        current_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        msg = f"{status_emoji} {router['name']} ({router['ip']})\nОписание: {router['description']}"
        
        # Информация о роутере
        if overall_available:
            msg += f"\n✅🖥️ Роутер: доступен"
        else:
            msg += f"\n❌🖥️ Роутер: недоступен"
            if router_status.get('error'):
                msg += f" (ошибка: {router_status['error']})"
        
        # Информация о LTE-модеме
        if lte_status:
            if lte_status.get('available'):
                msg += f"\n✅📱 LTE-модем: доступен"
            else:
                msg += f"\n❌📱 LTE-модем: недоступен"
                if lte_status.get('error'):
                    msg += f" (ошибка: {lte_status['error']})"
                elif lte_status.get('status'):
                    msg += f" ({lte_status['status']})"
        
        # Информация об офлайне
        if overall_available and offline_since is not None:
            delta = datetime.now() - offline_since
            total_seconds = delta.total_seconds()
            if total_seconds >= offline_threshold:
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
        elif not overall_available:
            msg += "\n🔄 Роутер недоступен"
        
        return msg
    else:
        # Старый формат (для обратной совместимости)
        status_emoji = "✅" if status['available'] else "❌"
        current_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        msg = f"{status_emoji} {router['name']} ({router['ip']})\nОписание: {router['description']}"
        
        if status['available'] and offline_since is not None:
            delta = datetime.now() - offline_since
            total_seconds = delta.total_seconds()
            if total_seconds >= offline_threshold:
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
        elif not status['available']:
            msg += "\n🔄 Роутер недоступен"
        return msg

def format_compact_status(router: dict, status: dict, offline_since: datetime = None) -> str:
    """Форматирует компактное сообщение о статусе роутера"""
    router_emoji = "✅" if status['overall_available'] else "❌"
    lte_emoji = ""
    
    # Добавляем информацию о LTE если есть
    if status.get('lte_modem'):
        lte_status = status['lte_modem']
        if lte_status.get('available'):
            lte_emoji = " 📱"
        else:
            lte_emoji = " 📱❌"
    
    msg = f"{router_emoji}{lte_emoji} {router['name']} ({router['ip']})"
    
    # Добавляем время офлайна в скобках при восстановлении
    if status['overall_available'] and offline_since is not None:
        delta = datetime.now() - offline_since
        total_seconds = delta.total_seconds()
        if total_seconds > 0:
            days, remainder = divmod(total_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            offline_parts = []
            if days > 0:
                offline_parts.append(f"{int(days)}д")
            if hours > 0 or days > 0:
                offline_parts.append(f"{int(hours)}ч")
            if minutes > 0 or hours > 0 or days > 0:
                offline_parts.append(f"{int(minutes)}м")
            offline_parts.append(f"{int(seconds)}с")
            offline_str = "".join(offline_parts)
            msg += f" ({offline_str})"
    
    return msg

async def monitor_routers(context: ContextTypes.DEFAULT_TYPE):
    """Функция мониторинга роутеров"""
    logger.info("Запуск мониторинга роутеров...")
    routers = load_routers()
    if not routers:
        logger.warning("Не найдено роутеров для мониторинга")
        return
    logger.info(f"Найдено {len(routers)} роутеров для проверки")

    for router in routers:
        logger.info(f"Проверяем роутер: {router['name']} ({router['ip']})")
        current_status = check_router_status_only(router)
        router_key = router['ip']
        logger.info(f"Статус {router['name']}: роутер {'доступен' if current_status['overall_available'] else 'недоступен'}")

        if router_key not in router_states:
            router_states[router_key] = { **current_status, 'offline_since': None, 'offline_notified': False }
            continue

        prev_state = router_states[router_key]
        prev_available = prev_state['overall_available']
        offline_since = prev_state.get('offline_since')
        offline_notified = prev_state.get('offline_notified', False)

        now = datetime.now()

        # Если роутер был онлайн и стал офлайн
        if prev_available and not current_status['overall_available']:
            # Если только что ушел в офлайн, запоминаем время
            if offline_since is None:
                router_states[router_key] = { **current_status, 'offline_since': now, 'offline_notified': False }
                continue
            # Если офлайн длится дольше порога и уведомление еще не отправлено
            offline_duration = (now - offline_since).total_seconds()
            if offline_duration >= MIN_OFFLINE_THRESHOLD and not offline_notified:
                router_states[router_key] = { **current_status, 'offline_since': offline_since, 'offline_notified': True }
                # Отправляем уведомление о том, что роутер стал недоступным
                offline_status = {
                    'router': current_status['router'],
                    'lte_modem': current_status.get('lte_modem'),
                    'overall_available': False
                }
                offline_msg = format_compact_status(router, offline_status)
                try:
                    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=offline_msg)
                    logger.info(f"Отправлено уведомление о недоступности для {router['name']}")
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления о недоступности: {str(e)}")
            else:
                # Просто обновляем состояние, уведомление не отправляем
                router_states[router_key] = { **current_status, 'offline_since': offline_since, 'offline_notified': offline_notified }
            continue

        # Если роутер был офлайн и стал онлайн
        if not prev_available and current_status['overall_available']:
            if offline_since is not None and offline_notified:
                offline_duration = (now - offline_since).total_seconds()
                # Отправляем сообщение о восстановлении
                restore_msg = format_compact_status(router, current_status, offline_since)
                try:
                    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=restore_msg)
                    logger.info(f"Отправлено уведомление о восстановлении для {router['name']} (офлайн {offline_duration:.1f}с)")
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления: {str(e)}")
            # Сброс состояния офлайна
            router_states[router_key] = { **current_status, 'offline_since': None, 'offline_notified': False }
            continue

        # Если роутер продолжает быть офлайн
        if not prev_available and not current_status['overall_available']:
            # Если офлайн длится дольше порога и уведомление еще не отправлено
            if offline_since is not None:
                offline_duration = (now - offline_since).total_seconds()
                if offline_duration >= MIN_OFFLINE_THRESHOLD and not offline_notified:
                    router_states[router_key] = { **current_status, 'offline_since': offline_since, 'offline_notified': True }
                    offline_status = {
                        'router': current_status['router'],
                        'lte_modem': current_status.get('lte_modem'),
                        'overall_available': False
                    }
                    offline_msg = format_compact_status(router, offline_status)
                    try:
                        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=offline_msg)
                        logger.info(f"Отправлено уведомление о недоступности для {router['name']}")
                    except Exception as e:
                        logger.error(f"Ошибка при отправке уведомления о недоступности: {str(e)}")
                else:
                    router_states[router_key] = { **current_status, 'offline_since': offline_since, 'offline_notified': offline_notified }
            else:
                router_states[router_key] = { **current_status, 'offline_since': offline_since, 'offline_notified': offline_notified }
            continue

        # Если роутер продолжает быть онлайн, просто обновляем состояние
        if prev_available and current_status['overall_available']:
            router_states[router_key] = { **current_status, 'offline_since': None, 'offline_notified': False }

async def send_initial_status(application: Application):
    """Отправка начального статуса при запуске бота"""
    try:
        routers = load_routers()
        if not routers:
            return

        # Формируем компактное сообщение для всех роутеров
        message = "🚀 Бот запущен. Статус роутеров:\n\n"
        for router in routers:
            status = check_router_status_only(router)
            router_states[router['ip']] = status
            
            # Используем компактный формат
            message += format_compact_status(router, status) + "\n"
        
        # Отправляем одно сообщение со всеми роутерами
        await application.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
        logger.info("Отправлен начальный статус всех роутеров")
    except Exception as e:
        logger.error(f"Ошибка при отправке начального статуса: {str(e)}")

async def check_lte_modems_daily(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневная проверка LTE-модемов в 11:00"""
    global lte_daily_check
    
    logger.info("🚀 Запуск ежедневной проверки LTE-модемов...")
    logger.info(f"⏰ Время запуска: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    
    try:
        routers = load_routers()
        if not routers:
            logger.warning("Не найдено роутеров для проверки LTE-модемов")
            return
        
        # Проверяем LTE-модемы на всех роутерах с LTE поддержкой (SSH или lte_gateway)
        lte_routers = [router for router in routers if 'ssh_access' in router or 'lte_gateway' in router]
        
        if not lte_routers:
            logger.info("Не найдено роутеров с SSH доступом для проверки LTE-модемов")
            return
        
        logger.info(f"Найдено {len(lte_routers)} роутеров для проверки LTE-модемов")
        
        # Проверяем, была ли уже проверка сегодня (с учетом таймзоны)
        try:
            tz = ZoneInfo('Europe/Moscow')  # Принудительно используем московское время
        except Exception:
            tz = None
        now_dt = datetime.now(tz) if tz else datetime.now()
        today = now_dt.date()
        
        # Проверяем, была ли уже проверка сегодня
        if today in lte_daily_check:
            logger.info("LTE-модемы уже проверялись сегодня")
            return
        
        # Формируем сообщение о проверке LTE-модемов
        message = "📱 Ежедневная проверка LTE-модемов:\n\n"
        
        # Счетчики для статистики
        total_checked = 0
        total_available = 0
        total_unavailable = 0
        
        for router in lte_routers:
            logger.info(f"Проверяем LTE-модем: {router['name']} ({router['ip']})")
            total_checked += 1
            
            try:
                # Проверяем LTE-модем используя подходящий метод
                if 'lte_gateway' in router and 'ssh_access' in router:
                    # Если есть и lte_gateway и ssh_access, определяем тип роутера по наличию пароля
                    ssh_config = router['ssh_access']
                    if ssh_config.get('password') and not ssh_config.get('key_path'):
                        # Keenetic роутер с паролем
                        lte_status = check_lte_modem_ping(router)
                    else:
                        # MikroTik роутер с ключом
                        lte_status = check_lte_modem_ssh(router)
                elif 'lte_gateway' in router:
                    # Только lte_gateway - используем ping
                    lte_status = check_lte_modem_ping(router)
                elif 'ssh_access' in router:
                    # Только ssh_access - используем SSH
                    lte_status = check_lte_modem_ssh(router)
                else:
                    lte_status = {'available': False, 'error': 'No LTE support configured'}
                
                if lte_status['available']:
                    total_available += 1
                    message += f"✅📱 {router['name']} ({router['ip']}): доступен\n"
                else:
                    total_unavailable += 1
                    error_msg = lte_status.get('error', lte_status.get('status', 'неизвестная ошибка'))
                    message += f"❌📱 {router['name']} ({router['ip']}): недоступен - {error_msg}\n"
                    
            except Exception as e:
                total_unavailable += 1
                logger.error(f"Ошибка при проверке LTE-модема {router['name']}: {e}")
                message += f"❌📱 {router['name']} ({router['ip']}): ошибка проверки - {str(e)}\n"
        
        # Добавляем итоговую статистику
        message += f"\n📊 Итого: {total_available}/{total_checked} доступно"
        
        # Отправляем сообщение с retry логикой
        max_retries = 3
        retry_delay = 5
        
        for attempt in range(max_retries):
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID, 
                    text=message,
                    read_timeout=60,  # Увеличиваем таймаут чтения
                    write_timeout=60,  # Увеличиваем таймаут записи
                    connect_timeout=30  # Увеличиваем таймаут подключения
                )
                logger.info("Отправлено сообщение о ежедневной проверке LTE-модемов")
                
                # Отмечаем, что проверка была выполнена сегодня
                lte_daily_check[today] = True
                
                # Очищаем старые записи (старше 7 дней)
                week_ago = today - timedelta(days=7)
                old_dates = [date for date in lte_daily_check.keys() if date < week_ago]
                for old_date in old_dates:
                    del lte_daily_check[old_date]
                
                # Следующая проверка автоматически запланирована планировщиком на завтра в 11:00
                logger.info("Следующая проверка LTE-модемов автоматически запланирована планировщиком")
                break  # Успешно отправили, выходим из цикла
                
            except Exception as e:
                logger.error(f"Ошибка при отправке сообщения о проверке LTE-модемов (попытка {attempt + 1}/{max_retries}): {str(e)}")
                
                if attempt < max_retries - 1:  # Если это не последняя попытка
                    logger.info(f"Повторная попытка через {retry_delay} секунд...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Увеличиваем задержку для следующей попытки
                else:
                    # Последняя попытка не удалась, пытаемся отправить уведомление об ошибке
                    try:
                        await asyncio.sleep(5)
                        error_msg = f"⚠️ Ошибка отправки отчета о проверке LTE-модемов: {str(e)}"
                        await context.bot.send_message(
                            chat_id=ADMIN_CHAT_ID, 
                            text=error_msg,
                            read_timeout=30,
                            write_timeout=30,
                            connect_timeout=15
                        )
                    except Exception as retry_error:
                        logger.error(f"Не удалось отправить уведомление об ошибке: {retry_error}")
            
    except Exception as e:
        logger.error(f"Критическая ошибка в ежедневной проверке LTE-модемов: {str(e)}")
        # Пытаемся отправить уведомление об ошибке
        try:
            error_message = f"❌ Критическая ошибка при ежедневной проверке LTE-модемов:\n{str(e)}"
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=error_message)
        except:
            pass

async def test_scheduler(context: ContextTypes.DEFAULT_TYPE):
    """Тестовая функция для проверки работы планировщика"""
    try:
        message = "🧪 ТЕСТ: Планировщик работает! Время: " + datetime.now().strftime("%H:%M:%S")
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
        logger.info("Отправлено тестовое сообщение от планировщика")
    except Exception as e:
        logger.error(f"Ошибка при отправке тестового сообщения: {str(e)}")

async def reschedule_lte_check(context: ContextTypes.DEFAULT_TYPE):
    """Перепланирование ежедневной проверки LTE-модемов"""
    try:
        # Определяем таймзону - принудительно используем московское время
        tz_name = 'Europe/Moscow'
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo('UTC')
        
        # Вычисляем время до следующего запуска в 11:00
        next_run = get_next_11am_time(tz)
        delay_seconds = int((next_run - datetime.now(tz)).total_seconds())
        
        # Удаляем старую задачу если она существует
        try:
            context.job_queue.get_jobs_by_name('lte_daily_check')[0].schedule_removal()
        except (IndexError, AttributeError):
            pass
        
        # Создаем новую задачу
        context.job_queue.run_daily(
            check_lte_modems_daily,
            time=time(11, 0),  # 11:00
            days=(0, 1, 2, 3, 4, 5, 6),  # Все дни недели
            name='lte_daily_check'
        )
        
        logger.info(f"Перепланирование: ежедневная проверка LTE-модемов установлена на 11:00 ежедневно")
        
        # Отправляем уведомление об успешном перепланировании
        try:
            message = f"🔄 Планировщик перезапущен\n📅 Проверка LTE-модемов: каждый день в 11:00"
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
            logger.info("Отправлено уведомление об успешном перепланировании")
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление о перепланировании: {e}")
        
    except Exception as e:
        logger.error(f"Ошибка при перепланировании: {e}")
        # Пытаемся отправить уведомление об ошибке
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Ошибка перепланирования: {str(e)}")
        except:
            pass

async def check_scheduler_health(context: ContextTypes.DEFAULT_TYPE):
    """Проверка здоровья планировщика"""
    try:
        # Проверяем, есть ли задача ежедневной проверки LTE-модемов
        lte_jobs = context.job_queue.get_jobs_by_name('lte_daily_check')
        
        if not lte_jobs:
            logger.warning("Задача 'lte_daily_check' не найдена, перезапускаем планировщик")
            await reschedule_lte_check(context)
            return
        
        # Проверяем, когда была последняя проверка
        try:
            tz = ZoneInfo('Europe/Moscow')  # Принудительно используем московское время
        except Exception:
            tz = ZoneInfo('UTC')
        
        now = datetime.now(tz)
        today = now.date()
        
        # Если сегодня еще не было проверки и уже после 11:00, проверяем
        if today not in lte_daily_check and now.hour >= 11:
            logger.warning("Сегодня еще не было проверки LTE-модемов, запускаем принудительно")
            await check_lte_modems_daily(context)
            return
        
        # Если сегодня еще не было проверки и уже после 12:00, отправляем уведомление
        if today not in lte_daily_check and now.hour >= 12:
            logger.error("Сегодня не была выполнена проверка LTE-модемов! Отправляем уведомление")
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID, 
                    text="⚠️ ВНИМАНИЕ: Сегодня не была выполнена ежедневная проверка LTE-модемов! Проверьте работу планировщика."
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление о пропущенной проверке: {e}")
        
        logger.info("Проверка здоровья планировщика: OK")
        
    except Exception as e:
        logger.error(f"Ошибка при проверке здоровья планировщика: {e}")
        # Пытаемся перезапустить планировщик
        try:
            await reschedule_lte_check(context)
        except Exception as reschedule_error:
            logger.error(f"Не удалось перезапустить планировщик: {reschedule_error}")

async def show_scheduler_status(context: ContextTypes.DEFAULT_TYPE):
    """Показывает статус планировщика"""
    try:
        # Определяем таймзону - принудительно используем московское время
        tz_name = 'Europe/Moscow'
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo('UTC')
        
        now = datetime.now(tz)
        today = now.date()
        
        # Получаем информацию о задачах планировщика
        lte_jobs = context.job_queue.get_jobs_by_name('lte_daily_check')
        health_jobs = context.job_queue.get_jobs_by_name('scheduler_health_check')
        
        message = "📅 Статус планировщика:\n\n"
        
        # Статус ежедневной проверки LTE-модемов
        if lte_jobs:
            next_run = lte_jobs[0].next_t
            if next_run:
                next_run_dt = datetime.fromtimestamp(next_run, tz=tz)
                message += f"✅ Ежедневная проверка LTE-модемов:\n"
                message += f"   📅 Следующий запуск: {next_run_dt.strftime('%d.%m.%Y %H:%M')}\n"
            else:
                message += f"⚠️ Ежедневная проверка LTE-модемов:\n"
                message += f"   📅 Время следующего запуска не определено\n"
        else:
            message += f"❌ Ежедневная проверка LTE-модемов:\n"
            message += f"   📅 Задача не найдена\n"
        
        # Статус проверки здоровья планировщика
        if health_jobs:
            message += f"✅ Мониторинг здоровья планировщика: активен\n"
        else:
            message += f"❌ Мониторинг здоровья планировщика: неактивен\n"
        
        # Информация о последних проверках
        message += f"\n📊 Последние проверки:\n"
        if today in lte_daily_check:
            message += f"✅ Сегодня ({today.strftime('%d.%m.%Y')}): проверка выполнена\n"
        else:
            message += f"❌ Сегодня ({today.strftime('%d.%m.%Y')}): проверка не выполнена\n"
        
        # Показываем последние 3 дня
        for i in range(1, 4):
            check_date = today - timedelta(days=i)
            if check_date in lte_daily_check:
                message += f"✅ {check_date.strftime('%d.%m.%Y')}: проверка выполнена\n"
            else:
                message += f"❌ {check_date.strftime('%d.%m.%Y')}: проверка не выполнена\n"
        
        # Текущее время
        message += f"\n🕐 Текущее время: {now.strftime('%d.%m.%Y %H:%M:%S')} ({tz_name})"
        
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message)
        
    except Exception as e:
        logger.error(f"Ошибка при отображении статуса планировщика: {e}")
        try:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Ошибка при получении статуса планировщика: {str(e)}")
        except:
            pass

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

        # Добавляем обработчики команд
        application.add_handler(CommandHandler("status", lambda update, context: asyncio.create_task(send_initial_status(application))))
        application.add_handler(CommandHandler("reschedule", lambda update, context: asyncio.create_task(reschedule_lte_check(context))))
        application.add_handler(CommandHandler("test", lambda update, context: asyncio.create_task(test_scheduler(context))))
        application.add_handler(CommandHandler("scheduler", lambda update, context: asyncio.create_task(show_scheduler_status(context))))
        application.add_handler(CommandHandler("lte_check", lambda update, context: asyncio.create_task(check_lte_modems_daily(context))))
        application.add_handler(CommandHandler("force_lte", lambda update, context: asyncio.create_task(check_lte_modems_daily(context))))

        # Определяем таймзону - принудительно используем московское время
        tz_name = 'Europe/Moscow'
        try:
            tz = ZoneInfo(tz_name)
            logger.info(f"Используется таймзона: {tz_name}")
        except Exception as e:
            logger.warning(f"Не удалось применить таймзону '{tz_name}': {e}. Используем UTC")
            tz = ZoneInfo('UTC')

        # Запускаем мониторинг каждые 10 секунд
        application.job_queue.run_repeating(
            monitor_routers,
            interval=10,
            first=1,
            name='monitor'
        )

        # Запускаем ежедневную проверку LTE-модемов в 11:00
        try:
            # Запускаем задачу каждый день в 11:00 по московскому времени
            application.job_queue.run_daily(
                check_lte_modems_daily,
                time=time(11, 0),  # 11:00
                days=(0, 1, 2, 3, 4, 5, 6),  # Все дни недели
                name='lte_daily_check'
            )
            logger.info(f"Планировщик: добавлена ежедневная задача 'lte_daily_check' - запуск каждый день в 11:00 ({tz_name})")
            
            # Добавляем задачу мониторинга планировщика каждые 6 часов
            application.job_queue.run_repeating(
                lambda ctx: asyncio.create_task(check_scheduler_health(ctx)),
                interval=6 * 60 * 60,  # 6 часов в секундах
                first=60,  # Первый запуск через 1 минуту
                name='scheduler_health_check'
            )
            logger.info("Планировщик: добавлена задача мониторинга здоровья планировщика каждые 6 часов")
            
        except Exception as e:
            logger.error(f"Не удалось добавить ежедневную задачу 'lte_daily_check': {e}")
            # Пытаемся отправить уведомление об ошибке
            try:
                # Создаем временное приложение для отправки уведомления
                temp_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
                asyncio.create_task(temp_app.bot.send_message(
                    chat_id=ADMIN_CHAT_ID, 
                    text=f"❌ Ошибка запуска планировщика LTE-модемов: {str(e)}"
                ))
            except:
                pass

        # Отправляем начальный статус
        async def post_init(app):
            await send_initial_status(app)
        application.post_init = post_init

        # Запуск бота
        logger.info("Бот запускается...")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            pool_timeout=60,  # Увеличиваем таймауты
            connect_timeout=60,
            read_timeout=60,
            write_timeout=60
        )
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {str(e)}")
        raise

if __name__ == '__main__':
    main() 