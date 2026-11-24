FROM python:3.12-slim
WORKDIR /app
COPY worker.py server.py ./
ENV PYTHONUNBUFFERED=1 PAPER_DATA_DIR=/data
CMD ["python", "server.py"]
