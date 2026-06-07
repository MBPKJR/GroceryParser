FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if needed (e.g. for pandas/numpy)
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt



# Copy application code
COPY . .

# Create necessary directories
RUN mkdir -p downloads

# Port for the web server
EXPOSE 8080

CMD ["python", "main_roster.py"]
