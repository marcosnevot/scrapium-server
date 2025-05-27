# Dockerfile
FROM python:3.11-slim

# 1) Instalar Chromium, Chromedriver y librerías necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    libnss3 libgconf-2-4 libxi6 libxcursor1 libxcomposite1 \
    libxdamage1 libxrandr2 libxss1 libxtst6 libasound2 libatk1.0-0 \
    libcups2 libdbus-1-3 libx11-xcb1 libxfixes3 libxkbcommon0 libgbm1 \
  && rm -rf /var/lib/apt/lists/*

# 2) Variables para Selenium
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# 3) Directorio de trabajo
WORKDIR /app

# 4) Copiar e instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5) Copiar código de la app
COPY scraper.py app.py ./    

# 6) Exponer puerto y arrancar Uvicorn
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
