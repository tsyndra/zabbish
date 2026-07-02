#!/bin/bash

# Скрипт для настройки ежедневной проверки LTE-модемов через cron

echo "🔧 Настройка ежедневной проверки LTE-модемов через cron..."

# Получаем абсолютный путь к директории
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/test_lte_daily.py"

# Проверяем, что скрипт существует
if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo "❌ Ошибка: файл $PYTHON_SCRIPT не найден"
    exit 1
fi

# Делаем скрипт исполняемым
chmod +x "$PYTHON_SCRIPT"

# Создаем временный файл с cron задачей
TEMP_CRON=$(mktemp)

# Добавляем переменные окружения и задачу на 11:00 каждый день
cat > "$TEMP_CRON" << EOF
# Переменные окружения для cron
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
HOME=$HOME
TZ=Europe/Moscow

# Ежедневная проверка LTE-модемов в 11:00
0 11 * * * cd $SCRIPT_DIR && python3 $PYTHON_SCRIPT >> $SCRIPT_DIR/logs/lte_cron.log 2>&1 && python3 $SCRIPT_DIR/send_lte_report.py >> $SCRIPT_DIR/logs/lte_cron.log 2>&1
EOF

# Проверяем, есть ли уже такая задача в cron
if crontab -l 2>/dev/null | grep -q "test_lte_daily.py"; then
    echo "⚠️  Задача уже существует в cron. Удаляем старую..."
    crontab -l 2>/dev/null | grep -v "test_lte_daily.py" | crontab -
fi

# Добавляем новую задачу
crontab "$TEMP_CRON"

# Проверяем, что задача добавлена
if crontab -l 2>/dev/null | grep -q "test_lte_daily.py"; then
    echo "✅ Cron задача успешно добавлена!"
    echo ""
    echo "📅 Расписание проверок:"
    echo "   - 11:00 - ежедневная проверка"
    echo ""
    echo "📁 Логи будут сохраняться в: $SCRIPT_DIR/logs/lte_cron.log"
    echo "📊 Результаты будут сохраняться в: $SCRIPT_DIR/logs/lte_daily_result.json"
    echo ""
    echo "🔍 Текущие cron задачи:"
    crontab -l 2>/dev/null | grep "test_lte_daily.py"
else
    echo "❌ Ошибка при добавлении cron задачи"
    exit 1
fi

# Очищаем временный файл
rm "$TEMP_CRON"

echo ""
echo "🚀 Настройка завершена! Проверка LTE-модемов будет выполняться автоматически."
