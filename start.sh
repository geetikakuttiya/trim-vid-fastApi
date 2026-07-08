#!/bin/bash

# Install ffmpeg (required by your app)
sudo apt-get update && sudo apt-get install -y ffmpeg

# Start FastAPI using uvicorn
uv run streamlit run streamlit_app.py 