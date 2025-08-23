FROM python:3.9-slim

WORKDIR /app

# Установка необходимых пакетов
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    iputils-ping \
    sshpass \
    openssh-client \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов проекта
COPY requirements.txt .
COPY bot.py .
COPY routers.json .
COPY router_ssh_key.pub .
COPY router_ssh_key .

# Установка зависимостей Python
RUN pip install --no-cache-dir -r requirements.txt

# Создание пользователя без прав root и настройка прав доступа
RUN useradd -m botuser && \
    mkdir -p /app/logs && \
    chown -R botuser:botuser /app && \
    chmod 600 /app/router_ssh_key && \
    chmod 644 /app/router_ssh_key.pub

USER botuser

# Запуск бота
CMD ["python", "bot.py"] 