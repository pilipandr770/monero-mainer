FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 5000
# Use Gunicorn with Gevent WebSocket worker to support websocket connections
CMD ["sh", "-c", "gunicorn -w 4 -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker 'app:app' --bind 0.0.0.0:${PORT:-5000}"]
