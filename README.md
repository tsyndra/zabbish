# Telegram Bot для мониторинга интернета и роутеров

Этот бот позволяет мониторить доступность и время отклика роутеров в вашей сети через Telegram.

## Функциональность

- Проверка доступности роутеров
- Измерение времени отклика
- Автоматический мониторинг с уведомлениями
- Периодические проверки состояния
- Отправка уведомлений только при изменении статуса

## Установка

### 1. Подготовка файлов

1. Создайте файл `.env` со следующими параметрами:
```
TELEGRAM_BOT_TOKEN=your_bot_token_here
ADMIN_CHAT_ID=your_chat_id_here
PING_INTERVAL=300
```

2. Настройте `routers.json` с вашими роутерами:
```json
{
    "routers": [
        {
            "name": "Router Name",
            "ip": "192.168.1.1",
            "description": "Router Description"
        }
    ]
}
```

### 2. Установка через Docker

1. Установите Docker и Docker Compose:
```bash
# Для Ubuntu/Debian
sudo apt-get update
sudo apt-get install docker.io docker-compose
```

2. Запустите бота:
```bash
docker-compose up -d
```

### 3. Настройка автозапуска через systemd

1. Скопируйте файл сервиса:
```bash
sudo cp router-monitor-bot.service /etc/systemd/system/
```

2. Включите и запустите сервис:
```bash
sudo systemctl daemon-reload
sudo systemctl enable router-monitor-bot
sudo systemctl start router-monitor-bot
```

## Использование

В Telegram доступны следующие команды:
- `/start` - начать работу с ботом
- `/list` - показать список роутеров
- `/status` - проверить текущий статус
- `/monitor` - начать автоматический мониторинг
- `/stop` - остановить мониторинг

## Управление сервисом

```bash
# Проверка статуса
sudo systemctl status router-monitor-bot

# Перезапуск
sudo systemctl restart router-monitor-bot

# Остановка
sudo systemctl stop router-monitor-bot

# Просмотр логов
sudo journalctl -u router-monitor-bot -f
```

## Требования

- Docker и Docker Compose
- Доступ к интернету
- Права администратора для выполнения ping-запросов 