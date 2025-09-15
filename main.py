from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
import uuid
import os
import shutil
import subprocess
from pathlib import Path
import json

# Movie Download Dependencies
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
import re
# end

app = FastAPI()

# middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://moomoviev2.pages.dev/", "https://*.app.github.dev/", "http://localhost:3000"],  # <-- Allows all origins. Change this in production.
    allow_origin_regex=r"https://.*\.app\.github\.dev",
    allow_methods=["*"],  # <-- Allows all methods: GET, POST, etc.
    allow_headers=["*"],  # <-- Allows all headers
)

# Static and temp folders
STATIC_DIR = Path("static")
TEMP_DIR = Path("temp_videos")
STATIC_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
async def home():
    if TEMP_DIR.exists():
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
    TEMP_DIR.mkdir(exist_ok=True)
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/health")
async def health():
    return {"health": "ok"}

def trim_with_ffmpeg(input_path, output_path, start_time, duration):
    command = [
        "ffmpeg",
        "-ss", str(start_time),
        "-t", str(duration),
        "-i", str(input_path),
        "-c", "copy",
        "-avoid_negative_ts", "1",
        str(output_path)
    ]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

@app.post("/upload_video_stream/")
async def upload_video_stream(
    file: UploadFile = File(...),
    max_duration: int = Form(60)
):
    video_id = str(uuid.uuid4())
    original_path = TEMP_DIR / f"{video_id}_{file.filename}"
    with open(original_path, "wb") as f:
        f.write(await file.read())

    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            str(original_path)
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        duration = float(result.stdout.decode().strip())
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to get video duration. {str(e)}"})

    parts = []
    start = 0
    index = 0

    while start < duration:
        end = min(start + max_duration, duration)
        part_path = TEMP_DIR / f"{video_id}_part{index}.mp4"

        try:
            trim_with_ffmpeg(original_path, part_path, start, end - start)
            parts.append(f"/download/{part_path.name}")
        except subprocess.CalledProcessError:
            return JSONResponse(status_code=500, content={"error": "Trimming failed with ffmpeg."})

        start = end
        index += 1

    # Remove original file after splitting
    os.remove(original_path)

    return {"parts": parts}

@app.get("/download/{filename}")
def download_file(filename: str):
    file_path = TEMP_DIR / filename
    if not file_path.exists():
        return JSONResponse(status_code=404, content={"error": "File not found."})

    def cleanup():
        try:
            file_path.unlink()
        except Exception as e:
            print(f"Failed to delete {file_path}: {e}")

    return StreamingResponse(
        open(file_path, "rb"),
        media_type="video/mp4",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
        background=BackgroundTask(cleanup)
    )

@app.on_event("shutdown")
def cleanup_temp_dir():
    shutil.rmtree(TEMP_DIR, ignore_errors=True)


@app.get("/movie/{movie_id}/downloads")
def get_download_urls(movie_id: int):
    url = f"https://dl.vidsrc.vip/movie/{movie_id}"
    data = {"movieId": str(movie_id), "slider": "100"}
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.post(url, data=data, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        return {"error": str(e)}

    soup = BeautifulSoup(response.text, "html.parser")
    buttons = soup.find_all("button", onclick=True)
    url_pattern = re.compile(r"triggerDownload\(this,\s*'([^']+)'")
    
    download_urls = []
    
    for button in buttons:
        onclick = button.get("onclick", "")
        text = button.get_text(strip=True).split(" ")[0]
        # print(button/)
        match = url_pattern.search(onclick)
        if match:
            extracted_url = match.group(1)
            if not extracted_url.startswith("/sub"):
                download_urls.append({
                    "quality": text,
                    "link": extracted_url
                })

    return {"id": movie_id, "download_urls": download_urls}


@app.get("/tv/{tv_id}/downloads")
def get_download_urls_tv(tv_id: int, season: int = 1, episode: int = 1):
    url = f"https://dl.vidsrc.vip/tv/{tv_id}/{season}/{episode}"
    data = {"movieId": str(tv_id), "slider": "100"}
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0"
    }

    try:
        response = requests.post(url, data=data, headers=headers, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        return {"error": str(e)}

    soup = BeautifulSoup(response.text, "html.parser")
    buttons = soup.find_all("button", onclick=True)
    text = button.get_text(strip=True).split(" ")[0]
    url_pattern = re.compile(r"triggerDownload\(this,\s*'([^']+)'")
    
    download_urls = []
    
    for button in buttons:
        onclick = button.get("onclick", "")
        match = url_pattern.search(onclick)
        if match:
            extracted_url = match.group(1)
            if not extracted_url.startswith("/sub"):
                download_urls.append({
                    "quality": text,
                    "link": extracted_url
                })

    return {"id": tv_id, "download_urls": download_urls}
