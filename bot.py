import os, subprocess, shutil, asyncio, threading
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

app = Flask(__name__)

# Health check route (Render needs this)
@app.route('/')
def home():
    return "Bot is running on Render Free Web Service!"

BOT_TOKEN = os.environ.get("BOT_TOKEN")
BASE_DIR = "/opt/render/project/src/static/hls/"
BASE_URL = os.environ.get("BASE_URL")
MAX_STORAGE_MB = 500


def get_duration(input_path):
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{input_path}"'
        return float(subprocess.check_output(cmd, shell=True))
    except:
        return 0


def cleanup_storage():
    total = 0
    folders = []

    for root, dirs, _ in os.walk(BASE_DIR):
        for d in dirs:
            folder = os.path.join(root, d)
            size = sum(
                os.path.getsize(os.path.join(folder, f))
                for f in os.listdir(folder)
                if os.path.isfile(os.path.join(folder, f))
            )
            total += size
            folders.append((size, folder))

    if total / (1024 * 1024) > MAX_STORAGE_MB:
        folders.sort(reverse=True)
        shutil.rmtree(folders[0][1], ignore_errors=True)


async def encode_with_progress(chat, input_path, out_dir, bot):
    duration = get_duration(input_path)

    cmd = f"""
    ffmpeg -y -i "{input_path}" -preset veryfast \
      -map v:0 -map a:0 -c:v:0 libx264 -b:v:0 3500k -s:v:0 1920x1080 -c:a:0 aac \
      -map v:0 -map a:0 -c:v:1 libx264 -b:v:1 2000k -s:v:1 1280x720 -c:a:1 aac \
      -map v:0 -map a:0 -c:v:2 libx264 -b:v:2 1000k -s:v:2 854x480 -c:a:2 aac \
      -map v:0 -map a:0 -c:v:3 libx264 -b:v:3 600k -s:v:3 640x360 -c:a:3 aac \
      -map v:0 -map a:0 -c:v:4 libx264 -b:v:4 350k -s:v:4 426x240 -c:a:4 aac \
      -f hls -hls_time 6 -hls_playlist_type vod \
      -master_pl_name master.m3u8 \
      -var_stream_map "v:0,a:0 v:1,a:1 v:2,a:2 v:3,a:3 v:4,a:4" \
      -hls_segment_filename "{out_dir}/stream_%v/segment_%03d.ts" \
      "{out_dir}/stream_%v/stream.m3u8" \
      -progress pipe:1 -nostats
    """

    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    msg_id = None
    last_pct = 0

    while True:
        line = await proc.stdout.readline()
        if not line:
            break

        line = line.decode().strip()

        if line.startswith("out_time_ms") and duration > 0:
            ms = int(line.split("=")[1])
            pct = int((ms/1000) / duration * 100)
            pct = min(100, max(0, pct))

            if pct - last_pct >= 5:
                last_pct = pct
                if msg_id is None:
                    m = await bot.send_message(chat_id=chat, text=f"Encoding… {pct}%")
                    msg_id = m.message_id
                else:
                    await bot.edit_message_text(chat_id=chat, message_id=msg_id, text=f"Encoding… {pct}%")

    await proc.wait()
    return proc.returncode


async def process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    f = msg.video or msg.document
    fid = f.file_id

    out_path = os.path.join(BASE_DIR, fid)
    os.makedirs(out_path, exist_ok=True)

    input_path = os.path.join(out_path, "input.mp4")

    await msg.reply_text("📥 Downloading video...")
    tgf = await context.bot.get_file(fid)
    await tgf.download_to_drive(input_path)

    await msg.reply_text("⚙ Starting encoding…")

    rc = await encode_with_progress(msg.chat_id, input_path, out_path, context.bot)

    if rc == 0:
        cleanup_storage()
        await msg.reply_text(f"🎉 HLS Ready!\n\n{BASE_URL}{fid}/master.m3u8")
    else:
        await msg.reply_text("❌ Encoding failed.")


def start_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL, process))
    app.run_polling()


# Run Telegram bot in background thread
threading.Thread(target=start_bot).start()

# Run Flask server (Render needs this)
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
