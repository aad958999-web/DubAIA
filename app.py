import os,uuid,subprocess
from pathlib import Path
from fastapi import FastAPI,UploadFile,File,Form,HTTPException,BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from faster_whisper import WhisperModel
from deep_translator import GoogleTranslator
import edge_tts

BASE=Path(__file__).parent
UPLOADS=BASE/"uploads"; WORK=BASE/"work"; OUTPUTS=BASE/"outputs"
for d in (UPLOADS,WORK,OUTPUTS): d.mkdir(exist_ok=True)
app=FastAPI(title="DubAI")
app.mount("/static",StaticFiles(directory=str(BASE/"static")),name="static")
jobs={}; model=None
VOICES={"ar":"ar-SA-HamedNeural","en":"en-US-GuyNeural","fr":"fr-FR-HenriNeural","es":"es-ES-AlvaroNeural","de":"de-DE-ConradNeural","it":"it-IT-DiegoNeural","pt":"pt-BR-AntonioNeural","tr":"tr-TR-AhmetNeural","ru":"ru-RU-DmitryNeural","zh-CN":"zh-CN-YunxiNeural","ja":"ja-JP-KeitaNeural","ko":"ko-KR-InJoonNeural"}
LANGS=set(VOICES)

def run(c):
 p=subprocess.run(c,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 if p.returncode: raise RuntimeError(p.stderr[-2000:])
 return p.stdout
def duration(p): return float(run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",str(p)]).strip())
def get_model():
 global model
 if model is None: model=WhisperModel("base",device="cpu",compute_type="int8")
 return model
def st(sec):
 ms=int(sec*1000); return f"{ms//3600000:02d}:{(ms%3600000)//60000:02d}:{(ms%60000)//1000:02d},{ms%1000:03d}"

async def process(jid,video,src,tgt,subs):
 j=jobs[jid]
 try:
  j.update(progress=3,message="استخراج الصوت...")
  dur=duration(video); w=WORK/jid; w.mkdir(exist_ok=True); wav=w/"audio.wav"
  run(["ffmpeg","-y","-i",str(video),"-vn","-ac","1","-ar","16000","-c:a","pcm_s16le",str(wav)])
  j.update(progress=12,message="تحويل الكلام إلى نص...")
  info=get_model(); segs,_=info.transcribe(str(wav),language=None if src=="auto" else src,vad_filter=True)
  segs=[{"start":s.start,"end":s.end,"text":s.text.strip()} for s in segs if s.text.strip()]
  if not segs: raise RuntimeError("لم يتم العثور على كلام واضح.")
  outsegs=[]
  for i,s in enumerate(segs):
   text=s["text"] if src==tgt else GoogleTranslator(source="auto" if src=="auto" else src,target=tgt).translate(s["text"])
   outsegs.append({**s,"text":text}); j["progress"]=15+int(i/len(segs)*30)
  if subs:
   with open(w/"subtitles.srt","w",encoding="utf-8") as f:
    for i,s in enumerate(outsegs,1): f.write(f'{i}\n{st(s["start"])} --> {st(s["end"])}\n{s["text"]}\n\n')
  j.update(progress=48,message="إنشاء الدبلجة الصوتية...")
  tracks=[]
  for i,s in enumerate(outsegs):
   mp=w/f"t{i}.mp3"; wav2=w/f"d{i}.wav"
   await edge_tts.Communicate(s["text"],VOICES[tgt]).save(str(mp))
   run(["ffmpeg","-y","-i",str(mp),"-af",f"adelay={int(s['start']*1000)}:all=1","-ar","48000","-ac","2",str(wav2)])
   tracks.append(wav2); j["progress"]=48+int(i/len(outsegs)*40)
  mix=w/"mix.wav"; cmd=["ffmpeg","-y","-f","lavfi","-i",f"anullsrc=r=48000:cl=stereo:d={dur}"]
  for p in tracks: cmd+=["-i",str(p)]
  ins="".join(f"[{i+1}:a]" for i in range(len(tracks)))
  cmd+=["-filter_complex",f"[0:a]{ins}amix=inputs={len(tracks)+1}:duration=first:normalize=0[m]","-map","[m]","-t",str(dur),str(mix)]
  run(cmd)
  j.update(progress=92,message="دمج الصوت مع الفيديو...")
  out=OUTPUTS/f"{jid}.mp4"
  run(["ffmpeg","-y","-i",str(video),"-i",str(mix),"-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-b:a","192k","-shortest",str(out)])
  j.update(status="done",progress=100,message="اكتملت الدبلجة!",download=f"/api/download/{jid}")
 except Exception as e: j.update(status="error",progress=0,message="حدث خطأ",error=str(e))

@app.get("/")
def home(): return FileResponse(BASE/"static/index.html")
@app.post("/api/upload")
async def upload(file:UploadFile=File(...)):
 data=await file.read()
 if len(data)>500*1024*1024: raise HTTPException(413,"الحد الأقصى 500MB")
 ext=Path(file.filename or "").suffix.lower()
 if ext not in {".mp4",".mov",".mkv",".webm",".avi",".m4v"}: raise HTTPException(400,"صيغة الفيديو غير مدعومة")
 jid=uuid.uuid4().hex; (UPLOADS/f"{jid}{ext}").write_bytes(data); jobs[jid]={"status":"uploaded","progress":0,"message":"تم رفع الفيديو"}
 return {"job_id":jid}
@app.post("/api/dub/{jid}")
async def dub(jid:str,bg:BackgroundTasks,source:str=Form("auto"),target:str=Form("ar"),subtitles:bool=Form(True)):
 if jid not in jobs or not list(UPLOADS.glob(jid+".*")): raise HTTPException(404,"المهمة غير موجودة")
 if target not in LANGS: raise HTTPException(400,"لغة الدبلجة غير مدعومة")
 bg.add_task(process,jid,list(UPLOADS.glob(jid+".*"))[0],source,target,subtitles); jobs[jid]["status"]="processing"; return {"ok":True}
@app.get("/api/status/{jid}")
def status(jid:str):
 if jid not in jobs: raise HTTPException(404)
 return jobs[jid]
@app.get("/api/download/{jid}")
def download(jid:str):
 p=OUTPUTS/f"{jid}.mp4"
 if not p.exists(): raise HTTPException(404,"لم يجهز الفيديو")
 return FileResponse(p,media_type="video/mp4",filename="DubAI_result.mp4")
