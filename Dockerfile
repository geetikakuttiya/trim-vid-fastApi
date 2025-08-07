FROM python:3.11-slim

# Install ffmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy files
COPY . .

# Install Python dependencies
RUN uv sync

# Expose the port used by Uvicorn
EXPOSE 10000

# Run the startup script
CMD ["./start.sh"]
