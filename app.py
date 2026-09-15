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


# =========================================================
# إعداد المشروع
# =========================================================

BASE = Path(__file__).parent

UPLOADS = BASE / "uploads"
WORK = BASE / "work"
OUTPUTS = BASE / "outputs"
STATIC = BASE / "static"

for folder in [UPLOADS, WORK, OUTPUTS]:
    folder.mkdir(parents=True, exist_ok=True)


app = FastAPI(
    title="DubAI Free",
    version="3.0"
)

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC)),
    name="static"
)


jobs = {}

MODEL = None


# =========================================================
# اللغات والأصوات
# =========================================================

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


LANGUAGES = set(VOICES.keys())


# =========================================================
# تنفيذ أوامر FFmpeg
# =========================================================

def run(command):

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


# =========================================================
# مدة الفيديو
# =========================================================

def get_duration(video):

    result = run([
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(video)
    ])

    return float(result.strip())


# =========================================================
# نموذج Whisper
# =========================================================

def get_model():

    global MODEL

    if MODEL is None:

        MODEL = WhisperModel(
            "tiny",
            device="cpu",
            compute_type="int8"
        )

    return MODEL


# =========================================================
# الوقت الخاص بالترجمة
# =========================================================

def srt_time(seconds):

    ms = int(seconds * 1000)

    hours = ms // 3600000
    ms %= 3600000

    minutes = ms // 60000
    ms %= 60000

    seconds = ms // 1000
    ms %= 1000

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{seconds:02d},"
        f"{ms:03d}"
    )


# =========================================================
# استخراج الصوت
# =========================================================

def extract_audio(video, audio):

    run([
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
        str(audio)
    ])


# =========================================================
# معالجة الفيديو
# =========================================================

async def process_video(
    job_id,
    video,
    source_language,
    target_language,
    subtitles
):

    job = jobs[job_id]

    try:

        work = WORK / job_id

        work.mkdir(
            parents=True,
            exist_ok=True
        )


        # -------------------------------------------------
        # 1 - استخراج الصوت
        # -------------------------------------------------

        job["progress"] = 5
        job["message"] = "🎧 استخراج الصوت..."

        audio = work / "audio.wav"

        extract_audio(
            video,
            audio
        )


        # -------------------------------------------------
        # 2 - Whisper
        # -------------------------------------------------

        job["progress"] = 15
        job["message"] = "🎤 التعرف على الكلام..."

        model = get_model()

        segments, info = model.transcribe(
            str(audio),
            language=None if source_language == "auto"
            else source_language,
            vad_filter=True,
            beam_size=1
        )


        segments = list(segments)


        if not segments:

            raise RuntimeError(
                "لم يتم العثور على كلام واضح في الفيديو."
            )


        # -------------------------------------------------
        # 3 - الترجمة
        # -------------------------------------------------

        job["progress"] = 30
        job["message"] = "🌍 ترجمة الكلام..."

        translated = []

        total = len(segments)


        for index, segment in enumerate(segments):

            text = segment.text.strip()

            if not text:
                continue


            if source_language == target_language:

                result = text

            else:

                source = (
                    "auto"
                    if source_language == "auto"
                    else source_language
                )

                result = GoogleTranslator(
                    source=source,
                    target=target_language
                ).translate(text)


            translated.append({
                "start": float(segment.start),
                "end": float(segment.end),
                "text": result
            })


            job["progress"] = (
                30 +
                int(
                    ((index + 1) / total) * 25
                )
            )


        # -------------------------------------------------
        # 4 - إنشاء SRT
        # -------------------------------------------------

        srt_file = work / "subtitles.srt"


        with open(
            srt_file,
            "w",
            encoding="utf-8"
        ) as f:

            for i, segment in enumerate(
                translated,
                1
            ):

                f.write(
                    f"{i}\n"
                )

                f.write(
                    f"{srt_time(segment['start'])} --> "
                    f"{srt_time(segment['end'])}\n"
                )

                f.write(
                    segment["text"]
                )

                f.write("\n\n")


        # -------------------------------------------------
        # 5 - إنشاء الأصوات
        # -------------------------------------------------

        job["progress"] = 58
        job["message"] = "🗣️ إنشاء الصوت المترجم..."

        voice_files = []

        total = len(translated)

        voice = VOICES.get(target_language)

        if not voice:

            raise RuntimeError(
                "لغة الصوت غير مدعومة."
            )


        for index, segment in enumerate(
            translated
        ):

            mp3 = (
                work /
                f"voice_{index}.mp3"
            )


            communicate = edge_tts.Communicate(
                segment["text"],
                voice
            )


            await communicate.save(
                str(mp3)
            )


            voice_files.append({
                "file": mp3,
                "start": segment["start"]
            })


            job["progress"] = (
                58 +
                int(
                    ((index + 1) / total) * 25
                )
            )


        # -------------------------------------------------
        # 6 - دمج الأصوات
        # -------------------------------------------------

        job["progress"] = 85
        job["message"] = "🎬 دمج الصوت مع الفيديو..."


        duration = get_duration(video)


        delayed = []


        for index, item in enumerate(
            voice_files
        ):

            wav = (
                work /
                f"delay_{index}.wav"
            )


            delay = int(
                item["start"] * 1000
            )


            run([
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
                str(wav)
            ])


            delayed.append(wav)


        if not delayed:

            raise RuntimeError(
                "لم يتم إنشاء الصوت."
            )


        mixed = (
            work /
            "mixed.wav"
        )


        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={duration}"
        ]


        for file in delayed:

            command += [
                "-i",
                str(file)
            ]


        inputs = ""


        for i in range(
            1,
            len(delayed) + 1
        ):

            inputs += f"[{i}:a]"


        filter_complex = (
            f"[0:a]{inputs}"
            f"amix="
            f"inputs={len(delayed)+1}:"
            f"duration=first:"
            f"normalize=0"
            f"[audio]"
        )


        command += [
            "-filter_complex",
            filter_complex,
            "-map",
            "[audio]",
            "-t",
            str(duration),
            str(mixed)
        ]


        run(command)


        # -------------------------------------------------
        # 7 - الفيديو النهائي
        # -------------------------------------------------

        job["progress"] = 95
        job["message"] = "📦 تجهيز الفيديو النهائي..."


        output = (
            OUTPUTS /
            f"{job_id}.mp4"
        )


        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(mixed),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k"
        ]


        # إضافة ملف الترجمة
        if subtitles:

            command += [
                "-i",
                str(srt_file),
                "-map",
                "2:0",
                "-c:s",
                "mov_text"
            ]


        command += [
            "-shortest",
            str(output)
        ]


        run(command)


        # -------------------------------------------------
        # 8 - نجاح
        # -------------------------------------------------

        job["status"] = "done"

        job["progress"] = 100

        job["message"] = (
            "✅ اكتملت الدبلجة والترجمة!"
        )

        job["download"] = (
            f"/api/download/{job_id}"
        )


        # حذف بعض الملفات المؤقتة
        try:

            audio.unlink()

        except:
            pass


    except Exception as error:

        print(
            "DubAI ERROR:",
            error
        )

        job["status"] = "error"

        job["progress"] = 0

        job["message"] = (
            "❌ حدث خطأ أثناء المعالجة."
        )

        job["error"] = str(error)


# =========================================================
# الصفحة الرئيسية
# =========================================================

@app.get("/")
def home():

    return FileResponse(
        STATIC /
        "index.html"
    )


# =========================================================
# رفع الفيديو
# =========================================================

@app.post("/api/upload")
async def upload_video(
    file: UploadFile = File(...)
):

    extension = Path(
        file.filename or ""
    ).suffix.lower()


    allowed = {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".avi",
        ".m4v"
    }


    if extension not in allowed:

        raise HTTPException(
            status_code=400,
            detail="صيغة الفيديو غير مدعومة."
        )


    job_id = uuid.uuid4().hex


    video = (
        UPLOADS /
        f"{job_id}{extension}"
    )


    size = 0


    try:

        with open(
            video,
            "wb"
        ) as f:

            while True:

                chunk = await file.read(
                    1024 * 1024
                )

                if not chunk:
                    break


                size += len(chunk)


                if size > 500 * 1024 * 1024:

                    raise HTTPException(
                        status_code=413,
                        detail="الفيديو أكبر من 500MB."
                    )


                f.write(chunk)


    except HTTPException:

        if video.exists():
            video.unlink()

        raise


    except Exception as error:

        if video.exists():
            video.unlink()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )


    jobs[job_id] = {
        "status": "uploaded",
        "progress": 0,
        "message": "تم رفع الفيديو."
    }


    return {
        "job_id": job_id
    }


# =========================================================
# بدء الدبلجة
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


    files = list(
        UPLOADS.glob(
            job_id + ".*"
        )
    )


    if not files:

        raise HTTPException(
            status_code=404,
            detail="الفيديو غير موجود."
        )


    jobs[job_id]["status"] = (
        "processing"
    )


    background_tasks.add_task(
        process_video,
        job_id,
        files[0],
        source,
        target,
        subtitles
    )


    return {
        "ok": True
    }


# =========================================================
# حالة المهمة
# =========================================================

@app.get("/api/status/{job_id}")
def status(job_id: str):

    if job_id not in jobs:

        raise HTTPException(
            status_code=404,
            detail="المهمة غير موجودة."
        )


    return jobs[job_id]


# =========================================================
# تحميل الفيديو
# =========================================================

@app.get("/api/download/{job_id}")
def download(job_id: str):

    file = (
        OUTPUTS /
        f"{job_id}.mp4"
    )


    if not file.exists():

        raise HTTPException(
            status_code=404,
            detail="الفيديو غير جاهز."
        )


    return FileResponse(
        file,
        media_type="video/mp4",
        filename="DubAI_result.mp4"
    )


# =========================================================
# فحص السيرفر
# =========================================================

@app.get("/api/health")
def health():

    return {
        "ok": True,
        "app": "DubAI Free",
        "version": "3.0"
    }
