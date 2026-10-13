FROM python:3.12.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --requirement requirements.lock && useradd --system --uid 10001 --create-home fieldcall
COPY app.py .
COPY static ./static
RUN mkdir -p /data && chown fieldcall:fieldcall /data
USER 10001:10001
ENV FIELDCALL_HOST=0.0.0.0 FIELDCALL_PORT=8080 DATABASE_PATH=/data/fieldcall.sqlite3
EXPOSE 8080
CMD ["python", "app.py"]
