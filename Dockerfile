FROM python:3.14.2

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot_main.py .

CMD ["python", "bot_main.py"]
