#!/bin/bash
# Скрипт для установки московской таймзоны

echo "Установка московской таймзоны..."

# Устанавливаем переменную окружения TZ
export TZ=Europe/Moscow

# Проверяем установку
echo "Текущая таймзона: $TZ"
echo "Текущее время: $(date)"

# Добавляем в .bashrc для постоянной установки
if ! grep -q "export TZ=Europe/Moscow" ~/.bashrc; then
    echo "export TZ=Europe/Moscow" >> ~/.bashrc
    echo "Добавлено в ~/.bashrc для постоянной установки"
fi

echo "Таймзона установлена успешно!"
