import os
import uuid
import asyncio
import subprocess
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from faster_whisper import WhisperModel
from deep_translator import GoogleTranslator
import edge_tts


# =========================
# إعدادات التطبيق
# =========================

BASE_DIR = Path(__file__).resolve().parent

STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
WORK_DIR = BASE_DIR / "work"
OUTPUT_DIR = BASE_DIR / "outputs"

for directory in [STATIC_DIR, UPLOAD_DIR, WORK_DIR, OUTPUT_DIR]:
    directory.mkdir(parents=True, exist_ok=True)


app = FastAPI(
    title="DubAI",
    version="1.0.0"
)

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static"
)


# =========================
# الذاكرة المؤقتة للمهام
# =========================

jobs = {}

whisper_model = None


# =========================
# اللغات والأصوات
# =========================

VOICES = {
    "ar": "ar-SA-HamedNeural",
    "en": "en-US-GuyNeural",
    "fr": "fr-FR-HenriNeural",
    "es": "es-ES-AlvaroNeural",
    "de": "de-DE-ConradNeural",
    "it": "it-IT-DiegoNeural",
    "pt": "pt-BR-AntonioNeural",
    "tr": "tr-TR-AhmetNeural",
    "ru": "ru-RU-DmitryNeural",
    "zh-CN": "zh-CN-YunxiNeural",
    "ja": "ja-JP-KeitaNeural",
    "ko": "ko-KR-InJoonNeural"
}

SUPPORTED_LANGUAGES = set(VOICES.keys())

SUPPORTED_VIDEO_FORMATS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v"
}

MAX_FILE_SIZE = 500 * 1024 * 1024


# =========================
# تشغيل أوامر FFmpeg
# =========================

def run_command(command):
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if process.returncode != 0:
        raise RuntimeError(
            process.stderr[-4000:]
        )

    return process.stdout


# =========================
# مدة الفيديو
# =========================

def get_video_duration(video_path):
    result = run_command([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ])

    return float(result.strip())


# =========================
# تحميل نموذج Whisper
# =========================

def get_whisper_model():
    global whisper_model

    if whisper_model is None:
        whisper_model = WhisperModel(
            "base",
            device="cpu",
            compute_type="int8"
        )

    return whisper_model


# =========================
# تحويل الوقت إلى SRT
# =========================

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


# =========================
# تحديث حالة المهمة
# =========================

def update_job(job_id, progress, message):
    if job_id in jobs:
        jobs[job_id]["progress"] = progress
        jobs[job_id]["message"] = message


# =========================
# معالجة الفيديو
# =========================

async def process_video(
    job_id,
    video_path,
    source_language,
    target_language,
    subtitles
):

    try:

        update_job(
            job_id,
            3,
            "جاري استخراج الصوت من الفيديو..."
        )

        duration = get_video_duration(video_path)

        job_folder = WORK_DIR / job_id
        job_folder.mkdir(parents=True, exist_ok=True)

        audio_file = job_folder / "audio.wav"

        # استخراج الصوت
        run_command([
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_file)
        ])

        # =========================
        # Whisper
        # =========================

        update_job(
            job_id,
            12,
            "جاري تحويل الكلام إلى نص بالذكاء الاصطناعي..."
        )

        model = get_whisper_model()

        segments_result, info = model.transcribe(
            str(audio_file),
            language=(
                None
                if source_language == "auto"
                else source_language
            ),
            vad_filter=True
        )

        segments = []

        for segment in segments_result:

            text = segment.text.strip()

            if text:

                segments.append({
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": text
                })

        if not segments:
            raise RuntimeError(
                "لم يتم العثور على كلام واضح في الفيديو."
            )

        # =========================
        # الترجمة
        # =========================

        update_job(
            job_id,
            20,
            "جاري ترجمة الكلام..."
        )

        translated_segments = []

        total = len(segments)

        for index, segment in enumerate(segments):

            original_text = segment["text"]

            if source_language == target_language:
                translated_text = original_text

            else:

                translator = GoogleTranslator(
                    source=(
                        "auto"
                        if source_language == "auto"
                        else source_language
                    ),
                    target=target_language
                )

                translated_text = translator.translate(
                    original_text
                )

            translated_segments.append({
                "start": segment["start"],
                "end": segment["end"],
                "text": translated_text
            })

            progress = 20 + int(
                ((index + 1) / total) * 25
            )

            update_job(
                job_id,
                progress,
                f"جاري ترجمة المقطع {index + 1} من {total}..."
            )

        # =========================
        # إنشاء ملف الترجمة
        # =========================

        subtitle_file = job_folder / "subtitles.srt"

        with open(
            subtitle_file,
            "w",
            encoding="utf-8"
        ) as file:

            for index, segment in enumerate(
                translated_segments,
                start=1
            ):

                file.write(
                    f"{index}\n"
                )

                file.write(
                    f"{srt_time(segment['start'])} --> "
                    f"{srt_time(segment['end'])}\n"
                )

                file.write(
                    f"{segment['text']}\n\n"
                )

        # =========================
        # إنشاء أصوات الدبلجة
        # =========================

        update_job(
            job_id,
            48,
            "جاري إنشاء الصوت المترجم..."
        )

        voice = VOICES.get(target_language)

        if not voice:
            raise RuntimeError(
                "لغة الدبلجة غير مدعومة."
            )

        audio_tracks = []

        total = len(translated_segments)

        for index, segment in enumerate(
            translated_segments
        ):

            mp3_file = (
                job_folder /
                f"voice_{index}.mp3"
            )

            wav_file = (
                job_folder /
                f"voice_{index}.wav"
            )

            communicator = edge_tts.Communicate(
                segment["text"],
                voice
            )

            await communicator.save(
                str(mp3_file)
            )

            delay = max(
                0,
                int(segment["start"] * 1000)
            )

            run_command([
                "ffmpeg",
                "-y",
                "-i",
                str(mp3_file),
                "-af",
                f"adelay={delay}:all=1",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(wav_file)
            ])

            audio_tracks.append(
                wav_file
            )

            progress = 48 + int(
                ((index + 1) / total) * 35
            )

            update_job(
                job_id,
                progress,
                f"جاري إنشاء الصوت {index + 1} من {total}..."
            )

        # =========================
        # دمج أصوات الدبلجة
        # =========================

        update_job(
            job_id,
            85,
            "جاري دمج أصوات الدبلجة..."
        )

        mixed_audio = (
            job_folder /
            "dubbed_audio.wav"
        )

        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={duration}"
        ]

        for track in audio_tracks:
            command.extend([
                "-i",
                str(track)
            ])

        input_count = len(audio_tracks) + 1

        audio_inputs = "".join(
            f"[{i}:a]"
            for i in range(1, input_count)
        )

        filter_complex = (
            f"[0:a]"
            f"{audio_inputs}"
            f"amix="
            f"inputs={input_count}:"
            f"duration=first:"
            f"dropout_transition=0:"
            f"normalize=0"
            f"[mixed]"
        )

        command.extend([
            "-filter_complex",
            filter_complex,
            "-map",
            "[mixed]",
            "-t",
            str(duration),
            "-ar",
            "48000",
            "-ac",
            "2",
            str(mixed_audio)
        ])

        run_command(command)

        # =========================
        # الفيديو النهائي
        # =========================

        update_job(
            job_id,
            94,
            "جاري إنشاء الفيديو النهائي..."
        )

        output_video = (
            OUTPUT_DIR /
            f"{job_id}.mp4"
        )

        run_command([
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
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
            "-shortest",
            str(output_video)
        ])

        # =========================
        # حفظ النتيجة
        # =========================

        jobs[job_id]["status"] = "done"
        jobs[job_id]["progress"] = 100
        jobs[job_id]["message"] = (
            "اكتملت الدبلجة والترجمة بنجاح!"
        )
        jobs[job_id]["download"] = (
            f"/api/download/{job_id}"
        )

        # حفظ مسار الترجمة
        jobs[job_id]["subtitle_file"] = (
            f"/api/subtitles/{job_id}"
            if subtitles
            else None
        )

    except Exception as error:

        jobs[job_id]["status"] = "error"
        jobs[job_id]["progress"] = 0
        jobs[job_id]["message"] = (
            "حدث خطأ أثناء معالجة الفيديو."
        )
        jobs[job_id]["error"] = str(error)


# =========================
# الصفحة الرئيسية
# =========================

@app.get("/")
async def home():

    index_file = STATIC_DIR / "index.html"

    if not index_file.exists():

        raise HTTPException(
            status_code=404,
            detail="ملف index.html غير موجود داخل مجلد static."
        )

    return FileResponse(
        index_file
    )


# =========================
# رفع الفيديو
# =========================

@app.post("/api/upload")
async def upload_video(
    file: UploadFile = File(...)
):

    filename = file.filename or ""

    extension = Path(
        filename
    ).suffix.lower()

    if extension not in SUPPORTED_VIDEO_FORMATS:

        raise HTTPException(
            status_code=400,
            detail=(
                "صيغة الفيديو غير مدعومة. "
                "استخدم MP4 أو MOV أو MKV أو WEBM."
            )
        )

    # قراءة الملف
    data = await file.read()

    if len(data) > MAX_FILE_SIZE:

        raise HTTPException(
            status_code=413,
            detail="الحد الأقصى لحجم الفيديو هو 500MB."
        )

    job_id = uuid.uuid4().hex

    video_path = (
        UPLOAD_DIR /
        f"{job_id}{extension}"
    )

    video_path.write_bytes(data)

    jobs[job_id] = {
        "status": "uploaded",
        "progress": 0,
        "message": "تم رفع الفيديو بنجاح."
    }

    return {
        "ok": True,
        "job_id": job_id
    }


# =========================
# بدء الدبلجة
# =========================

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

    if target not in SUPPORTED_LANGUAGES:

        raise HTTPException(
            status_code=400,
            detail="لغة الدبلجة غير مدعومة."
        )

    video_files = list(
        UPLOAD_DIR.glob(
            f"{job_id}.*"
        )
    )

    if not video_files:

        raise HTTPException(
            status_code=404,
            detail="الفيديو غير موجود."
        )

    jobs[job_id]["status"] = "processing"
    jobs[job_id]["progress"] = 1
    jobs[job_id]["message"] = (
        "جاري بدء معالجة الفيديو..."
    )

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
        "job_id": job_id
    }


# =========================
# حالة المهمة
# =========================

@app.get("/api/status/{job_id}")
async def get_status(
    job_id: str
):

    if job_id not in jobs:

        raise HTTPException(
            status_code=404,
            detail="المهمة غير موجودة."
        )

    return jobs[job_id]


# =========================
# تحميل الفيديو النهائي
# =========================

@app.get("/api/download/{job_id}")
async def download_video(
    job_id: str
):

    output_file = (
        OUTPUT_DIR /
        f"{job_id}.mp4"
    )

    if not output_file.exists():

        raise HTTPException(
            status_code=404,
            detail="الفيديو النهائي غير جاهز."
        )

    return FileResponse(
        output_file,
        media_type="video/mp4",
        filename="DubAI_result.mp4"
    )


# =========================
# تحميل ملف الترجمة
# =========================

@app.get("/api/subtitles/{job_id}")
async def download_subtitles(
    job_id: str
):

    subtitle_file = (
        WORK_DIR /
        job_id /
        "subtitles.srt"
    )

    if not subtitle_file.exists():

        raise HTTPException(
            status_code=404,
            detail="ملف الترجمة غير موجود."
        )

    return FileResponse(
        subtitle_file,
        media_type="application/x-subrip",
        filename="DubAI_subtitles.srt"
    )


# =========================
# فحص حالة السيرفر
# =========================

@app.get("/api/health")
async def health():

    return {
        "ok": True,
        "app": "DubAI",
        "version": "1.0.0"
    }
