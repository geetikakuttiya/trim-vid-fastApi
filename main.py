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

app = FastAPI()

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
