import os, asyncio, threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters
import subprocess, shutil

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running on Render Web Service!"

BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_URL = os.getenv("BASE_URL")
BASE_DIR = "/opt/render/project/src/static/hls/"
MAX_STORAGE_MB = 500


def get_duration(path):
    try:
        out = subprocess.check_output(
            f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{path}"',
            shell=True
        )
        return float(out)
    except:
        return 0


def cleanup_storage():
    folders = []
    total = 0

    for root, dirs, _ in os.walk(BASE_DIR):
        for d in dirs:
            fp = os.path.join(root, d)
            size = sum(
                os.path.getsize(os.path.join(fp, f))
                for f in os.listdir(fp)
                if os.path.isfile(os.path.join(fp, f))
            )
            folders.append((size, fp))
            total += size

    if total/(1024*1024) > MAX_STORAGE_MB:
        folders.sort(reverse=True)
        shutil.rmtree(folders[0][1], ignore_errors=True)


async def process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    f = msg.video or msg.document
    fid = f.file_id

    out = os.path.join(BASE_DIR, fid)
    os.makedirs(out, exist_ok=True)

    input_path = os.path.join(out, "input.mp4")

    await msg.reply_text("📥 Downloading...")
    tgf = await context.bot.get_file(fid)
    await tgf.download_to_drive(input_path)

    await msg.reply_text("⚙ Encoding...")

    duration = get_duration(input_path)

    cmd = f"""
    ffmpeg -y -i "{input_path}" -preset veryfast \
      -map v:0 -map a:0 -s:v:0 1920x1080 -b:v:0 3500k \
      -map v:0 -map a:0 -s:v:1 1280x720 -b:v:1 2000k \
      -map v:0 -map a:0 -s:v:2 854x480  -b:v:2 1000k \
      -map v:0 -map a:0 -s:v:3 640x360  -b:v:3 600k \
      -map v:0 -map a:0 -s:v:4 426x240  -b:v:4 350k \
      -hls_time 6 -hls_playlist_type vod \
      -master_pl_name master.m3u8 \
      -var_stream_map "v:0,a:0 v:1,a:1 v:2,a:2 v:3,a:3 v:4,a:4" \
      -hls_segment_filename "{out}/stream_%v/segment_%d.ts" \
      "{out}/stream_%v/stream.m3u8"
    """

    proc = await asyncio.create_subprocess_shell(cmd)
    await proc.wait()

    cleanup_storage()

    m3u8 = f"{BASE_URL}{fid}/master.m3u8"
    await msg.reply_text(f"🎉 HLS Ready:\n{m3u8}")


async def bot_runner():
    os.makedirs(BASE_DIR, exist_ok=True)

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(MessageHandler(filters.ALL, process))

    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    print("BOT STARTED")


def run_flask():
    app.run(host="0.0.0.0", port=10000, debug=False, use_reloader=False)


if __name__ == "__main__":
    # Start Flask in separate thread
    threading.Thread(target=run_flask).start()

    # Start telegram bot inside asyncio loop
    asyncio.run(bot_runner())
