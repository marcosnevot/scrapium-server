# Usa una base ligera
FROM python:3.11-slim

# 1) Instala Chromium, ChromeDriver y librerías necesarias
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    libnss3 \
    libgconf-2-4 \
    libxi6 \
    libxcursor1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxss1 \
    libxtst6 \
    libasound2 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libx11-xcb1 \
    libxfixes3 \
    libxkbcommon0 \
    libgbm1 \
 && rm -rf /var/lib/apt/lists/*

# 2) Variables de entorno para Selenium
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROME_PATH=/usr/bin/chromium

# 3) Trabaja en /app
WORKDIR /app

# 4) Copia y instala dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5) Copia el código
COPY scraper.py app.py .

# 6) Expone el puerto y arranca Uvicorn
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
