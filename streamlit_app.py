"""
Streamlit UI — Reel Splitter

  1. Get a source video by either:
       a) Pasting a YouTube (or any yt-dlp supported) URL, or
       b) Uploading a video file yourself (no download needed).
  2. Split it into N-second parts, auto-converted to labelled 9:16 reels
     ("Part 1", "Part 2", ...), each with its own preview + download button.
  3. Optionally download just the audio track (mp3) from a YouTube URL.

Run with:  streamlit run streamlit_app.py
"""

from __future__ import annotations

import shutil
import tempfile
import time
import traceback
from pathlib import Path

import streamlit as st

import video_processor as vp

st.set_page_config(page_title="Reel Splitter", page_icon="🎬", layout="centered")

WORK_ROOT = Path(tempfile.gettempdir()) / "reel_splitter_sessions"
WORK_ROOT.mkdir(exist_ok=True)

# How long an old session's temp files are allowed to sit around before an
# app startup / rerun sweeps them away.
STALE_AFTER_SECONDS = 2 * 60 * 60  # 2 hours


def purge_stale_sessions(keep_dir: Path | None = None) -> None:
    """Delete session folders that haven't been touched in a while.

    Runs on every script execution (cheap: just a glob + stat), so leftover
    temp files from crashed/abandoned sessions don't pile up forever on a
    long-running server. The active session's folder is always kept.
    """
    now = time.time()
    for folder in WORK_ROOT.glob("session_*"):
        if keep_dir is not None and folder == keep_dir:
            continue
        try:
            newest_mtime = max((p.stat().st_mtime for p in folder.rglob("*")), default=folder.stat().st_mtime)
        except FileNotFoundError:
            continue
        if now - newest_mtime > STALE_AFTER_SECONDS:
            shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "session_dir" not in st.session_state:
    session_dir = WORK_ROOT / f"session_{int(time.time() * 1000)}"
    session_dir.mkdir(parents=True, exist_ok=True)
    st.session_state.session_dir = session_dir

purge_stale_sessions(keep_dir=st.session_state.session_dir)

if "reels" not in st.session_state:
    st.session_state.reels = []  # list[Path]

if "audio_file" not in st.session_state:
    st.session_state.audio_file = None  # Path | None


def get_session_dir() -> Path:
    return st.session_state.session_dir


def reset_outputs():
    st.session_state.reels = []
    session_dir = get_session_dir() / "parts"
    shutil.rmtree(session_dir, ignore_errors=True)


def clear_my_files():
    """Wipe everything this browser session has downloaded/generated so far."""
    session_dir = get_session_dir()
    shutil.rmtree(session_dir, ignore_errors=True)
    session_dir.mkdir(parents=True, exist_ok=True)
    st.session_state.reels = []
    st.session_state.audio_file = None


def save_cookie_upload(uploaded_file, dest_dir: Path) -> Path | None:
    if uploaded_file is None:
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = dest_dir / "cookies.txt"
    cookie_path.write_bytes(uploaded_file.getvalue())
    return cookie_path


def process_source_video(source_path: Path, segment_seconds: int, text_position: str, status, progress_bar):
    def report(msg: str):
        status.write(msg)

    info = vp.get_video_info(source_path)
    status.write(
        f"Source: {info.width}x{info.height}, {info.duration:.1f}s "
        f"({'portrait 9:16' if info.is_portrait else 'landscape 16:9'})"
    )

    parts_dir = get_session_dir() / "parts"
    status.write("Splitting into segments...")
    raw_parts = vp.split_video(source_path, int(segment_seconds), parts_dir, progress_cb=report)

    # the full source video isn't needed anymore once it's split into parts —
    # drop it to save disk space on the host
    source_path.unlink(missing_ok=True)

    reels = []
    for i, part in enumerate(raw_parts, start=1):
        reel_path = vp.convert_to_reel(part, i, parts_dir, text_position=text_position, progress_cb=report)
        reels.append(reel_path)
        progress_bar.progress(i / len(raw_parts))

    return reels


def render_reels():
    if st.session_state.reels:
        st.subheader("Your reels")
        for i, reel_path in enumerate(st.session_state.reels, start=1):
            if not reel_path.exists():
                continue
            with st.container(border=True):
                st.markdown(f"**Part {i}**")
                st.video(str(reel_path))
                with open(reel_path, "rb") as f:
                    data = f.read()
                st.download_button(
                    label=f"⬇️ Download Part {i}",
                    data=data,
                    file_name=f"part_{i}.mp4",
                    mime="video/mp4",
                    key=f"dl_reel_{i}",  # stable across reruns: index alone is
                    # unique within a session's reel list, no timestamp/random
                    use_container_width=True,
                )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title("🎬 Reel Splitter")
st.caption(
    "Split a long video into fixed-length parts and auto-convert each part into "
    "a labelled 9:16 reel, ready for Shorts/Reels/TikTok."
)

with st.sidebar:
    st.subheader("Session")
    st.caption(
        "Your downloads and generated reels live in a temp folder on the server. "
        "Files older than 2 hours are swept automatically; use this to clear "
        "yours right now."
    )
    if st.button("🗑️ Clear my files", use_container_width=True):
        clear_my_files()
        st.success("Cleared.")
        st.rerun()

tab_youtube, tab_upload, tab_audio = st.tabs(
    ["🔗 From YouTube URL", "📁 Upload a video", "🎵 Audio only (YouTube)"]
)

# --- From YouTube URL -------------------------------------------------------
with tab_youtube:
    url = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=...", key="yt_url")

    col1, col2 = st.columns(2)
    with col1:
        segment_seconds_yt = st.number_input(
            "Split length (seconds)", min_value=5, max_value=600, value=60, step=5, key="seg_yt"
        )
    with col2:
        text_position_yt = st.selectbox("Part label position", ["top", "bottom"], index=0, key="pos_yt")

    with st.expander("Advanced: cookies (fixes 'Sign in to confirm you're not a bot' / format errors)"):
        st.markdown(
            "YouTube sometimes blocks downloads unless you prove you're logged in. "
            "If the download fails, export your YouTube cookies with a browser "
            "extension like **Get cookies.txt LOCALLY**, then upload the file here."
        )
        cookie_upload_yt = st.file_uploader("cookies.txt (Netscape format)", type=["txt"], key="cookie_yt")

    if st.button("Download & Convert to Reels", type="primary", use_container_width=True, key="btn_yt"):
        if not url.strip():
            st.warning("Please paste a YouTube URL first.")
        else:
            reset_outputs()
            session_dir = get_session_dir()
            raw_dir = session_dir / "raw"
            status = st.status("Starting...", expanded=True)
            progress_bar = st.progress(0.0)

            try:
                cookie_path = save_cookie_upload(cookie_upload_yt, session_dir / "cookies")
                status.write("Fetching video info & downloading...")
                source_path = vp.download_video(
                    url.strip(), raw_dir, cookiefile=cookie_path, progress_cb=lambda m: status.write(m)
                )
                reels = process_source_video(source_path, segment_seconds_yt, text_position_yt, status, progress_bar)
                st.session_state.reels = reels
                status.update(label=f"Done! Generated {len(reels)} reel(s).", state="complete")
            except Exception as e:
                status.update(label="Something went wrong.", state="error")
                st.error(f"{e}")
                with st.expander("Details"):
                    st.code(traceback.format_exc())

# --- Upload your own video --------------------------------------------------
with tab_upload:
    st.write("Already have the video file? Upload it directly — no download needed.")
    uploaded_video = st.file_uploader(
        "Video file", type=["mp4", "mov", "mkv", "webm", "avi", "m4v"], key="video_upload"
    )

    col1, col2 = st.columns(2)
    with col1:
        segment_seconds_up = st.number_input(
            "Split length (seconds)", min_value=5, max_value=600, value=60, step=5, key="seg_up"
        )
    with col2:
        text_position_up = st.selectbox("Part label position", ["top", "bottom"], index=0, key="pos_up")

    if st.button("Convert to Reels", type="primary", use_container_width=True, key="btn_up"):
        if uploaded_video is None:
            st.warning("Please upload a video file first.")
        else:
            reset_outputs()
            session_dir = get_session_dir()
            raw_dir = session_dir / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            status = st.status("Starting...", expanded=True)
            progress_bar = st.progress(0.0)

            try:
                source_path = raw_dir / uploaded_video.name
                source_path.write_bytes(uploaded_video.getvalue())
                status.write(f"Saved upload: {uploaded_video.name}")
                reels = process_source_video(source_path, segment_seconds_up, text_position_up, status, progress_bar)
                st.session_state.reels = reels
                status.update(label=f"Done! Generated {len(reels)} reel(s).", state="complete")
            except Exception as e:
                status.update(label="Something went wrong.", state="error")
                st.error(f"{e}")
                with st.expander("Details"):
                    st.code(traceback.format_exc())

# --- Audio only -----------------------------------------------------------
with tab_audio:
    st.write("Download only the audio track (mp3) from a YouTube URL.")
    audio_url = st.text_input("YouTube URL", placeholder="https://www.youtube.com/watch?v=...", key="audio_url")

    with st.expander("Advanced: cookies (fixes 'Sign in to confirm you're not a bot' / format errors)"):
        cookie_upload_audio = st.file_uploader("cookies.txt (Netscape format)", type=["txt"], key="cookie_audio")

    if st.button("Download Audio (mp3)", use_container_width=True, key="btn_audio"):
        if not audio_url.strip():
            st.warning("Please paste a YouTube URL first.")
        else:
            session_dir = get_session_dir()
            audio_dir = session_dir / "audio"
            status = st.status("Downloading audio...", expanded=True)

            try:
                cookie_path = save_cookie_upload(cookie_upload_audio, session_dir / "cookies")
                audio_path = vp.download_audio(
                    audio_url.strip(), audio_dir, cookiefile=cookie_path, progress_cb=lambda m: status.write(m)
                )
                st.session_state.audio_file = audio_path
                status.update(label="Audio ready!", state="complete")
            except Exception as e:
                status.update(label="Something went wrong.", state="error")
                st.error(f"{e}")
                with st.expander("Details"):
                    st.code(traceback.format_exc())

    if st.session_state.audio_file and Path(st.session_state.audio_file).exists():
        audio_path = Path(st.session_state.audio_file)
        st.audio(str(audio_path))
        with open(audio_path, "rb") as f:
            audio_data = f.read()
        st.download_button(
            label="⬇️ Download MP3",
            data=audio_data,
            file_name=audio_path.name.split("_", 1)[-1],
            mime="audio/mpeg",
            key="dl_audio",
            use_container_width=True,
        )

# Reels are rendered ONCE here (not inside each tab) — st.tabs renders every
# tab's content into the DOM simultaneously, so calling this inside more than
# one tab created duplicate widget keys.
render_reels()

st.divider()
with st.expander("How the 9:16 conversion works"):
    st.markdown(
        "- **Landscape (16:9) clips**: a mid-frame is grabbed, cropped and blurred "
        "with Pillow into a 1080x1920 background canvas, then the original clip is "
        "overlaid centered on top of it — a classic blurred-letterbox reel look.\n"
        "- **Portrait (9:16) clips**: already the right shape, so the part label is "
        "simply stamped on top or bottom.\n"
        "- Every part gets a **\"Part N\"** label so viewers can follow a multi-part "
        "series in order."
    )

with st.expander("YouTube download failing? Read this"):
    st.markdown(
        "YouTube periodically tightens bot-detection, which can make yt-dlp ask for "
        "cookies or report *'Requested format is not available'* (often alongside an "
        "`Unknown codec iamf...` warning for a newer audio codec).\n\n"
        "This app already retries several internal strategies automatically "
        "(different player clients, looser format selection). If it still fails:\n"
        "1. **Update yt-dlp**: `pip install -U yt-dlp` — YouTube changes break old "
        "versions constantly, and fixes usually land within days.\n"
        "2. **Add cookies**: export `cookies.txt` from your browser while logged into "
        "YouTube (e.g. the *Get cookies.txt LOCALLY* extension) and upload it in the "
        "*Advanced* section above.\n"
        "3. **Or just upload the file yourself** in the *Upload a video* tab — that "
        "path never touches YouTube at all."
    )
