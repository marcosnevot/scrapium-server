FROM python:3.11-slim

# Instalar Chromium, Chromedriver y deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium chromium-driver \
    libnss3 libgconf-2-4 libxi6 libxcursor1 libxcomposite1 \
    libxdamage1 libxrandr2 libxss1 libxtst6 libasound2 libatk1.0-0 \
    libcups2 libdbus-1-3 libx11-xcb1 libxfixes3 libxkbcommon0 libgbm1 \
  && rm -rf /var/lib/apt/lists/*

# Variables para Selenium
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scraper.py app.py .

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
