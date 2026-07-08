# trim-vid-fastApi

## YouTube Reel Splitter (Streamlit)

A Streamlit UI that:
1. Downloads a video (or just its audio) from a YouTube URL via `yt-dlp`.
2. Splits the video into fixed-length parts (you choose the seconds).
3. Converts each part into a labelled 9:16 reel:
   - **16:9 landscape clips** → a blurred 1080x1920 background is built with
     Pillow from a frame of the clip, and the original clip is overlaid
     centered on top of it (letterboxed into 9:16).
   - **9:16 portrait clips** → left as-is, just gets the label stamped on.
   - Every part is labelled `Part 1`, `Part 2`, ... so a multi-part series
     stays easy to follow.
4. Each generated reel gets its own preview + download button in the UI.

### Requirements

- `ffmpeg` / `ffprobe` must be installed and on `PATH`.
- Python deps: `pip install -r requirements.txt` (or `uv sync`, they're also
  listed in `pyproject.toml`).

### Run it

```bash
streamlit run streamlit_app.py
```

Then open the local URL Streamlit prints, paste a YouTube URL, pick a split
length, and hit **Download & Convert to Reels**. Use the **Audio only** tab
to grab just an mp3 instead.
