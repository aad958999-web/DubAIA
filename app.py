import os
import uuid
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import requests
from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
    HTTPException,
    BackgroundTasks
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


# =========================================================
# CONFIG
# =========================================================

BASE = Path(__file__).parent

UPLOADS = BASE / "uploads"
WORK = BASE / "work"
OUTPUTS = BASE / "outputs"

for folder in [UPLOADS, WORK, OUTPUTS]:
    folder.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 500 * 1024 * 1024

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

app = FastAPI(
    title="DubAI",
    version="2.0"
)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE / "static")),
    name="static"
)

jobs = {}


# =========================================================
# SUPPORTED LANGUAGES
# =========================================================

LANGUAGES = {
    "ar": {
        "name": "العربية",
        "voice": "alloy"
    },
    "en": {
        "name": "English",
        "voice": "alloy"
    },
    "fr": {
        "name": "Français",
        "voice": "alloy"
    },
    "es": {
        "name": "Español",
        "voice": "alloy"
    },
    "de": {
        "name": "Deutsch",
        "voice": "alloy"
    },
    "it": {
        "name": "Italiano",
        "voice": "alloy"
    },
    "pt": {
        "name": "Português",
        "voice": "alloy"
    },
    "ru": {
        "name": "Русский",
        "voice": "alloy"
    },
    "ja": {
        "name": "日本語",
        "voice": "alloy"
    },
    "ko": {
        "name": "한국어",
        "voice": "alloy"
    },
    "zh": {
        "name": "中文",
        "voice": "alloy"
    }
}


# =========================================================
# HELPERS
# =========================================================

def run_command(command):
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr[-4000:]
        )

    return result.stdout


def get_duration(video):
    result = run_command([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video)
    ])

    try:
        return float(result.strip())
    except:
        return 0


def extract_audio(video, output):
    run_command([
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output)
    ])


# =========================================================
# OPENAI TRANSCRIPTION
# =========================================================

def transcribe_audio(audio_file, language):
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY غير موجود في Railway Variables."
        )

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}"
    }

    data = {
        "model": "whisper-1",
        "response_format": "verbose_json"
    }

    if language and language != "auto":
        data["language"] = language

    with open(audio_file, "rb") as f:

        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers=headers,
            files={
                "file": (
                    "audio.wav",
                    f,
                    "audio/wav"
                )
            },
            data=data,
            timeout=600
        )

    if response.status_code != 200:
        raise RuntimeError(
            f"فشل التعرف على الكلام: {response.text}"
        )

    return response.json()


# =========================================================
# TRANSLATION
# =========================================================

def translate_text(text, source, target):

    if not text.strip():
        return ""

    if source == target:
        return text

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY غير موجود."
        )

    prompt = f"""
Translate the following spoken dialogue into {LANGUAGES[target]['name']}.

Rules:
- Keep the meaning accurate.
- Make it natural for spoken dialogue.
- Do not add explanations.
- Return only the translated dialogue.

Text:
{text}
"""

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "gpt-4o-mini",
        "input": prompt,
        "temperature": 0.2
    }

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers=headers,
        json=payload,
        timeout=120
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"فشل الترجمة: {response.text}"
        )

    data = response.json()

    text_result = data.get("output_text")

    if not text_result:
        raise RuntimeError(
            "لم يتم الحصول على نتيجة الترجمة."
        )

    return text_result.strip()


# =========================================================
# TEXT TO SPEECH
# =========================================================

def generate_speech(text, target, output_file):

    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY غير موجود."
        )

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "gpt-4o-mini-tts",
        "voice": LANGUAGES[target]["voice"],
        "input": text,
        "response_format": "mp3"
    }

    response = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers=headers,
        json=payload,
        timeout=180
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"فشل إنشاء الصوت: {response.text}"
        )

    with open(output_file, "wb") as f:
        f.write(response.content)


# =========================================================
# SRT
# =========================================================

def srt_time(seconds):

    milliseconds = int(seconds * 1000)

    hours = milliseconds // 3600000
    milliseconds %= 3600000

    minutes = milliseconds // 60000
    milliseconds %= 60000

    secs = milliseconds // 1000
    milliseconds %= 1000

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{secs:02d},"
        f"{milliseconds:03d}"
    )


def create_srt(segments, output_file):

    with open(
        output_file,
        "w",
        encoding="utf-8"
    ) as f:

        for i, segment in enumerate(
            segments,
            start=1
        ):

            f.write(f"{i}\n")

            f.write(
                f"{srt_time(segment['start'])} --> "
                f"{srt_time(segment['end'])}\n"
            )

            f.write(
                segment["text"].strip()
            )

            f.write("\n\n")


# =========================================================
# PROCESS VIDEO
# =========================================================

def process_video(
    job_id,
    video,
    source_language,
    target_language,
    subtitles
):

    job = jobs[job_id]

    try:

        # -------------------------------------------------
        # STEP 1
        # -------------------------------------------------

        job["progress"] = 5
        job["message"] = "استخراج الصوت..."

        work_folder = WORK / job_id
        work_folder.mkdir(
            parents=True,
            exist_ok=True
        )

        audio_file = work_folder / "audio.wav"

        extract_audio(
            video,
            audio_file
        )

        # -------------------------------------------------
        # STEP 2
        # -------------------------------------------------

        job["progress"] = 20
        job["message"] = "التعرف على الكلام بالذكاء الاصطناعي..."

        transcription = transcribe_audio(
            audio_file,
            source_language
        )

        original_segments = []

        for segment in transcription.get(
            "segments",
            []
        ):

            text = segment.get(
                "text",
                ""
            ).strip()

            if not text:
                continue

            original_segments.append({
                "start": float(
                    segment["start"]
                ),
                "end": float(
                    segment["end"]
                ),
                "text": text
            })

        if not original_segments:

            raise RuntimeError(
                "لم يتم العثور على كلام في الفيديو."
            )

        # -------------------------------------------------
        # STEP 3
        # -------------------------------------------------

        job["progress"] = 35
        job["message"] = "ترجمة الكلام..."

        translated_segments = []

        total = len(original_segments)

        for i, segment in enumerate(
            original_segments
        ):

            translated = translate_text(
                segment["text"],
                source_language,
                target_language
            )

            translated_segments.append({
                "start": segment["start"],
                "end": segment["end"],
                "text": translated
            })

            progress = 35 + int(
                ((i + 1) / total) * 25
            )

            job["progress"] = progress

        # -------------------------------------------------
        # STEP 4
        # -------------------------------------------------

        subtitle_file = (
            work_folder /
            "subtitles.srt"
        )

        create_srt(
            translated_segments,
            subtitle_file
        )

        # -------------------------------------------------
        # STEP 5
        # -------------------------------------------------

        job["progress"] = 62
        job["message"] = "إنشاء الدبلجة الصوتية..."

        voice_files = []

        total = len(
            translated_segments
        )

        for i, segment in enumerate(
            translated_segments
        ):

            voice_file = (
                work_folder /
                f"voice_{i}.mp3"
            )

            generate_speech(
                segment["text"],
                target_language,
                voice_file
            )

            voice_files.append({
                "file": voice_file,
                "start": segment["start"]
            })

            progress = 62 + int(
                ((i + 1) / total) * 23
            )

            job["progress"] = progress

        # -------------------------------------------------
        # STEP 6
        # -------------------------------------------------

        job["progress"] = 87
        job["message"] = "دمج الصوت مع الفيديو..."

        duration = get_duration(video)

        if duration <= 0:
            duration = 3600

        delayed_files = []

        for i, item in enumerate(
            voice_files
        ):

            delayed = (
                work_folder /
                f"delayed_{i}.wav"
            )

            delay = int(
                item["start"] * 1000
            )

            run_command([
                "ffmpeg",
                "-y",
                "-i",
                str(item["file"]),
                "-af",
                f"adelay={delay}:all=1",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(delayed)
            ])

            delayed_files.append(
                delayed
            )

        # -------------------------------------------------
        # STEP 7
        # -------------------------------------------------

        mixed_audio = (
            work_folder /
            "dubbed.wav"
        )

        if delayed_files:

            command = [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r=48000:cl=stereo:d={duration}"
            ]

            for file in delayed_files:
                command += [
                    "-i",
                    str(file)
                ]

            inputs = "".join(
                f"[{i}:a]"
                for i in range(
                    1,
                    len(delayed_files) + 1
                )
            )

            filter_complex = (
                f"[0:a]{inputs}"
                f"amix="
                f"inputs={len(delayed_files)+1}:"
                f"duration=first:"
                f"normalize=0"
                f"[mixed]"
            )

            command += [
                "-filter_complex",
                filter_complex,
                "-map",
                "[mixed]",
                "-t",
                str(duration),
                str(mixed_audio)
            ]

            run_command(command)

        else:

            raise RuntimeError(
                "لم يتم إنشاء أي صوت للدبلجة."
            )

        # -------------------------------------------------
        # STEP 8
        # -------------------------------------------------

        job["progress"] = 95
        job["message"] = "إنتاج الفيديو النهائي..."

        output_video = (
            OUTPUTS /
            f"{job_id}.mp4"
        )

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(mixed_audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest"
        ]

        # -------------------------------------------------
        # Optional subtitles
        # -------------------------------------------------

        if subtitles:

            # Add SRT as a selectable subtitle track.
            command += [
                "-i",
                str(subtitle_file),
                "-map",
                "2:0",
                "-c:s",
                "mov_text",
                "-metadata:s:s:0",
                "language=ara"
            ]

        command += [
            str(output_video)
        ]

        run_command(command)

        # -------------------------------------------------
        # DONE
        # -------------------------------------------------

        job["status"] = "done"
        job["progress"] = 100
        job["message"] = (
            "اكتملت الدبلجة والترجمة بنجاح!"
        )

        job["download"] = (
            f"/api/download/{job_id}"
        )

        # Clean uploaded file
        try:
            video.unlink()
        except:
            pass

    except Exception as error:

        print(
            f"JOB ERROR {job_id}:",
            error
        )

        job["status"] = "error"
        job["progress"] = 0
        job["message"] = (
            "حدث خطأ أثناء معالجة الفيديو."
        )
        job["error"] = str(error)


# =========================================================
# HOME
# =========================================================

@app.get("/")
def home():

    return FileResponse(
        BASE /
        "static" /
        "index.html"
    )


# =========================================================
# UPLOAD
# =========================================================

@app.post("/api/upload")
async def upload_video(
    file: UploadFile = File(...)
):

    extension = Path(
        file.filename or ""
    ).suffix.lower()

    supported_formats = {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".avi",
        ".m4v"
    }

    if extension not in supported_formats:

        raise HTTPException(
            status_code=400,
            detail="صيغة الفيديو غير مدعومة."
        )

    job_id = uuid.uuid4().hex

    video_path = (
        UPLOADS /
        f"{job_id}{extension}"
    )

    total_size = 0

    try:

        with open(
            video_path,
            "wb"
        ) as buffer:

            while True:

                chunk = await file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                total_size += len(chunk)

                if total_size > MAX_FILE_SIZE:

                    buffer.close()

                    try:
                        video_path.unlink()
                    except:
                        pass

                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "الحد الأقصى لحجم الفيديو "
                            "هو 500MB."
                        )
                    )

                buffer.write(chunk)

    except HTTPException:
        raise

    except Exception as error:

        try:
            video_path.unlink()
        except:
            pass

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    jobs[job_id] = {
        "status": "uploaded",
        "progress": 0,
        "message": "تم رفع الفيديو بنجاح."
    }

    return {
        "job_id": job_id
    }


# =========================================================
# START DUBBING
# =========================================================

@app.post("/api/dub/{job_id}")
async def start_dubbing(
    job_id: str,
    background_tasks: BackgroundTasks,
    source: str = Form("auto"),
    target: str = Form("ar"),
    subtitles: bool = Form(True)
):

    if job_id not in jobs:

        raise HTTPException(
            status_code=404,
            detail="المهمة غير موجودة."
        )

    if target not in LANGUAGES:

        raise HTTPException(
            status_code=400,
            detail="لغة الدبلجة غير مدعومة."
        )

    video_files = list(
        UPLOADS.glob(
            job_id + ".*"
        )
    )

    if not video_files:

        raise HTTPException(
            status_code=404,
            detail="الفيديو غير موجود."
        )

    jobs[job_id]["status"] = (
        "processing"
    )

    jobs[job_id]["progress"] = 1

    background_tasks.add_task(
        process_video,
        job_id,
        video_files[0],
        source,
        target,
        subtitles
    )

    return {
        "ok": True,
        "message": "بدأت المعالجة."
    }


# =========================================================
# STATUS
# =========================================================

@app.get("/api/status/{job_id}")
def get_status(job_id: str):

    if job_id not in jobs:

        raise HTTPException(
            status_code=404,
            detail="المهمة غير موجودة."
        )

    return jobs[job_id]


# =========================================================
# DOWNLOAD
# =========================================================

@app.get("/api/download/{job_id}")
def download_video(job_id: str):

    output = (
        OUTPUTS /
        f"{job_id}.mp4"
    )

    if not output.exists():

        raise HTTPException(
            status_code=404,
            detail="الفيديو النهائي غير جاهز."
        )

    return FileResponse(
        output,
        media_type="video/mp4",
        filename="DubAI_result.mp4"
    )


# =========================================================
# HEALTH
# =========================================================

@app.get("/api/health")
def health():

    return {
        "ok": True,
        "app": "DubAI",
        "version": "2.0",
        "openai_configured": bool(
            OPENAI_API_KEY
        )
    }
