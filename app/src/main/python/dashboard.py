import asyncio
import json
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path as FilePath
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

# The standalone Android APK installs its bundled FFmpeg executable and shared
# libraries here before importing this module. Termux keeps using its own PATH.
_ANDROID_FFMPEG_DIR = "/data/data/com.amer.tiktokmonitor/files/ffmpeg"
if FilePath(_ANDROID_FFMPEG_DIR).is_dir():
    os.environ["PATH"] = _ANDROID_FFMPEG_DIR + os.pathsep + os.environ.get("PATH", "")
    os.environ["LD_LIBRARY_PATH"] = _ANDROID_FFMPEG_DIR + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")
    _ffmpeg_probe = shutil.which("ffmpeg")
    print(f"🎥 FFmpeg runtime: {_ffmpeg_probe or 'NOT FOUND'}")
    if _ffmpeg_probe:
        try:
            import subprocess as _ffmpeg_subprocess
            _probe = _ffmpeg_subprocess.run([_ffmpeg_probe, "-version"], capture_output=True, text=True, timeout=8)
            print(f"🎥 FFmpeg startup check: exit={_probe.returncode}; {(_probe.stdout or _probe.stderr)[:180].strip()}")
        except Exception as _probe_error:
            print(f"⚠️ FFmpeg startup check failed: {type(_probe_error).__name__}: {_probe_error}")

from TikTokLive import TikTokLiveClient
from TikTokLive.client.web.routes.fetch_video_data import (
    VideoFetchQuality,
    VideoFetchFormat,
)
from TikTokLive.events import (
    ConnectEvent,
    CommentEvent,
    GiftEvent,
    LikeEvent,
    FollowEvent,
    ShareEvent,
    JoinEvent,
    RoomUserSeqEvent,
    LiveEndEvent,
    LinkMicBattleEvent,
)

HOST = "127.0.0.1"
PORT = 8765
MAX_COMMENTS = 60
MAX_GIFTS = 40
MAX_JOINS = 40
MAX_VIEWER_NAMES = 120
APP_DATA_ROOT = FilePath(
    "/data/data/com.amer.tiktokmonitor/files/TikTokMonitor"
    if FilePath("/data/data/com.amer.tiktokmonitor").exists()
    else "/storage/emulated/0/TikTokMonitor"
)
APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
ARCHIVE_ROOT = APP_DATA_ROOT / "LIVE_History"

streams = OrderedDict()
clients = {}
loop = None
lock = threading.RLock()


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def safe_text(value, default=""):
    try:
        if value is None:
            return default
        return str(value)
    except Exception:
        return default


def user_info(user):
    if user is None:
        return {"nickname": "مستخدم", "unique_id": ""}
    return {
        "nickname": safe_text(getattr(user, "nickname", None), "مستخدم"),
        "unique_id": safe_text(getattr(user, "unique_id", None), ""),
    }


def new_stream(username):
    return {
        "username": username,
        "monitoring_enabled": False,
        "live": False,
        "viewers": 0,
        "viewer_names": OrderedDict(),
        "recent_joins": [],
        "likes": 0,
        "gifts": 0,
        "gift_coins": 0,
        "gift_events": [],
        "supporters": {},
        "supporters_sorted": [],
        "gift_combos": {},
        "follows": 0,
        "shares": 0,
        "follow_events": [],
        "share_events": [],
        "comment_count": 0,
        "comments": [],
        "room_id": None,
        "error": None,
        "started_at": None,
        "session_started_at": None,
        "session_ended_at": None,
        "session_dir": None,
        "session_files": {},
        "video_file": None,
        "video_recording": False,
        "last_viewer_update": None,
        "last_activity": None,
        "pk": {
            "active": False,
            "battle_id": None,
            "action": "",
            "bubble": "",
            "anchors": [],
            "armies": [],
            "results": [],
        },
    }


HTML = r'''<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>TikTok LIVE Monitor</title>
<style>
*{box-sizing:border-box}
:root{color-scheme:dark}
body{margin:0;background:#0b0d12;color:#f5f7fb;font-family:Arial,"Noto Sans Arabic",sans-serif}
header{position:sticky;top:0;z-index:10;background:rgba(14,16,22,.96);backdrop-filter:blur(12px);border-bottom:1px solid #252a35;padding:16px}
.header-inner{max-width:1100px;margin:auto;display:flex;justify-content:space-between;align-items:center;gap:12px}
h1{margin:0;font-size:21px}.sub{margin-top:5px;color:#8f98a8;font-size:12px}
.container{max-width:1100px;margin:auto;padding:14px}
.add{display:flex;gap:8px;margin-bottom:15px}.add input{flex:1;min-width:0}
input{background:#151922;color:#fff;border:1px solid #303745;border-radius:11px;padding:13px;font-size:15px;outline:none}
input:focus{border-color:#596579}button{border:0;border-radius:11px;min-height:43px;padding:0 17px;background:#fe2c55;color:#fff;font-weight:700;font-size:14px;cursor:pointer}
button:active{transform:scale(.98)}
.empty{text-align:center;color:#818a9a;padding:50px 10px;background:#12151c;border:1px solid #252a35;border-radius:14px}
.card{background:#12151c;border:1px solid #282e3a;border-radius:16px;padding:14px;margin-bottom:15px;box-shadow:0 8px 28px rgba(0,0,0,.16)}
.card-top{display:flex;justify-content:space-between;align-items:center;gap:10px}.user{font-size:18px;font-weight:800}.room{color:#7f8999;font-size:11px;margin-top:4px;direction:ltr;text-align:right;word-break:break-all}
.status{font-size:12px;padding:6px 10px;border-radius:20px;background:#2b303b;white-space:nowrap}.live{background:#153b28;color:#61ed91}.offline{color:#a0a8b6}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:14px}.stat{background:#0d1016;border:1px solid #202631;border-radius:11px;padding:11px 5px;text-align:center}.icon{font-size:18px}.value{margin-top:4px;font-weight:800;font-size:16px}.label{color:#818b9c;font-size:10px;margin-top:3px}
.section{margin-top:12px;background:#0d1016;border:1px solid #202631;border-radius:12px;overflow:hidden}.section-title{padding:10px 11px;font-weight:800;font-size:13px;border-bottom:1px solid #202631;display:flex;justify-content:space-between;gap:8px}.hint{font-weight:400;color:#7f8999;font-size:10px}
.scroll{max-height:210px;overflow:auto;padding:9px}.row{padding:7px 2px;border-bottom:1px solid #202631;font-size:12px}.row:last-child{border-bottom:0}.nick{font-weight:800}.uid{color:#7e8795;font-size:10px;direction:ltr;display:inline-block}.muted{color:#7f8999}.comment{padding:7px 2px;border-bottom:1px solid #202631;font-size:12px}.comment:last-child{border-bottom:0}.comment-text{margin-top:3px;white-space:pre-wrap;overflow-wrap:anywhere}
.gift-name{font-weight:800}.coins{color:#f2c66d}.pk-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;padding:9px}.pk-box{background:#151922;border-radius:10px;padding:9px}.pk-box b{display:block;margin-bottom:4px}.remove{margin-top:12px;width:100%;background:#222731;color:#ff6e86}.error{margin-top:10px;padding:9px;border-radius:9px;background:#3a1820;color:#ff9caf;font-size:11px;overflow-wrap:anywhere}.online-dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:#59e78b;margin-left:5px}.offline-dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:#737b89;margin-left:5px}
.archive-section{margin-top:20px;background:#12151c;border:1px solid #282e3a;border-radius:16px;padding:14px;box-shadow:0 8px 28px rgba(0,0,0,.16)}
.archive-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px}
.archive-title{font-size:17px;font-weight:800}
.archive-refresh{background:#222731;color:#fff;min-height:38px;padding:0 13px}
.archive-card{background:#0d1016;border:1px solid #202631;border-radius:12px;margin-top:9px;overflow:hidden}
.archive-card-head{padding:10px 11px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.archive-name{font-weight:800;font-size:13px;word-break:break-all}
.archive-date{color:#818b9c;font-size:10px;margin-top:3px}
.archive-files{display:flex;flex-wrap:wrap;gap:7px;padding:9px 11px;border-top:1px solid #202631}
.archive-file{display:inline-flex;align-items:center;text-decoration:none;background:#151922;border:1px solid #303745;color:#f5f7fb;border-radius:9px;padding:8px 10px;font-size:11px}
.archive-empty{color:#818b9c;padding:18px 5px;text-align:center}

@media(min-width:700px){.stats{grid-template-columns:repeat(6,1fr)}}
@media(max-width:430px){.add{flex-direction:column}.add button{width:100%}.card{padding:12px}.stats{gap:6px}.stat{padding:9px 3px}.value{font-size:14px}.user{font-size:16px}}
</style>
</head>
<body>
<header><div class="header-inner"><div><h1>📡 TikTok LIVE Monitor</h1><div class="sub">مراقبة متعددة للبثوث — بيانات مباشرة من TikTokLive</div></div></div></header>
<div class="container">
  <div class="add">
    <input id="username" placeholder="أدخل اسم حساب TikTok بدون @" autocomplete="off" spellcheck="false">
    <button onclick="addUser()">＋ إضافة حساب</button>
  </div>
  <div id="streams"><div class="empty">لا توجد حسابات مضافة</div></div>
<div class="archive-section">
  <div class="archive-head">
    <div>
      <div class="archive-title">📚 سجل جلسات LIVE</div>
      <div class="sub">الأرشيف الكامل للجلسات السابقة</div>
    </div>
    <button class="archive-refresh" onclick="loadArchives()">🔄 تحديث</button>
  </div>
  <div id="archives"><div class="archive-empty">جارٍ تحميل الأرشيف...</div></div>
</div>
</div>
<script>
function escapeHtml(text){return String(text??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"})[m]);}
function fmt(n){n=Number(n||0);return n.toLocaleString("en-US");}
function escAttr(text){return escapeHtml(text).replace(/`/g,"&#096;");}
async function addUser(){
  const input=document.getElementById("username");
  const username=input.value.trim().replace(/^@/,"");
  if(!username)return;
  try{
    const r=await fetch("/api/streams",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username})});
    const d=await r.json();
    if(!r.ok){alert(d.error||"تعذر إضافة الحساب");return;}
    input.value="";loadStreams();
  }catch(e){alert("تعذر الاتصال بالخادم");}
}
async function startMonitoring(username){
    try{
        const r=await fetch("/api/streams/"+encodeURIComponent(username)+"/start",{method:"POST"});
        const d=await r.json();
        if(!r.ok){alert(d.error||"تعذر بدء المراقبة");return;}
        loadStreams();
    }catch(e){
        alert("تعذر الاتصال بالخادم");
    }
}

async function stopMonitoring(username){
    if(!confirm("إيقاف مراقبة @"+username+"؟"))return;
    try{
        const r=await fetch("/api/streams/"+encodeURIComponent(username)+"/stop",{method:"POST"});
        const d=await r.json();
        if(!r.ok){alert(d.error||"تعذر إيقاف المراقبة");return;}
        loadStreams();
    }catch(e){
        alert("تعذر الاتصال بالخادم");
    }
}

async function removeUser(username){
  if(!confirm("حذف @"+username+" من المراقبة؟"))return;
  try{
    const r=await fetch("/api/streams/"+encodeURIComponent(username),{method:"DELETE"});
    const d=await r.json();if(!r.ok){alert(d.error||"تعذر حذف الحساب");return;}loadStreams();
  }catch(e){alert("تعذر الاتصال بالخادم");}
}
function emptyRows(text){return '<div class="muted" style="padding:8px 2px">'+escapeHtml(text)+'</div>';}
function renderStreams(data){
  const box=document.getElementById("streams");const users=Object.values(data||{});
  if(!users.length){box.innerHTML='<div class="empty">لا توجد حسابات مضافة</div>';return;}
  const oldScroll={};
  document.querySelectorAll(".comments").forEach(el=>{oldScroll[el.dataset.username]={top:el.scrollTop,height:el.scrollHeight,client:el.clientHeight};});
  box.innerHTML=users.map(stream=>{
    const status=stream.monitoring_enabled
      ? (stream.live
          ? '<span class="online-dot"></span>🟢 LIVE'
          : '<span class="offline-dot"></span>⏳ بانتظار LIVE')
      : '<span class="offline-dot"></span>⏹️ المراقبة متوقفة';
    const viewers=stream.viewer_names||{};const names=Object.values(viewers);
    const viewerRows=names.slice(-120).reverse().map(v=>'<div class="row"><span class="nick">'+escapeHtml(v.nickname||"مستخدم")+'</span> '+(v.unique_id?'<span class="uid">@'+escapeHtml(v.unique_id)+'</span>':'')+'</div>').join("");
    const joins=(stream.recent_joins||[]).slice().reverse().map(v=>'<div class="row">👤 <span class="nick">'+escapeHtml(v.nickname||"مستخدم")+'</span> '+(v.unique_id?'<span class="uid">@'+escapeHtml(v.unique_id)+'</span>':'')+'</div>').join("");
    const comments=(stream.comments||[]).map(c=>'<div class="comment"><span class="nick">'+escapeHtml(c.user||"مستخدم")+'</span>'+(c.unique_id?' <span class="uid">@'+escapeHtml(c.unique_id)+'</span>':'')+'<div class="comment-text">'+escapeHtml(c.text||"")+'</div></div>').join("");
    const follows=(stream.follow_events||[]).slice().reverse().map(v=>'<div class="row">➕ <span class="nick">'+escapeHtml(v.user||"مستخدم")+'</span> تابع البث</div>').join("");
const shares=(stream.share_events||[]).slice().reverse().map(v=>'<div class="row">🔄 <span class="nick">'+escapeHtml(v.user||"مستخدم")+'</span> شارك البث</div>').join("");
const supporters=(stream.supporters_sorted||[]).slice(0,10).map((x,i)=>'<div class="row">🏆 <b>#'+(i+1)+'</b> <span class="nick">'+escapeHtml(x.user||"مستخدم")+'</span> — '+fmt(x.gifts)+' هدية · <span class="coins">'+fmt(x.coins)+' coins</span></div>').join("");

const gifts=(stream.gift_events||[]).slice().reverse().map(g=>'<div class="row">🎁 <span class="nick">'+escapeHtml(g.user||"مستخدم")+'</span> أرسل <span class="gift-name">'+escapeHtml(g.name||"هدية")+'</span> ×'+fmt(g.repeat_count)+' '+(g.coins?'<span class="coins">('+fmt(g.coins)+' coins)</span>':'')+'</div>').join("");
    const pk=stream.pk||{};const armies=(pk.armies||[]).map(a=>'<div class="pk-box"><b>'+escapeHtml(a.name||"مشارك")+'</b><span>'+fmt(a.score)+' نقطة</span></div>').join("");
    return '<div class="card">'+
      '<div class="card-top"><div><div class="user">@'+escapeHtml(stream.username)+'</div>'+(stream.room_id?'<div class="room">Room ID: '+escapeHtml(stream.room_id)+'</div>':'')+'</div><div class="status '+(stream.live?'live':'offline')+'">'+status+'</div></div>'+ 
      '<div class="stats">'+
      stat('👥',fmt(stream.viewers),'المتواجدون الآن')+
      stat('❤️',fmt(stream.likes),'الإعجابات')+
      stat('🎁',fmt(stream.gifts),'الهدايا')+
      stat('💰',fmt(stream.gift_coins),'قيمة الهدايا')+
      stat('💬',fmt(stream.comment_count),'التعليقات')+
      stat('➕',fmt(stream.follows),'المتابعات')+
      '</div>'+ 
      '<div class="stats" style="grid-template-columns:1fr 1fr;margin-top:8px">'+stat('🔄',fmt(stream.shares),'المشاركات')+stat('📥',fmt((stream.recent_joins||[]).length),'آخر الداخلين')+'</div>'+ 
      '<div class="section"><div class="section-title">👥 المتواجدون الآن <span class="hint">العدد: '+fmt(stream.viewers)+'</span></div><div class="scroll viewer-list">'+(viewerRows||emptyRows(stream.viewers>0?'أسماء snapshot الأخيرة التي يرسلها TikTok؛ العدد هو العدد الحالي الفعلي.':'لا يوجد متواجدون حاليًا حسب آخر تحديث'))+'</div></div>'+ 
      '<div class="section"><div class="section-title">📥 آخر من دخلوا</div><div class="scroll joins">'+(joins||emptyRows('لم يُرصد دخول جديد بعد'))+'</div></div>'+ 
      '<div class="section"><div class="section-title">➕ آخر المتابعات</div><div class="scroll follows">'+(follows||emptyRows('لم تُرصد متابعة بعد'))+'</div></div>'+
'<div class="section"><div class="section-title">🔄 آخر المشاركات</div><div class="scroll shares">'+(shares||emptyRows('لم تُرصد مشاركة بعد'))+'</div></div>'+
'<div class="section"><div class="section-title">🏆 Top Supporters <span class="hint">أعلى الداعمين حسب العملات</span></div><div class="list">'+(supporters||'<div class="empty">لا توجد هدايا حتى الآن</div>')+'</div></div><div class="section"><div class="section-title">🎁 آخر الهدايا <span class="hint">القيمة المتاحة من الحدث</span></div><div class="scroll gifts">'+(gifts||emptyRows('لا توجد هدايا بعد'))+'</div></div>'+ 
      '<div class="section"><div class="section-title">💬 التعليقات</div><div class="scroll comments" data-username="'+escAttr(stream.username)+'">'+(comments||emptyRows('لا توجد تعليقات بعد'))+'</div></div>'+ 
      '<div class="section"><div class="section-title">⚔️ PK / Battle <span class="hint">'+(pk.active?'نشط':'غير نشط')+'</span></div>'+(pk.active?'<div class="pk-grid">'+(armies||'<div class="pk-box">تم رصد PK بدون بيانات نقاط قابلة للعرض بعد.</div>')+'</div>':'<div class="scroll">'+emptyRows('لا يوجد PK نشط حاليًا')+'</div>')+'</div>'+ 
      (stream.error?'<div class="error">⚠️ '+escapeHtml(stream.error)+'</div>':'')+
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">'+
      (stream.monitoring_enabled
        ? '<button class="remove" style="flex:1" onclick="stopMonitoring(\''+String(stream.username).replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\')">⏹️ إيقاف المراقبة</button>'
        : '<button class="remove" style="flex:1" onclick="startMonitoring(\''+String(stream.username).replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\')">▶️ بدء المراقبة</button>')+
      '<button class="remove" style="flex:1" onclick="removeUser(\''+String(stream.username).replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\')">حذف الحساب</button>'+
      '</div>'+
      '</div>';
  }).join("");
  document.querySelectorAll(".comments").forEach(el=>{const old=oldScroll[el.dataset.username];if(!old){el.scrollTop=el.scrollHeight;return;}const near=old.top+old.client>=old.height-40;el.scrollTop=near?el.scrollHeight:old.top;});
}
function stat(icon,value,label){return '<div class="stat"><div class="icon">'+icon+'</div><div class="value">'+value+'</div><div class="label">'+label+'</div></div>';}
async function deleteArchive(folder){
  if(!confirm('هل أنت متأكد من حذف أرشيف الجلسة المحددة؟ لا يمكن التراجع عن الحذف.'))return;
  try{
    const r=await fetch('/api/archive-delete',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({folder})
    });
    const d=await r.json();
    if(!r.ok){alert(d.error||'تعذر حذف الأرشيف');return;}
    await loadArchives();
    alert('تم حذف أرشيف الجلسة المحددة.');
  }catch(e){alert('تعذر الاتصال بالخادم؛ لم يتم تأكيد الحذف.');}
}
async function loadArchives(){
  const box=document.getElementById("archives");
  try{
    const r=await fetch("/api/archives",{cache:"no-store"});
    const d=await r.json();
    if(!r.ok){box.innerHTML='<div class="archive-empty">تعذر تحميل سجل الأرشيف</div>';return;}
    if(!Array.isArray(d)||!d.length){
      box.innerHTML='<div class="archive-empty">لا توجد جلسات مؤرشفة حتى الآن</div>';
      return;
    }
    box.innerHTML=d.map(a=>{
      const files=(a.files||[]).map(f=>{
        const labels={
          comments:"💬 التعليقات",
          gifts:"🎁 الهدايا",
          follows:"➕ المتابعات",
          joins:"📥 الدخول",
          video:"🎥 تنزيل الفيديو"
        };
        if(f.type==="video"){
          const videoName=(a.account_name||a.username)+"_live.mp4";
          return '<button type="button" class="archive-file" data-download-path="/api/archive-file?path='+escapeHtml(encodeURIComponent(f.path))+'" data-download-name="'+escapeHtml(videoName)+'">'+labels.video+'</button>';
        }
        const downloadName=(a.account_name||a.username)+"_"+(f.type||"file")+".txt";
        return '<button type="button" class="archive-file" data-download-path="/api/archive-file?path='+escapeHtml(encodeURIComponent(f.path))+'" data-download-name="'+escapeHtml(downloadName)+'">'+escapeHtml(labels[f.type]||f.type)+'</button>';
      }).join("");
      const zipButton='<button type="button" class="archive-file" data-download-path="/api/archive-zip?folder='+escapeHtml(encodeURIComponent(a.folder))+'" data-download-name="'+escapeHtml(a.folder+'.zip')+'" style="background:#18352c;border-color:#28664f">📦 تحميل الجلسة كاملة ZIP</button>';
      const deleteButton='<button type="button" class="archive-file" data-delete-folder="'+escapeHtml(a.folder)+'" style="background:#3a1820;border-color:#71303d;color:#ff9caf">🗑️ حذف الأرشيف</button>';
      const videoFile=(a.files||[]).find(f=>f.type==="video");
      const videoPlayer=videoFile
        ? '<div style="padding:0 11px 12px"><video controls playsinline preload="metadata" style="width:100%;max-height:360px;background:#000;border-radius:10px" src="/api/archive-video?path='+escapeHtml(encodeURIComponent(videoFile.path))+'"></video></div>'
        : '';
      return '<div class="archive-card"><div class="archive-card-head"><div><div class="archive-name">📁 @'+escapeHtml(a.username)+'</div><div class="archive-date">'+escapeHtml(a.folder)+'</div></div></div><div class="archive-files">'+zipButton+deleteButton+(files||'<span class="muted">لا توجد ملفات</span>')+'</div>'+videoPlayer+'</div>';
    }).join("");
  }catch(e){
    box.innerHTML='<div class="archive-empty">تعذر الاتصال بالخادم</div>';
  }
}

function downloadArchive(path,name){
  if(!path || !path.startsWith('/api/archive-')){alert('مسار التنزيل غير صالح');return;}
  if(window.Android && typeof window.Android.download === 'function'){
    window.Android.download(path,name||'TikTokArchive');
  }else{
    window.location.href=path;
  }
}
document.addEventListener('click',function(event){
  const downloadButton=event.target.closest('[data-download-path]');
  if(downloadButton){
    event.preventDefault();
    downloadArchive(downloadButton.dataset.downloadPath,downloadButton.dataset.downloadName);
    return;
  }
  const deleteButton=event.target.closest('[data-delete-folder]');
  if(deleteButton){
    event.preventDefault();
    deleteArchive(deleteButton.dataset.deleteFolder);
  }
});
async function loadStreams(){try{const r=await fetch('/api/streams',{cache:'no-store'});const d=await r.json();if(r.ok)renderStreams(d);}catch(e){}}
document.getElementById('username').addEventListener('keydown',e=>{if(e.key==='Enter')addUser();});
loadStreams();loadArchives();setInterval(loadStreams,1000);setInterval(loadArchives,5000);
</script>
</body>
</html>'''


def add_recent(stream, key, item, limit):
    arr = stream[key]
    arr.append(item)
    if len(arr) > limit:
        del arr[:-limit]


def start_archive_session(stream):
    """Create a separate archive workspace for this LIVE session."""
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d_%H-%M-%S")

    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

    username = stream["username"]
    session_dir = ARCHIVE_ROOT / f"{username}_{stamp}"
    session_dir.mkdir(parents=True, exist_ok=True)

    comments_file = session_dir / "comments.txt"
    gifts_file = session_dir / "gifts.txt"
    follows_file = session_dir / "follows.txt"
    joins_file = session_dir / "joins.txt"
    viewers_file = session_dir / "viewers_timeline.jsonl"

    comments_file.write_text(
        f"TikTok LIVE Archive\\n"
        f"Account: @{username}\\n"
        f"Start: {now.strftime('%Y-%m-%d %H:%M:%S')}\\n"
        f"End: جاري...\\n"
        f"{'=' * 60}\\n\\n",
        encoding="utf-8",
    )

    gifts_file.write_text(
        f"TikTok LIVE Gifts Archive\\n"
        f"Account: @{username}\\n"
        f"Start: {now.strftime('%Y-%m-%d %H:%M:%S')}\\n"
        f"End: جاري...\\n"
        f"{'=' * 60}\\n\\n",
        encoding="utf-8",
    )

    follows_file.write_text(
        f"TikTok LIVE Follows Archive\\n"
        f"Account: @{username}\\n"
        f"Start: {now.strftime('%Y-%m-%d %H:%M:%S')}\\n"
        f"End: جاري...\\n"
        f"{'=' * 60}\\n\\n",
        encoding="utf-8",
    )

    joins_file.write_text(
        f"TikTok LIVE Joins Archive\\n"
        f"Account: @{username}\\n"
        f"Start: {now.strftime('%Y-%m-%d %H:%M:%S')}\\n"
        f"End: جاري...\\n"
        f"{'=' * 60}\\n\\n",
        encoding="utf-8",
    )

    viewers_file.write_text(
        f"TikTok named viewer snapshots\n"
        f"Account: @{username}\n"
        f"Start: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"End: جاري...\n", encoding="utf-8"
    )

    stream["session_started_at"] = time.time()
    stream["session_ended_at"] = None
    stream["session_dir"] = str(session_dir)
    stream["session_files"] = {
        "comments": str(comments_file),
        "gifts": str(gifts_file),
        "follows": str(follows_file),
        "joins": str(joins_file),
        "viewers": str(viewers_file),
    }

    print(f"📁 Archive session created: {session_dir}")


def finish_archive_session(stream):
    """Finalize the current LIVE archive and rename its folder with the end time."""
    session_dir_value = stream.get("session_dir")
    session_started = stream.get("session_started_at")

    if not session_dir_value or not session_started:
        return

    session_dir = FilePath(session_dir_value)

    # Prevent double-finalization.
    if stream.get("session_ended_at"):
        return

    ended_at = time.time()
    end_dt = datetime.fromtimestamp(ended_at)
    start_dt = datetime.fromtimestamp(session_started)


    end_text = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    # Update the End line in all archive files.
    for file_path in stream.get("session_files", {}).values():
        try:
            path = FilePath(file_path)
            if path.exists():
                content = path.read_text(encoding="utf-8")
                content = "\n".join((f"End: {end_text}" if line.startswith("End:") else line) for line in content.split("\n"))
                path.write_text(content, encoding="utf-8")
        except Exception as archive_error:
            print(f"⚠️ Archive finalization error: {archive_error}")

    # Rename the session folder:
    # username_YYYY-MM-DD_HH-MM-SS_to_YYYY-MM-DD_HH-MM-SS
    archive_finalized = False
    if session_dir.exists():
        start_stamp = start_dt.strftime("%Y-%m-%d_%H-%M-%S")
        end_stamp = end_dt.strftime("%Y-%m-%d_%H-%M-%S")
        final_dir = session_dir.parent / f"{stream['username']}_{start_stamp}_to_{end_stamp}"

        try:
            if final_dir.exists():
                final_dir = session_dir.parent / (
                    f"{stream['username']}_{start_stamp}_to_{end_stamp}_"
                    f"{int(ended_at)}"
                )

            session_dir.rename(final_dir)
            stream["session_ended_at"] = ended_at
            archive_finalized = True

            # Update all stored paths after the rename.
            stream["session_dir"] = str(final_dir)
            stream["session_files"] = {
                "comments": str(final_dir / "comments.txt"),
                "gifts": str(final_dir / "gifts.txt"),
                "follows": str(final_dir / "follows.txt"),
                "joins": str(final_dir / "joins.txt"),
            "viewers": str(final_dir / "viewers_timeline.jsonl"),
            }

            # Keep the video path aligned with the renamed archive folder.
            video_file = stream.get("video_file")
            if video_file:
                try:
                    relative_video = FilePath(video_file).relative_to(session_dir)
                    stream["video_file"] = str(final_dir / relative_video)
                except ValueError:
                    pass

            print(f"📦 Archive finalized: {final_dir}")

        except Exception as archive_error:
            print(f"⚠️ Archive rename error: {archive_error}")

    # Clear session markers only after a successful folder rename.
    if archive_finalized:
        stream["session_started_at"] = None
        stream["session_ended_at"] = None



def start_video_recording(stream, client):
    """Start one MP4 recording for the currently active archive session."""
    try:
        if not stream.get("monitoring_enabled", False):
            return False

        if stream.get("video_recording"):
            return True

        session_dir_value = stream.get("session_dir")
        if not session_dir_value:
            print(f"⚠️ No archive session for video recording: @{stream['username']}")
            return False

        room_info = client.room_info or {}
        if not room_info.get("stream_url"):
            print(
                f"⚠️ No stream_url available for video recording: "
                f"@{stream['username']}"
            )
            return False

        video_path = FilePath(session_dir_value) / "live.mp4"

        route = client._web.fetch_video_data

        # TikTok does not guarantee that every LIVE exposes every
        # quality key. Select the highest quality actually available.
        stream_url = room_info.get("stream_url") or {}
        core_sdk = stream_url.get("live_core_sdk_data") or {}
        pull_data = core_sdk.get("pull_data") or {}
        raw_stream_data = pull_data.get("stream_data")

        if not raw_stream_data:
            raise RuntimeError("stream_data is unavailable")

        stream_data = json.loads(raw_stream_data)
        quality_data = stream_data.get("data") or {}

        print(f"🔎 Available video qualities for @{stream['username']}:")
        for quality_name, quality_item in quality_data.items():
            main_data = (quality_item or {}).get("main") or {}
            formats = list(main_data.keys())
            print(f"   • {quality_name}: formats={formats}")

        quality_order = [
            VideoFetchQuality.ORIGIN,
            VideoFetchQuality.UHD,
            VideoFetchQuality.HD,
            VideoFetchQuality.SD,
            VideoFetchQuality.LD,
        ]

        selected_quality = None

        for quality in quality_order:
            if quality.value in quality_data:
                selected_quality = quality
                break

        if selected_quality is None:
            available = list(quality_data.keys())
            raise RuntimeError(
                f"No supported video quality found. Available: {available}"
            )

        from datetime import datetime
        from pathlib import Path

        quality_log = Path("LIVE_History/quality_diagnostics.log")
        quality_log.parent.mkdir(parents=True, exist_ok=True)

        diagnostic_lines = [
            "",
            f"[{datetime.now().astimezone().isoformat(timespec='seconds')}]",
            f"Account: @{stream['username']}",
            f"Available qualities: {list(quality_data.keys())}",
        ]
        for quality_name, quality_item in quality_data.items():
            main_data = (quality_item or {}).get("main") or {}
            diagnostic_lines.append(
                f"  {quality_name}: formats={list(main_data.keys())}"
            )
        diagnostic_lines.append(
            f"Selected quality: {selected_quality.value}"
        )

        with quality_log.open("a", encoding="utf-8") as log:
            log.write("\n".join(diagnostic_lines) + "\n")

        print(
            f"🎥 Selected video quality for @{stream['username']}: "
            f"{selected_quality.value}"
        )

        route.start(
            output_fp=video_path,
            room_info=room_info,
            record_for=-1,
            quality=selected_quality,
            record_format=VideoFetchFormat.FLV,
            output_format="mp4",
        )

        stream["video_file"] = str(video_path)
        stream["video_recording"] = True

        print(f"🎥 Video recording STARTED: {video_path}")
        return True

    except Exception as e:
        stream["video_file"] = None
        stream["video_recording"] = False
        print(
            f"⚠️ Video recording start failed for "
            f"@{stream['username']}: {type(e).__name__}: {e}"
        )
        return False



def stop_video_recording(stream, client=None):
    """Stop the matching FFmpeg process and validate the video."""
    import os
    import signal
    import subprocess
    import time

    video_file = stream.get("video_file")
    if not video_file:
        print(f"No video path recorded for @{stream.get('username', '?')}")
        return False

    path = FilePath(video_file)
    target_path = os.path.abspath(str(path))
    username = stream.get("username", "?")
    route = None
    ffmpeg_obj = None
    recording_thread = None
    process = None

    if client is not None:
        try:
            route = client._web.fetch_video_data
            ffmpeg_obj = getattr(route, "_ffmpeg", None)
            recording_thread = getattr(route, "_thread", None)
            process = getattr(ffmpeg_obj, "process", None) if ffmpeg_obj else None
        except Exception as e:
            print(f"FFmpeg handle lookup warning: {e}")

    def matches(pid):
        try:
            raw = FilePath(f"/proc/{pid}/cmdline").read_bytes()
            args = [os.fsdecode(x) for x in raw.split(bytes([0]) ) if x]
            return (
                bool(args)
                and "ffmpeg" in os.path.basename(args[0]).lower()
                and any(
                    arg == target_path
                    or (arg.startswith("/") and os.path.abspath(arg) == target_path)
                    for arg in args[1:]
                )
            )
        except (OSError, PermissionError):
            return False

    def find_pids():
        found = []
        try:
            for entry in os.listdir("/proc"):
                if entry.isdigit() and matches(entry):
                    found.append(int(entry))
        except OSError:
            pass
        return found

    pids = set(find_pids())
    if process is not None:
        try:
            if process.poll() is None and matches(process.pid):
                pids.add(int(process.pid))
        except Exception:
            pass

    def alive(pid):
        if process is not None:
            try:
                if int(process.pid) == pid:
                    return process.poll() is None
            except Exception:
                pass
        return matches(pid)

    for pid in list(pids):
        try:
            if matches(pid):
                os.kill(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and any(alive(p) for p in pids):
        time.sleep(0.25)

    remaining = [p for p in pids if alive(p)]
    for pid in remaining:
        try:
            if matches(pid):
                os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and any(alive(p) for p in remaining):
        time.sleep(0.25)

    for pid in [p for p in remaining if alive(p)]:
        try:
            if matches(pid):
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    if process is not None:
        try:
            process.wait(timeout=5)
        except Exception:
            pass

    if recording_thread is not None:
        try:
            recording_thread.join(timeout=20)
            if recording_thread.is_alive():
                print(f"Recording thread still active for @{username}")
                return False
        except Exception as e:
            print(f"Recording thread warning: {e}")

    if find_pids():
        print(f"FFmpeg still writing @{username}; archive preserved")
        return False

    if route is not None:
        try:
            if getattr(route, "_ffmpeg", None) is ffmpeg_obj:
                route._ffmpeg = None
            if getattr(route, "_thread", None) is recording_thread:
                route._thread = None
        except Exception:
            pass

    if not path.is_file() or path.stat().st_size == 0:
        print(f"Video file missing or empty: {path}")
        return False

    # Preserve the original recording without lossy post-recording compression.
    print(f"Skipping post-recording compression; preserving original: {path}")

    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True, text=True, timeout=90
        )
    except Exception as e:
        print(f"Video validation error: {type(e).__name__}: {e}")
        return False

    if result.returncode != 0:
        print(f"Video validation failed: {path}")
        print((result.stderr or "FFmpeg could not read the file")[-1500:])
        return False

    stream["video_recording"] = False
    print(f"Video validation passed ({path.stat().st_size} bytes): {path}")
    return True

def create_client(username):
    client = TikTokLiveClient(unique_id=username)

    @client.on(ConnectEvent)
    async def on_connect(event):
        with lock:
            if username not in streams or not streams[username].get("monitoring_enabled", False):
                return
            streams[username]["live"] = True
            streams[username]["room_id"] = client.room_id
            streams[username]["error"] = None

            room_info = client.room_info or {}
            owner_info = room_info.get("owner") or {}
            owner_nickname = safe_text(
                owner_info.get("nickname"),
                username,
            )
            streams[username]["owner_nickname"] = owner_nickname

            streams[username]["started_at"] = time.time()
            streams[username]["last_activity"] = time.time()

            # Start an archive only when there is no active session.
            # A reconnect during the same LIVE must keep the existing archive.
            if not streams[username].get("session_started_at"):
                start_archive_session(streams[username])
            else:
                print(
                    f"📂 Continuing existing archive session for @{username}"
                )

            # Start video only when manual monitoring is enabled.
            # The recording belongs to this monitoring/LIVE session.
            start_video_recording(streams[username], client)

        print(f"\n🟢 LIVE: @{username}")
        print(f"   Room ID: {client.room_id}")

    @client.on(CommentEvent)
    async def on_comment(event):
        info = user_info(getattr(event, "user", None))
        text_value = safe_text(getattr(event, "comment", None))
        with lock:
            if username not in streams or not streams[username].get("monitoring_enabled", False):
                return
            streams[username]["comment_count"] += 1
            add_recent(streams[username], "comments", {
                "user": info["nickname"],
                "unique_id": info["unique_id"],
                "text": text_value,
            }, MAX_COMMENTS)

            # Save EVERY comment to the current LIVE session archive.
            session_files = streams[username].get("session_files", {})
            comments_path = session_files.get("comments")
            if comments_path:
                try:
                    now_text = datetime.now().strftime("%H:%M:%S")
                    with open(comments_path, "a", encoding="utf-8") as f:
                        f.write(
                            f"[{now_text}] "
                            f"{info['nickname']} "
                            f"(@{info['unique_id'] or 'unknown'}): "
                            f"{text_value}\n"
                        )
                        f.flush()
                except Exception as archive_error:
                    print(f"⚠️ Comment archive error: {archive_error}")

            streams[username]["last_activity"] = time.time()
        print(f"💬 @{username} | {info['nickname']}: {text_value}")

    @client.on(GiftEvent)
    async def on_gift(event):
        info = user_info(getattr(event, "user", None))
        gift = getattr(event, "gift", None)

        gift_name = safe_text(getattr(gift, "name", None), "هدية")
        gift_id = safe_text(getattr(event, "gift_id", None), gift_name)
        group_id = safe_text(getattr(event, "group_id", None), "")

        repeat = safe_int(getattr(event, "repeat_count", None), 1)
        if repeat < 1:
            repeat = 1

        streaking = bool(getattr(event, "streaking", False))
        repeat_end = bool(getattr(event, "repeat_end", False))

        # TikTokLive sends the same gift streak in two stages:
        #   1) streaking=True, repeat_end=False
        #   2) streaking=False, repeat_end=True
        #
        # Only the final event is counted. This prevents the same gift
        # from being counted twice while preserving the final repeat count.
        diamond_count = safe_int(getattr(gift, "diamond_count", None), 0)

        print(
            "🔎 GIFT DEBUG:",
            "gift_id=", getattr(event, "gift_id", None),
            "group_id=", getattr(event, "group_id", None),
            "order_id=", getattr(event, "order_id", None),
            "repeat_count=", getattr(event, "repeat_count", None),
            "streaking=", getattr(event, "streaking", None),
            "repeat_end=", getattr(event, "repeat_end", None),
            "diamond_count=", diamond_count,
        )

        key = (
            info["unique_id"] or info["nickname"],
            gift_id,
            group_id,
        )

        with lock:
            if username not in streams or not streams[username].get("monitoring_enabled", False):
                return

            stream = streams[username]

            # Ignore the opening streak event.
            # The final event contains the definitive repeat_count.
            if streaking and not repeat_end:
                stream["gift_combos"][key] = repeat
                stream["last_activity"] = time.time()
                return

            # Final event: count the complete streak exactly once.
            previous = safe_int(stream["gift_combos"].pop(key, 0), 0)

            quantity = repeat

            # Safety fallback for unusual providers/events where the final
            # event repeats the already-recorded quantity.
            if previous and quantity < previous:
                quantity = previous

            coins = diamond_count * quantity

            stream["gifts"] += quantity
            stream["gift_coins"] += coins

            # Save finalized gift to the current LIVE session archive.
            gifts_path = stream.get("session_files", {}).get("gifts")
            if gifts_path:
                try:
                    now_text = datetime.now().strftime("%H:%M:%S")
                    with open(gifts_path, "a", encoding="utf-8") as f:
                        f.write(
                            f"[{now_text}] "
                            f"{info['nickname']} "
                            f"(@{info['unique_id'] or 'unknown'}) -> "
                            f"{gift_name} x{quantity} | "
                            f"{coins} coins"
                            "\n"
                        )
                        f.flush()
                except Exception as archive_error:
                    print(f"⚠️ Gift archive error: {archive_error}")

            add_recent(stream, "gift_events", {
                "user": info["nickname"],
                "unique_id": info["unique_id"],
                "name": gift_name,
                "repeat_count": quantity,
                "coins": coins,
            }, MAX_GIFTS)

            # Top Supporters: aggregate only the final, counted gift event.
            supporter_key = info["unique_id"] or info["nickname"] or "unknown"
            supporter = stream["supporters"].setdefault(
                supporter_key,
                {
                    "unique_id": info["unique_id"],
                    "user": info["nickname"],
                    "gifts": 0,
                    "coins": 0,
                },
            )
            supporter["user"] = info["nickname"] or supporter["user"]
            supporter["gifts"] += quantity
            supporter["coins"] += coins

            stream["supporters_sorted"] = sorted(
                stream["supporters"].values(),
                key=lambda x: (x.get("coins", 0), x.get("gifts", 0)),
                reverse=True,
            )[:50]

            stream["last_activity"] = time.time()

            if len(stream["gift_combos"]) > 200:
                stream["gift_combos"] = dict(
                    list(stream["gift_combos"].items())[-100:]
                )

        suffix = f" ({coins} coins)" if coins else ""
        print(
            f"🎁 @{username} | {info['nickname']} "
            f"sent {gift_name} x{quantity}{suffix}"
        )

    @client.on(LikeEvent)
    async def on_like(event):
        count = safe_int(getattr(event, "like_count", None), 1)
        if count < 0:
            count = 0
        with lock:
            if username not in streams or not streams[username].get("monitoring_enabled", False):
                return
            streams[username]["likes"] += count
            streams[username]["last_activity"] = time.time()
        info = user_info(getattr(event, "user", None))
        print(f"❤️ @{username} | {info['nickname']} liked x{count}")

    @client.on(FollowEvent)
    async def on_follow(event):
        info = user_info(getattr(event, "user", None))
        with lock:
            if username not in streams or not streams[username].get("monitoring_enabled", False):
                return
            stream = streams[username]
            stream["follows"] += 1

            # Save follow event to the current LIVE session archive.
            follows_path = stream.get("session_files", {}).get("follows")
            if follows_path:
                try:
                    now_text = datetime.now().strftime("%H:%M:%S")
                    with open(follows_path, "a", encoding="utf-8") as f:
                        f.write(
                            f"[{now_text}] "
                            f"{info['nickname']} "
                            f"(@{info['unique_id'] or 'unknown'})"
                            "\n"
                        )
                        f.flush()
                except Exception as archive_error:
                    print(f"⚠️ Follow archive error: {archive_error}")

            add_recent(stream, "follow_events", {
                "user": info["nickname"],
                "unique_id": info["unique_id"],
            }, 100)
            stream["last_activity"] = time.time()

        print(f"➕ @{username} | {info['nickname']} followed")

    @client.on(ShareEvent)
    async def on_share(event):
        info = user_info(getattr(event, "user", None))
        with lock:
            if username not in streams or not streams[username].get("monitoring_enabled", False):
                return
            stream = streams[username]
            stream["shares"] += 1
            add_recent(stream, "share_events", {
                "user": info["nickname"],
                "unique_id": info["unique_id"],
            }, 100)
            stream["last_activity"] = time.time()

        print(f"🔄 @{username} | {info['nickname']} shared")

    @client.on(JoinEvent)
    async def on_join(event):
        info = user_info(getattr(event, "user", None))
        member_count = safe_int(getattr(event, "member_count", None), 0)
        joined = {
            "nickname": info["nickname"],
            "unique_id": info["unique_id"],
            "member_count": member_count,
        }
        with lock:
            if username not in streams or not streams[username].get("monitoring_enabled", False):
                return
            streams[username]["viewers"] = member_count if member_count >= 0 else streams[username]["viewers"]
            streams[username]["viewer_names"].pop(info["unique_id"] or info["nickname"], None)
            streams[username]["viewer_names"][info["unique_id"] or info["nickname"]] = {
                "nickname": info["nickname"],
                "unique_id": info["unique_id"],
            }
            while len(streams[username]["viewer_names"]) > MAX_VIEWER_NAMES:
                streams[username]["viewer_names"].popitem(last=False)
            add_recent(streams[username], "recent_joins", joined, MAX_JOINS)

            # Save every detected JOIN to the current LIVE session archive.
            joins_path = streams[username].get("session_files", {}).get("joins")
            if joins_path:
                try:
                    now_text = datetime.now().strftime("%H:%M:%S")
                    with open(joins_path, "a", encoding="utf-8") as f:
                        f.write(
                            f"[{now_text}] "
                            f"{info['nickname']} "
                            f"(@{info['unique_id'] or 'unknown'})"
                            "\n"
                        )
                        f.flush()
                except Exception as archive_error:
                    print(f"⚠️ Join archive error: {archive_error}")

            streams[username]["last_activity"] = time.time()
        print("👤 JOIN")
        print("=" * 50)
        print(f"Nickname: {info['nickname']}")
        print(f"Unique ID: {info['unique_id']}")
        print(f"Member Count: {member_count}")
        print("Action: JOINED")

    @client.on(RoomUserSeqEvent)
    async def on_viewers(event):
        total = safe_int(getattr(event, "total", None), 0)

        # RoomUserSeqEvent gives the authoritative current count. Its ranks/seats
        # are the latest named snapshot TikTok exposes; they are NOT the complete
        # viewer roster. Replace the named snapshot instead of accumulating joins.
        snapshot = OrderedDict()

        def add_snapshot_user(obj):
            user = getattr(obj, "user", None)
            info = user_info(user)
            if info["nickname"] == "مستخدم" and not info["unique_id"]:
                # Some versions expose nickname/unique_id directly on Contributor.
                info["nickname"] = safe_text(getattr(obj, "nickname", None), info["nickname"])
                info["unique_id"] = safe_text(getattr(obj, "unique_id", None), info["unique_id"])
            key = info["unique_id"] or info["nickname"]
            if key and (info["nickname"] != "مستخدم" or info["unique_id"]):
                snapshot[key] = {
                    "nickname": info["nickname"],
                    "unique_id": info["unique_id"],
                }

        for obj in (getattr(event, "ranks", []) or []):
            add_snapshot_user(obj)
        for obj in (getattr(event, "seats", []) or []):
            add_snapshot_user(obj)

        with lock:
            if username not in streams or not streams[username].get("monitoring_enabled", False):
                return
            previous = streams[username]["viewers"]
            streams[username]["viewers"] = total
            streams[username]["last_viewer_update"] = time.time()
            streams[username]["last_activity"] = time.time()
            # Only replace the named current snapshot when TikTok actually supplied names.
            if snapshot:
                streams[username]["viewer_names"] = snapshot
            elif total == 0:
                streams[username]["viewer_names"] = OrderedDict()
            # VIEWER_TIMELINE_PATCH_V1: preserve timestamped named snapshots.
            if snapshot or total == 0:
                timeline_path = streams[username].get("session_files", {}).get("viewers")
                if timeline_path:
                    try:
                        record = {
                            "time": datetime.now().isoformat(timespec="milliseconds"),
                            "total": total,
                            "users": list(snapshot.values()) if total > 0 else [],
                            "snapshot_named_count": len(snapshot),
                        }
                        with open(timeline_path, "a", encoding="utf-8") as f:
                            f.write(json.dumps(record, ensure_ascii=False) + "\n")
                            f.flush()
                    except Exception as archive_error:
                        print(f"Viewer timeline archive error: {archive_error}")

            if total < previous:
                print(f"📉 @{username} | viewers {previous} -> {total} (TikTok does not reliably identify leavers)")
        print(f"👥 @{username} | Viewers: {total} | named snapshot: {len(snapshot)}")

    @client.on(LinkMicBattleEvent)
    async def on_battle(event):
        battle_id = safe_int(getattr(event, "battle_id", None), 0)
        action = safe_text(getattr(event, "action", None))
        bubble = safe_text(getattr(event, "bubble_text", None))

        anchors = []
        for item in getattr(event, "anchors_info", []) or []:
            name = safe_text(getattr(getattr(item, "user", None), "nickname", None))
            if not name:
                name = safe_text(getattr(item, "nickname", None), "مشارك")
            anchors.append({"name": name or "مشارك"})

        armies = []
        for item in getattr(event, "armies", []) or []:
            name = safe_text(getattr(getattr(item, "user", None), "nickname", None))
            if not name:
                name = safe_text(getattr(item, "nickname", None), "مشارك")

            score = 0
            value = getattr(item, "value", None)

            if value is not None:
                score = safe_int(getattr(value, "hostscore", None), 0)

                if not score:
                    for army in getattr(value, "user_armies", []) or []:
                        score += safe_int(getattr(army, "score", None), 0)
                        score += safe_int(getattr(army, "diamond_score", None), 0)

            for attr in ("score", "total_score", "gift_score", "points"):
                if not score:
                    score = safe_int(getattr(item, attr, None), 0)

            armies.append({
                "name": name or "مشارك",
                "score": score
            })

        results = []
        battle_result = getattr(event, "battle_result", None)

        if isinstance(battle_result, dict):
            for user_id, result in battle_result.items():
                score = safe_int(getattr(result, "score", None), 0)
                diamond_score = safe_int(
                    getattr(result, "diamond_score", None), 0
                )

                results.append({
                    "user_id": safe_int(user_id, 0),
                    "score": score,
                    "diamond_score": diamond_score
                })

        # If TikTok provides battle_result, use it as the authoritative score source.
        if results:
            for result in results:
                uid = result["user_id"]

                for anchor in anchors:
                    anchor_uid = safe_int(anchor.get("user_id"), 0)
                    if anchor_uid and anchor_uid == uid:
                        result["name"] = anchor["name"]

                for army in armies:
                    if result["score"] and army["score"] == 0:
                        army["score"] = result["score"]

        active = bool(
            battle_id or
            armies or
            anchors or
            results
        )

        with lock:
            if username not in streams or not streams[username].get("monitoring_enabled", False):
                return

            streams[username]["pk"] = {
                "active": active,
                "battle_id": battle_id or None,
                "action": action,
                "bubble": bubble,
                "anchors": anchors[:20],
                "armies": armies[:20],
                "results": results[:50],
            }
            streams[username]["last_activity"] = time.time()

        print(
            f"⚔️ @{username} | PK/Battle "
            f"id={battle_id} action={action} "
            f"armies={len(armies)} results={len(results)}"
        )
    @client.on(LiveEndEvent)
    async def on_live_end(event):
        with lock:
            if username in streams:
                stream = streams[username]
                stream["live"] = False
                stream["viewers"] = 0
                stream["pk"]["active"] = False
                stream["last_activity"] = time.time()

                # LIVE really ended: stop video first, then finalize
                # the archive directory containing the saved MP4.
                video_expected = bool(
                    stream.get("video_recording") or stream.get("video_file")
                )
                video_ok = True
                if video_expected:
                    video_ok = stop_video_recording(
                        stream,
                        clients.get(username),
                    )

                # Keep the archive active if video finalization failed.
                if video_ok:
                    finish_archive_session(stream)
                else:
                    print(
                        f"Archive preserved because video validation failed "
                        f"for @{username}"
                    )

        print(f"🔴 LIVE ENDED: @{username}")

    return client


ACCOUNTS_FILE = APP_DATA_ROOT / "monitored_accounts.json"


def load_monitored_accounts():
    try:
        if not ACCOUNTS_FILE.exists():
            return []
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        accounts = []
        for item in data:
            username = safe_text(item, "").strip().lstrip("@").strip()
            if username and username not in accounts:
                accounts.append(username)
        return accounts
    except Exception as e:
        print(f"⚠️ Accounts load error: {type(e).__name__}: {e}")
        return []


def save_monitored_accounts(accounts):
    try:
        clean = []
        for item in accounts:
            username = safe_text(item, "").strip().lstrip("@").strip()
            if username and username not in clean:
                clean.append(username)

        ACCOUNTS_FILE.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except Exception as e:
        print(f"⚠️ Accounts save error: {type(e).__name__}: {e}")
        return False


def add_monitored_account(username):
    accounts = load_monitored_accounts()
    username = safe_text(username, "").strip().lstrip("@").strip()

    if not username:
        return False

    if username not in accounts:
        accounts.append(username)
        return save_monitored_accounts(accounts)

    return True


def remove_monitored_account(username):
    accounts = load_monitored_accounts()
    username = safe_text(username, "").strip().lstrip("@").strip()

    if username in accounts:
        accounts.remove(username)
        return save_monitored_accounts(accounts)

    return True


async def start_tiktok(username):
    print(f"🚀 Starting TikTok monitor for @{username}")

    # Permanent supervisor:
    # keep monitoring until the account is removed.
    #
    # IMPORTANT:
    # A TikTok/WebSocket disconnect is NOT the same thing as
    # the LIVE actually ending.  The archive session is therefore
    # finalized only by LiveEndEvent, not by this supervisor.
    while True:
        with lock:
            if username not in streams:
                print(f"🛑 Monitor stopped for @{username}")
                clients.pop(username, None)
                return

            if not streams[username].get("monitoring_enabled", False):
                print(f"⏹️ Manual monitoring is OFF for @{username}")
                clients.pop(username, None)
                return

        client = None
        was_live_before_disconnect = False

        try:
            print(f"🔎 Checking @{username} ...")

            client = create_client(username)

            with lock:
                if username not in streams:
                    return
                clients[username] = client

            task = client.start(
                process_connect_events=True,
                fetch_room_info=True,
                fetch_gift_info=True,
                fetch_live_check=True,
            )

            await task

            # If the client task returns without LiveEndEvent,
            # treat it as a connection interruption, not as LIVE end.
            with lock:
                if username in streams:
                    was_live_before_disconnect = bool(
                        streams[username].get("live")
                    )

        except Exception as e:
            error_type = type(e).__name__
            error_text = f"{error_type}: {e}"
            is_rate_limit = (
                error_type == "SignatureRateLimitError"
                or "rate_limit_account_hour" in error_text
                or "[RATE_LIMIT]" in error_text
            )

            if is_rate_limit:
                user_error = (
                    "تم تجاوز حد الاتصالات المؤقت مع TikTok. "
                    "سيتم إعادة المحاولة تلقائيًا بعد 10 دقائق."
                )
                print(f"⏸️ @{username} TikTok connection rate-limited.")
                print(f"   {user_error}")
            else:
                user_error = error_text
                print(f"⚠️ @{username} {error_text}")

            with lock:
                if username in streams:
                    was_live_before_disconnect = bool(
                        streams[username].get("live")
                    )
                    streams[username]["error"] = user_error

        finally:
            with lock:
                # DO NOT mark the stream offline here.
                # DO NOT finalize the archive here.
                #
                # LiveEndEvent is responsible for the real LIVE end.
                clients.pop(username, None)

            if client is not None:
                try:
                    await client.disconnect(close_client=True)
                except Exception:
                    pass

        # If the account was deleted, do not restart it.
        with lock:
            if username not in streams:
                print(f"🛑 Monitor stopped for @{username}")
                return

            still_live = bool(streams[username].get("live"))

        # If the last connection attempt hit TikTok's account-hour
        # signature rate limit, avoid hammering the service every 30 seconds.
        with lock:
            rate_limited = (
                username in streams
                and "تم تجاوز حد الاتصالات المؤقت مع TikTok"
                in str(streams[username].get("error", ""))
            )

        if rate_limited:
            print(
                f"🕒 @{username} is rate-limited by TikTok. "
                f"Waiting 10 minutes before the next connection attempt..."
            )
            await asyncio.sleep(600)
        elif still_live or was_live_before_disconnect:
            print(
                f"🔌 @{username} connection interrupted. "
                f"Reconnecting in 5 seconds without ending the LIVE session..."
            )
            await asyncio.sleep(5)
        else:
            print(
                f"⏳ @{username} is not currently LIVE. "
                f"Rechecking in 30 seconds..."
            )
            await asyncio.sleep(30)

        # Re-check manual stop after waiting, before another connection attempt.
        with lock:
            if (
                username not in streams
                or not streams[username].get("monitoring_enabled", False)
            ):
                print(f"⏹️ Monitor stopped; no reconnect for @{username}")
                clients.pop(username, None)
                return


def run_async_loop():
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Restore saved accounts only. Monitoring starts manually from the dashboard.
    saved_accounts = load_monitored_accounts()

    for username in saved_accounts:
        with lock:
            if username not in streams:
                streams[username] = new_stream(username)
                print(f"🔄 Restored @{username}")

    print(
        f"📋 Restored {len(saved_accounts)} saved account(s) — "
        f"manual monitoring is OFF by default"
    )

    loop.run_forever()

threading.Thread(target=run_async_loop, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        length = safe_int(self.headers.get("Content-Length"), 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            raw = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if parsed.path == "/api/streams":
            with lock:
                data = {u: dict(s) for u, s in streams.items()}
                # Convert OrderedDict viewer names to ordinary dicts for JSON.
                for s in data.values():
                    s["viewer_names"] = dict(s["viewer_names"])
                    s.pop("gift_combos", None)
            self.send_json(data)
            return
        if parsed.path == "/api/archives":
            archives = []
            try:
                if ARCHIVE_ROOT.exists():
                    folders = sorted(
                        [x for x in ARCHIVE_ROOT.iterdir() if x.is_dir()],
                        key=lambda x: x.stat().st_mtime,
                        reverse=True,
                    )
                    for folder in folders:
                        files = []
                        for filename, file_type in (
                            ("comments.txt", "comments"),
                            ("gifts.txt", "gifts"),
                            ("follows.txt", "follows"),
                            ("joins.txt", "joins"),
                            ("live.mp4", "video"),
                        ):
                            fp = folder / filename
                            if fp.is_file() and fp.stat().st_size > 0:
                                files.append({
                                    "type": file_type,
                                    "path": str(fp.relative_to(ARCHIVE_ROOT)),
                                    "size": fp.stat().st_size,
                                })

                        username = folder.name.split("_", 1)[0]

                        account_name = ""
                        comments_source = folder / "comments.txt"
                        if comments_source.is_file():
                            try:
                                header_text = comments_source.read_text(
                                    encoding="utf-8",
                                    errors="ignore",
                                )[:4096]
                                for header_line in header_text.splitlines():
                                    if header_line.startswith("Account Name:"):
                                        account_name = header_line.split(":", 1)[1].strip()
                                        break
                            except Exception:
                                account_name = ""

                        archives.append({
                            "username": username,
                            "account_name": account_name or username,
                            "folder": folder.name,
                            "files": files,
                        })

                self.send_json(archives)
            except Exception as e:
                print(f"⚠️ Archive listing error: {e}")
                self.send_json({"error": "تعذر قراءة الأرشيف"}, 500)
            return

        if parsed.path == "/api/archive-snapshot":
            from io import BytesIO
            from zipfile import ZipFile, ZIP_DEFLATED
            from urllib.parse import parse_qs, quote
            import re

            username = parse_qs(urlparse(self.path).query).get("username", [""])[0].strip()
            with lock:
                stream = streams.get(username)
                active = bool(stream and stream.get("monitoring_enabled"))
                session_value = stream.get("session_dir") if stream else None
                video_value = stream.get("video_file") if stream else None

            if not active or not session_value:
                self.send_json({"error": "لا توجد جلسة مراقبة نشطة لهذا الحساب"}, 404)
                return

            try:
                root = ARCHIVE_ROOT.resolve()
                folder = FilePath(session_value).resolve()
                if folder == root or root not in folder.parents or not folder.is_dir():
                    self.send_json({"error": "مسار جلسة غير صالح"}, 403)
                    return

                buffer = BytesIO()
                video_status = "لم يُرفق فيديو في هذه النسخة."
                with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
                    for name in ("comments.txt", "gifts.txt", "follows.txt", "joins.txt"):
                        source = folder / name
                        if source.is_file():
                            try:
                                archive.writestr(str(FilePath(folder.name) / name), source.read_bytes())
                            except Exception as err:
                                print("Snapshot text file skipped:", name, err)

                    if video_value:
                        video = FilePath(video_value)
                        try:
                            if video.is_file() and folder in video.resolve().parents:
                                data = video.read_bytes()
                                if data:
                                    archive.writestr(str(FilePath(folder.name) / "live_snapshot.mp4"), data)
                                    video_status = "أُرفقت نسخة من الفيديو الجاري؛ قد لا تعمل حتى ينتهي التسجيل."
                        except Exception as err:
                            video_status = "تعذرت قراءة الفيديو الجاري: " + type(err).__name__

                    archive.writestr(
                        str(FilePath(folder.name) / "SNAPSHOT_INFO.txt"),
                        "نسخة مؤقتة أثناء المراقبة؛ الأرشيف الأصلي لم يتغير.\n" + video_status + "\n"
                    )

                raw = buffer.getvalue()
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", folder.name).strip("._") or "TikTok_session"
                filename = safe + "_snapshot.zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"; filename*=UTF-8\'\'{quote(filename, safe="")}')
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                print("Temporary snapshot sent for @" + username)
            except Exception as err:
                print("Snapshot error:", err)
                self.send_json({"error": "تعذر تجهيز النسخة المؤقتة"}, 500)
            return

        if parsed.path == "/api/archive-zip":
            from io import BytesIO
            from zipfile import ZipFile, ZIP_DEFLATED
            from urllib.parse import parse_qs, quote
            import re

            params = parse_qs(urlparse(self.path).query)
            folder_name = params.get("folder", [""])[0].strip()

            # Accept only a single folder name, never a path.
            if not folder_name or folder_name in (".", "..") or "/" in folder_name or "\\" in folder_name:
                self.send_json({"error": "Invalid archive folder"}, 400)
                return

            try:
                root = ARCHIVE_ROOT.resolve()
                folder = (ARCHIVE_ROOT / folder_name).resolve()

                if folder == root or root not in folder.parents:
                    self.send_json({"error": "Invalid archive folder"}, 403)
                    return
                if not folder.is_dir():
                    self.send_json({"error": "Archive session not found"}, 404)
                    return

                buffer = BytesIO()
                with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
                    for item in sorted(folder.rglob("*")):
                        if not item.is_file():
                            continue
                        resolved = item.resolve()
                        if folder != resolved and folder not in resolved.parents:
                            continue
                        archive.write(item, arcname=str(FilePath(folder.name) / item.relative_to(folder)))

                raw = buffer.getvalue()
                safe_name = re.sub(r'[^A-Za-z0-9._-]+', '_', folder.name).strip("._") or "TikTok_session"
                download_name = safe_name + ".zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{download_name}"; filename*=UTF-8\'\'{quote(download_name, safe="")}'
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception as e:
                print(f"⚠️ Archive ZIP error: {e}")
                self.send_json({"error": "تعذر تجهيز ملف ZIP"}, 500)
            return

        if parsed.path == "/api/archive-video":
            params = {}
            for part in urlparse(self.path).query.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = unquote(v)
            relative_path = params.get("path", "").strip()
            if not relative_path:
                self.send_json({"error": "Archive video required"}, 400)
                return
            try:
                root = ARCHIVE_ROOT.resolve()
                target = (ARCHIVE_ROOT / relative_path).resolve()
                if target == root or root not in target.parents:
                    self.send_json({"error": "Invalid archive path"}, 403)
                    return
                if not target.is_file() or target.suffix.lower() != ".mp4":
                    self.send_json({"error": "Archive video not found"}, 404)
                    return
                size = target.stat().st_size
                range_header = self.headers.get("Range", "")
                start, end = 0, max(0, size - 1)
                partial = False
                if range_header.startswith("bytes="):
                    try:
                        first, last = range_header[6:].split(",", 1)[0].split("-", 1)
                        if first:
                            start = int(first)
                        if last:
                            end = int(last)
                        if start < 0 or start >= size or end < start:
                            raise ValueError("invalid byte range")
                        end = min(end, size - 1)
                        partial = True
                    except Exception:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                length = end - start + 1
                self.send_response(206 if partial else 200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "no-store")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with target.open("rb") as video:
                    video.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = video.read(min(1024 * 256, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                print(f"⚠️ Archive video playback error: {e}")
            return

        if parsed.path == "/api/archive-file":
            query = urlparse(self.path).query
            params = {}
            for part in query.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = unquote(v)

            relative_path = params.get("path", "").strip()
            if not relative_path:
                self.send_json({"error": "Archive file required"}, 400)
                return

            try:
                root = ARCHIVE_ROOT.resolve()
                target = (ARCHIVE_ROOT / relative_path).resolve()

                if target != root and root not in target.parents:
                    self.send_json({"error": "Invalid archive path"}, 403)
                    return

                if not target.is_file():
                    self.send_json({"error": "Archive file not found"}, 404)
                    return

                with target.open("rb") as source:
                    header_bytes = source.read(4096)
                self.send_response(200)
                content_type = "video/mp4" if target.suffix.lower() == ".mp4" else "text/plain; charset=utf-8"
                self.send_header("Content-Type", content_type)
                owner_name = ""
                try:
                    header_text = header_bytes.decode("utf-8", errors="ignore")
                    for header_line in header_text.splitlines():
                        if header_line.startswith("Account Name:"):
                            owner_name = header_line.split(":", 1)[1].strip()
                            break
                except Exception:
                    owner_name = ""

                if not owner_name:
                    owner_name = target.stem

                bad_chars = '<>:"/\\|?*'
                safe_owner = "".join(
                    "_" if ch in bad_chars or ord(ch) < 32 else ch
                    for ch in owner_name
                ).strip(" .")

                if not safe_owner:
                    safe_owner = "TikTok"

                download_name = f"{safe_owner}_{target.name}"

                from urllib.parse import quote
                encoded_name = quote(download_name, safe="")

                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{target.name}"; filename*=UTF-8\'\'{encoded_name}'
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(target.stat().st_size))
                self.end_headers()
                with target.open("rb") as source:
                    while True:
                        chunk = source.read(1024 * 256)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                print(f"⚠️ Archive download error: {e}")
                self.send_json({"error": "تعذر تحميل ملف الأرشيف"}, 500)
            return

        self.send_json({"error": "Not Found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/archive-delete":
            try:
                body = self.read_json()
                folder_name = safe_text(body.get("folder"), "").strip()
                if (not folder_name or folder_name in (".", "..")
                        or "/" in folder_name or "\\" in folder_name):
                    self.send_json({"error": "اسم الأرشيف غير صالح"}, 400)
                    return

                root = ARCHIVE_ROOT.resolve()
                folder = (ARCHIVE_ROOT / folder_name).resolve()
                if folder == root or root not in folder.parents:
                    self.send_json({"error": "مسار الأرشيف غير صالح"}, 403)
                    return
                if not folder.is_dir():
                    self.send_json({"error": "الأرشيف غير موجود"}, 404)
                    return

                # Never delete a folder currently used by any session.
                with lock:
                    active_dirs = {
                        str(FilePath(s.get("session_dir")).resolve())
                        for s in streams.values()
                        if s.get("session_dir")
                    }
                if str(folder) in active_dirs:
                    self.send_json(
                        {"error": "لا يمكن حذف أرشيف جلسة نشطة؛ أوقفها أولًا"},
                        409
                    )
                    return

                shutil.rmtree(folder)
                print(f"🗑️ Archive deleted: {folder.name}")
                self.send_json({"ok": True, "folder": folder.name})
            except Exception as e:
                print(f"Archive deletion error: {e}")
                self.send_json({"error": "تعذر حذف الأرشيف"}, 500)
            return

        # ---------------------------------------------------------
        # Manual START
        # ---------------------------------------------------------
        if parsed.path.startswith("/api/streams/") and parsed.path.endswith("/start"):
            prefix = "/api/streams/"
            username = unquote(
                parsed.path[len(prefix):-len("/start")]
            ).strip()

            if not username:
                self.send_json({"error": "Username required"}, 400)
                return

            with lock:
                stream = streams.get(username)

                if stream is None:
                    self.send_json({"error": "الحساب غير موجود"}, 404)
                    return

                if stream.get("monitoring_enabled", False):
                    self.send_json({
                        "ok": True,
                        "username": username,
                        "monitoring_enabled": True,
                        "already_running": True,
                    })
                    return

                stream["monitoring_enabled"] = True
                stream["error"] = None

            print(f"▶️ Manual monitoring STARTED for @{username}")

            if loop:
                asyncio.run_coroutine_threadsafe(
                    start_tiktok(username),
                    loop,
                )

            self.send_json({
                "ok": True,
                "username": username,
                "monitoring_enabled": True,
            })
            return

        # ---------------------------------------------------------
        # Manual STOP
        # ---------------------------------------------------------
        if parsed.path.startswith("/api/streams/") and parsed.path.endswith("/stop"):
            prefix = "/api/streams/"
            username = unquote(parsed.path[len(prefix):-len("/stop")]).strip()
            if not username:
                self.send_json({"error": "Username required"}, 400)
                return
            with lock:
                stream = streams.get(username)
                if stream is None:
                    self.send_json({"error": "الحساب غير موجود"}, 404)
                    return
                stream["monitoring_enabled"] = False
                stream["error"] = None
                client = clients.get(username)
            print(f"Manual STOP requested for @{username}")
            video_ok = True
            try:
                with lock:
                    current = streams.get(username)
                    has_session = bool(current and current.get("session_dir"))
                    video_expected = bool(
                        current and (
                            current.get("video_recording")
                            or current.get("video_file")
                        )
                    )
                if has_session and video_expected:
                    video_ok = stop_video_recording(current, client)
            except Exception as e:
                video_ok = False
                print(f"Video stop warning: {e}")
            confirmed = client is None
            stop_error = None
            if client is not None and loop is not None:
                try:
                    future = asyncio.run_coroutine_threadsafe(client.disconnect(close_client=True), loop)
                    future.result(timeout=15)
                    confirmed = True
                except Exception as e:
                    stop_error = str(e) or type(e).__name__
                    print(f"Connection stop not confirmed: {stop_error}")
            try:
                with lock:
                    current = streams.get(username)
                    has_session = bool(current and current.get("session_dir"))
                if has_session and video_ok:
                    finish_archive_session(current)
                    print(f"Archive finalized for @{username}")
                elif has_session:
                    print(
                        f"Archive NOT finalized because video stop/validation "
                        f"failed for @{username}"
                    )
            except Exception as e:
                print(f"Archive finalize warning: {e}")
            self.send_json({"ok": True, "username": username, "monitoring_enabled": False, "connection_stop_confirmed": confirmed, "stop_error": stop_error})
            return

        # Add account
        # ---------------------------------------------------------
        if parsed.path != "/api/streams":
            self.send_json({"error": "Not Found"}, 404)
            return

        try:
            body = self.read_json()
        except Exception:
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        username = safe_text(
            body.get("username"),
            ""
        ).strip().lstrip("@").strip()

        if not username:
            self.send_json({"error": "Username required"}, 400)
            return

        if len(username) > 100 or any(c.isspace() for c in username):
            self.send_json({"error": "اسم الحساب غير صالح"}, 400)
            return

        with lock:
            if username in streams:
                self.send_json({"error": "الحساب مضاف مسبقًا"}, 409)
                return

            streams[username] = new_stream(username)

        print(f"➕ Added @{username}")

        # Save the account permanently.
        if not add_monitored_account(username):
            with lock:
                streams.pop(username, None)

            self.send_json({"error": "تعذر حفظ الحساب"}, 500)
            return

        self.send_json({
            "ok": True,
            "username": username,
        })
    def do_DELETE(self):
        parsed = urlparse(self.path)
        prefix = "/api/streams/"
        if not parsed.path.startswith(prefix):
            self.send_json({"error": "Not Found"}, 404)
            return
        username = unquote(parsed.path[len(prefix):]).strip()
        if not username:
            self.send_json({"error": "Username required"}, 400)
            return
        with lock:
            client = clients.get(username)
            streams.pop(username, None)

        # Remove permanently from the monitored accounts list.
        remove_monitored_account(username)

        if client and loop:
            try:
                asyncio.run_coroutine_threadsafe(client.disconnect(close_client=True), loop)
            except Exception:
                pass

        print(f"🗑️ Removed @{username} permanently")
        self.send_json({"ok": True})

    def log_message(self, format, *args):
        print("[WEB]", format % args)


print(f"🚀 Dashboard starting on http://127.0.0.1:{PORT}")
print(f"📱 Open in browser: http://127.0.0.1:{PORT}")
server = ThreadingHTTPServer((HOST, PORT), Handler)
try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\n🛑 Dashboard stopped.")
    server.server_close()
