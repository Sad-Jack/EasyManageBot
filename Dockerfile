FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY . .
RUN pip install --no-cache-dir .

ENV NODE_ENV=production

CMD ["posting-assistant-bot"]
