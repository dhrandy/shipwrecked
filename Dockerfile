FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py engine.py ./
COPY static ./static
RUN useradd -r -u 1000 shipwrecked && mkdir -p /data && chown shipwrecked /data
USER shipwrecked
EXPOSE 8647
CMD ["gunicorn", "-b", "0.0.0.0:8647", "--workers", "1", "--threads", "4", "app:app"]
