FROM selenium/standalone-chrome:latest

USER root

RUN pip install --no-cache-dir \
    python-telegram-bot>=20.0 \
    selenium>=4.0 \
    openpyxl

WORKDIR /app
COPY bot.py .

CMD ["python", "bot.py"]
