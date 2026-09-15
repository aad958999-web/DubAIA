import os
import uuid
import subprocess
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from faster_whisper import WhisperModel
from deep_translator import GoogleTranslator
import edge_tts


# =========================
# إعداد المجلدات
# =========================

BASE = Path(__file__).parent

UPLOADS = BASE / "uploads"
WORK = BASE / "work"
OUTPUTS = BASE / "outputs"

for folder in (UPLOADS, WORK, OUTPUTS):
    folder.mkdir(exist_ok=True)


# =========================
# إنشاء التطبيق
# =========================

app = FastAPI(title="DubAI")

app.mount(
    "/static",
    StaticFiles(directory=str(BASE / "static")),
    name="static"
)


# =========================
# تخزين المهام
# =========================

jobs = {}

model = None


# =========================
# أصوات الدبلجة
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

LANGUAGES = set(VOICES.keys())


# =========================
# تشغيل أوامر النظام
# =========================

def run(command):

    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if process.returncode != 0:
        raise RuntimeError(
            process.stderr[-3000:]
        )

    return process.stdout


# =========================
# مدة الفيديو
# =========================

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


# =========================
# تحميل Whisper
# =========================

def get_model():

    global model

    if model is None:

        model = WhisperModel(
            "base",
            device="cpu",
            compute_type="int8"
        )

    return model


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
# معالجة الفيديو
# =========================

async def process_video(
    job_id,
    video,
    source_language,
    target_language,
    subtitles
):

    job = jobs[job_id]

    try:

        # -------------------------
        # استخراج الصوت
        # -------------------------

        job["progress"] = 3
        job["message"] = "استخراج الصوت من الفيديو..."

        video_duration = get_duration(video)

        work_folder = WORK / job_id

        work_folder.mkdir(
            exist_ok=True
        )

        audio_file = work_folder / "audio.wav"

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
            str(audio_file)
        ])


        # -------------------------
        # تحويل الكلام إلى نص
        # -------------------------

        job["progress"] = 12

        job["message"] = (
            "تحويل الكلام إلى نص بالذكاء الاصطناعي..."
        )

        whisper = get_model()

        segments, info = whisper.transcribe(
            str(audio_file),
            language=(
                None
                if source_language == "auto"
                else source_language
            ),
            vad_filter=True
        )


        segments = [
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "text": segment.text.strip()
            }
            for segment in segments
            if segment.text.strip()
        ]


        if not segments:

            raise RuntimeError(
                "لم يتم العثور على كلام واضح في الفيديو."
            )


        # -------------------------
        # الترجمة
        # -------------------------

        job["progress"] = 20

        job["message"] = (
            "ترجمة الكلام..."
        )

        translated_segments = []

        total = len(segments)


        for index, segment in enumerate(segments):

            original_text = segment["text"]


            if source_language == target_language:

                translated_text = original_text

            else:

                translated_text = GoogleTranslator(
                    source=(
                        "auto"
                        if source_language == "auto"
                        else source_language
                    ),
                    target=target_language
                ).translate(
                    original_text
                )


            translated_segments.append({
                "start": segment["start"],
                "end": segment["end"],
                "text": translated_text
            })


            job["progress"] = (
                20 +
                int(
                    (index + 1) /
                    total *
                    28
                )
            )


        # -------------------------
        # إنشاء الترجمة SRT
        # -------------------------

        subtitle_file = (
            work_folder /
            "subtitles.srt"
        )


        with open(
            subtitle_file,
            "w",
            encoding="utf-8"
        ) as subtitle:

            for index, segment in enumerate(
                translated_segments,
                1
            ):

                subtitle.write(
                    f"{index}\n"
                )

                subtitle.write(
                    f"{srt_time(segment['start'])}"
                    f" --> "
                    f"{srt_time(segment['end'])}\n"
                )

                subtitle.write(
                    f"{segment['text']}\n\n"
                )


        # -------------------------
        # إنشاء صوت الدبلجة
        # -------------------------

        job["progress"] = 50

        job["message"] = (
            "إنشاء الصوت المترجم..."
        )


        tracks = []

        total = len(
            translated_segments
        )


        for index, segment in enumerate(
            translated_segments
        ):

            mp3_file = (
                work_folder /
                f"voice_{index}.mp3"
            )

            delayed_audio = (
                work_folder /
                f"voice_{index}.wav"
            )


            voice = VOICES.get(
                target_language
            )


            if not voice:

                raise RuntimeError(
                    "لغة الدبلجة غير مدعومة."
                )


            # إنشاء الصوت

            communicator = edge_tts.Communicate(
                segment["text"],
                voice
            )

            await communicator.save(
                str(mp3_file)
            )


            # وضع الصوت في توقيته الصحيح

            delay = max(
                0,
                int(
                    segment["start"] *
                    1000
                )
            )


            run([
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
                str(delayed_audio)
            ])


            tracks.append(
                delayed_audio
            )


            job["progress"] = (
                50 +
                int(
                    (index + 1) /
                    total *
                    38
                )
            )


        # -------------------------
        # دمج جميع أصوات الدبلجة
        # -------------------------

        job["progress"] = 90

        job["message"] = (
            "دمج أصوات الدبلجة..."
        )


        mixed_audio = (
            work_folder /
            "dubbed_audio.wav"
        )


        command = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={video_duration}"
        ]


        for track in tracks:

            command += [
                "-i",
                str(track)
            ]


        inputs = "".join(
            f"[{index}:a]"
            for index in range(
                1,
                len(tracks) + 1
            )
        )


        filter_complex = (
            f"[0:a]"
            f"{inputs}"
            f"amix="
            f"inputs={len(tracks)+1}:"
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
            str(video_duration),
            str(mixed_audio)
        ]


        run(command)


        # -------------------------
        # إخراج الفيديو النهائي
        # -------------------------

        job["progress"] = 94

        job["message"] = (
            "إنتاج الفيديو النهائي..."
        )


        output_video = (
            OUTPUTS /
            f"{job_id}.mp4"
        )


        run([
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

            "-shortest",

            str(output_video)
        ])


        # -------------------------
        # نجاح
        # -------------------------

        job["status"] = "done"

        job["progress"] = 100

        job["message"] = (
            "اكتملت الدبلجة والترجمة بنجاح!"
        )

        job["download"] = (
            f"/api/download/{job_id}"
        )


    except Exception as error:

        job["status"] = "error"

        job["progress"] = 0

        job["message"] = (
            "حدث خطأ أثناء معالجة الفيديو."
        )

        job["error"] = str(error)


# =========================
# الصفحة الرئيسية
# =========================

@app.get("/")
def home():

    return FileResponse(
        BASE /
        "static" /
        "index.html"
    )


# =========================
# رفع الفيديو
# =========================

@app.post("/api/upload")
async def upload_video(
    file: UploadFile = File(...)
):

    data = await file.read()


    # الحد الأقصى 500MB

    if len(data) > 500 * 1024 * 1024:

        raise HTTPException(
            status_code=413,
            detail="الحد الأقصى لحجم الفيديو هو 500MB."
        )


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


    video_path.write_bytes(
        data
    )


    jobs[job_id] = {

        "status": "uploaded",

        "progress": 0,

        "message": "تم رفع الفيديو بنجاح."

    }


    return {
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


    if target not in LANGUAGES:

        raise HTTPException(
            status_code=400,
            detail="لغة الدبلجة غير مدعومة."
        )


    jobs[job_id]["status"] = (
        "processing"
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
        "ok": True
    }


# =========================
# حالة المعالجة
# =========================

@app.get("/api/status/{job_id}")
def get_status(
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
def download_video(
    job_id: str
):

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


# =========================
# فحص الخادم
# =========================

@app.get("/api/health")
def health():

    return {
        "ok": True,
        "app": "DubAI"
    }
