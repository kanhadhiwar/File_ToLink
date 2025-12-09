import os, asyncio
import subprocess, shutil

from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running on Render Free Web Service!"


BOT_TOKEN = os.environ.get("BOT_TOKEN")
BASE_URL = os.environ.get("BASE_URL")
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

    if total / (1024*1024) > MAX_STORAGE_MB:
        folders.sort(reverse=True)
        shutil.rmtree(folders[0][1], ignore_errors=True)


async def encode_with_progress(chat, input_path, out, bot):
    duration = get_duration(input_path)

    cmd = f"""
    ffmpeg -y -i "{input_path}" -preset veryfast \
      -map v:0 -map a:0 -s:v:0 1920x1080 -b:v:0 3500k -c:v:0 libx264 -c:a:0 aac \
      -map v:0 -map a:0 -s:v:1 1280x720  -b:v:1 2000k -c:v:1 libx264 -c:a:1 aac \
      -map v:0 -map a:0 -s:v:2 854x480   -b:v:2 1000k -c:v:2 libx264 -c:a:2 aac \
      -map v:0 -map a:0 -s:v:3 640x360   -b:v:3 600k  -c:v:3 libx264 -c:a:3 aac \
      -map v:0 -map a:0 -s:v:4 426x240   -b:v:4 350k  -c:v:4 libx264 -c:a:4 aac \
      -hls_time 6 -hls_playlist_type vod \
      -master_pl_name master.m3u8 \
      -var_stream_map "v:0,a:0 v:1,a:1 v:2,a:2 v:3,a:3 v:4,a:4" \
      -hls_segment_filename "{out}/stream_%v/segment_%d.ts" \
      "{out}/stream_%v/stream.m3u8" \
      -progress pipe:1 -nostats
    """

    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    progress_msg = None
    last_pct = 0

    while True:
        line = await proc.stdout.readline()
        if not line:
            break

        line = line.decode().strip()

        if line.startswith("out_time_ms") and duration > 0:
            ms = int(line.split("=")[1])
            pct = int((ms/1000) / duration * 100)

            if pct - last_pct >= 5:
                last_pct = pct
                if not progress_msg:
                    progress_msg = await bot.send_message(chat, f"Encoding… {pct}%")
                else:
                    await bot.edit_message_text(f"Encoding… {pct}%",
                                                chat_id=chat,
                                                message_id=progress_msg.message_id)

    await proc.wait()
    return proc.returncode


async def process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    f = msg.video or msg.document
    fid = f.file_id

    output = os.path.join(BASE_DIR, fid)
    os.makedirs(output, exist_ok=True)

    input_path = os.path.join(output, "input.mp4")

    await msg.reply_text("📥 Downloading...")
    tgf = await context.bot.get_file(fid)
    await tgf.download_to_drive(input_path)

    await msg.reply_text("⚙ Encoding started…")

    rc = await encode_with_progress(msg.chat_id, input_path, output, context.bot)

    if rc == 0:
        cleanup_storage()
        m3u8 = f"{BASE_URL}{fid}/master.m3u8"
        await msg.reply_text(f"🎉 HLS Ready!\n\n{m3u8}")
    else:
        await msg.reply_text("❌ Encoding failed.")


async def run_bot():
    os.makedirs(BASE_DIR, exist_ok=True)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.ALL, process))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await app.updater.idle()


# Start bot async task
asyncio.get_event_loop().create_task(run_bot())


# Run Flask server (port binding for Render)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
