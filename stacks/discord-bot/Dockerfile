FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Create non-root user and group
RUN groupadd -r botuser && useradd -r -g botuser -u 1000 botuser

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and default assets
COPY bot/ ./bot/
COPY data/instructions/default.md /app/default_instructions.md

# Prepare data directory ownership
RUN mkdir -p /app/data/instructions/servers && chown -R botuser:botuser /app

USER botuser

CMD ["python", "-m", "bot.main"]
