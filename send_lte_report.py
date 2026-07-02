#!/usr/bin/env python3
"""
Скрипт для отправки результатов проверки LTE-модемов в Telegram
"""

import json
import os
import requests
from datetime import datetime
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')

def send_telegram_message(message):
    """Отправка сообщения в Telegram"""
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        print("❌ Ошибка: не настроены TELEGRAM_BOT_TOKEN или ADMIN_CHAT_ID")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': ADMIN_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        
        response = requests.post(url, data=data, timeout=30)
        
        if response.status_code == 200:
            print("✅ Сообщение отправлено в Telegram")
            return True
        else:
            print(f"❌ Ошибка отправки: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка при отправке в Telegram: {e}")
        return False

def format_lte_report():
    """Форматирование отчета о проверке LTE-модемов"""
    try:
        # Читаем результат проверки
        result_file = 'logs/lte_daily_result.json'
        if not os.path.exists(result_file):
            return "❌ Файл с результатами проверки не найден"
        
        with open(result_file, 'r', encoding='utf-8') as f:
            result = json.load(f)
        
        # Форматируем сообщение
        timestamp = datetime.fromisoformat(result['timestamp']).strftime('%d.%m.%Y %H:%M')
        
        message = f"📱 <b>Ежедневная проверка LTE-модемов</b>\n"
        message += f"⏰ Время: {timestamp}\n"
        message += f"📊 Статистика: {result['total_available']}/{result['total_checked']} доступно\n\n"
        
        # Группируем роутеры по статусу
        available_routers = []
        unavailable_routers = []
        
        for router in result['routers']:
            if router['lte_status']['available']:
                available_routers.append(router)
            else:
                unavailable_routers.append(router)
        
        # Доступные роутеры
        if available_routers:
            message += "✅ <b>Доступные LTE-модемы:</b>\n"
            for router in available_routers:
                status = router['lte_status']
                if 'gateway' in status:
                    message += f"   • {router['name']} ({router['ip']}) - gateway: {status['gateway']}\n"
                else:
                    message += f"   • {router['name']} ({router['ip']}) - {status.get('status', 'OK')}\n"
            message += "\n"
        
        # Недоступные роутеры
        if unavailable_routers:
            message += "❌ <b>Недоступные LTE-модемы:</b>\n"
            for router in unavailable_routers:
                status = router['lte_status']
                error = status.get('error', status.get('status', 'неизвестная ошибка'))
                message += f"   • {router['name']} ({router['ip']}) - {error}\n"
            message += "\n"
        
        # Итоговая статистика
        message += f"📈 <b>Итого:</b>\n"
        message += f"   • Доступно: {result['total_available']}\n"
        message += f"   • Недоступно: {result['total_unavailable']}\n"
        message += f"   • Процент доступности: {(result['total_available']/result['total_checked']*100):.1f}%"
        
        return message
        
    except Exception as e:
        return f"❌ Ошибка при формировании отчета: {str(e)}"

def main():
    """Основная функция"""
    print("📤 Отправка отчета о проверке LTE-модемов в Telegram...")
    
    # Форматируем отчет
    message = format_lte_report()
    if not message:
        print("❌ Не удалось сформировать отчет")
        return
    
    print("📝 Сформированный отчет:")
    print("-" * 50)
    print(message)
    print("-" * 50)
    
    # Отправляем в Telegram
    if send_telegram_message(message):
        print("✅ Отчет успешно отправлен!")
    else:
        print("❌ Не удалось отправить отчет")

if __name__ == '__main__':
    main()
