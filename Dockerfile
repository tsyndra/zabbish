FROM python:3.9-slim

WORKDIR /app

# Установка необходимых пакетов
RUN apt-get update && apt-get install -y \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов проекта
COPY requirements.txt .
COPY bot.py .
COPY routers.json .

# Установка зависимостей Python
RUN pip install --no-cache-dir -r requirements.txt

# Создание пользователя без прав root и настройка прав доступа
RUN useradd -m botuser && \
    mkdir -p /app/logs && \
    chown -R botuser:botuser /app

USER botuser

# Запуск бота
CMD ["python", "bot.py"] 