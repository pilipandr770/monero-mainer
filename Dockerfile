FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 5000
# Use Gunicorn with standard Gevent worker (simple-websocket handles WS without gevent-websocket bugs)
CMD ["sh", "-c", "gunicorn -w 4 -k gevent 'app:app' --bind 0.0.0.0:${PORT:-5000}"]
