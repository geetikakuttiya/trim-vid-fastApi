#!/bin/bash

# Install ffmpeg (required by your app)
sudo apt-get update && sudo apt-get install -y ffmpeg

# Start FastAPI using uvicorn
uv run uvicorn main:app --host 0.0.0.0 --port 10000