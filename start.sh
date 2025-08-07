#!/bin/bash

# Install ffmpeg (required by your app)
apt-get update && apt-get install -y ffmpeg

# Start FastAPI using uvicorn
uv run uvicorn main:app --host 0.0.0.0 --port 10000