import asyncio
import json
import os
import time
import math
from urllib.parse import quote_plus
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait

try:
    from TechVJ.bot import TechVJBot
    from TechVJ.util.file_properties import get_name, get_hash
except:
    from bot import TechVJBot
    from util.file_properties import get_name, get_hash

from info import URL, LOG_CHANNEL

# ADMIN_ID = 0 → সব ইউজার ব্যবহার করতে পারবে
# ADMIN_ID = আপনার আইডি → শুধু আপনি
ADMIN_ID = 6621572366

TEXT_DELETE = 120
DIRECT_DELETE = 120

upload_sessions = {}
direct_sessions = {}
bot_messages = {}   # {uid: set(msg_id)}
delete_tasks = {}   # {uid: {msg_id: asyncio.Task}}

QUALITY_MAP = {
    "480": "link480",
    "720": "link720",
    "1080": "link1080",
    "4k": "link4k"
}

# ==================== Rate Limiter (Token Bucket) ====================
class TokenBucket:
    def __init__(self, rate, capacity):
        self.rate = rate          # tokens per second
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last = time.time()
        self.lock = asyncio.Lock()

    async def acquire(self, tokens=1):
        async with self.lock:
            while True:
                now = time.time()
                self.tokens = min(
                    self.capacity,
                    self.tokens + (now - self.last) * self.rate
                )
                self.last = now
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
                wait = (tokens - self.tokens) / self.rate
                await asyncio.sleep(wait)

class RateLimiterManager:
    def __init__(self):
        # Global: 20 req/s (Telegram ~30, headroom rakha)
        self.global_bucket = TokenBucket(rate=20, capacity=25)
        self.chat_buckets = {}   # chat_id -> TokenBucket

    def _get_bucket(self, chat_id, is_group=False):
        if chat_id not in self.chat_buckets:
            if is_group:
                # Channel/Group: 20 msg/min = 0.333/s
                self.chat_buckets[chat_id] = TokenBucket(rate=20/60, capacity=3)
            else:
                # Private: 1 msg/s
                self.chat_buckets[chat_id] = TokenBucket(rate=1.0, capacity=2)
        return self.chat_buckets[chat_id]

    async def acquire(self, chat_id, is_group=False):
        await self.global_bucket.acquire()
        bucket = self._get_bucket(chat_id, is_group)
        await bucket.acquire()

rate_limiter = RateLimiterManager()

async def safe_send_message(client, chat_id, text, **kwargs):
    await rate_limiter.acquire(chat_id, is_group=False)
    return await client.send_message(chat_id, text, **kwargs)

async def safe_send_cached_media(client, chat_id, file_id, **kwargs):
    await rate_limiter.acquire(chat_id, is_group=True)
    return await client.send_cached_media(chat_id, file_id, **kwargs)

async def safe_edit_message_text(client, chat_id, message_id, text, **kwargs):
    await rate_limiter.acquire(chat_id, is_group=False)
    return await client.edit_message_text(chat_id, message_id, text, **kwargs)

# ==================== UI Boxes ====================
def create_box(title, lines):
    top = "┏━━━━━━━━◉😇◉━━━━━━━━━┓"
    bottom = "┗━━━━━━━━◉🔥◉━━━━━━━━━┛"
    content = f"┃  {title}  ┃\n┗━━━━━━━━◉🖥️◉━━━━━━━━━┛"
    for line in lines:
        content += f"\n┣⪼ {line}"
    content += f"\n{bottom}"
    return top + "\n" + content

def start_box():
    lines = [
        "STATUS: ONLINE",
        "MODE: ULTRA PRO",
        "/upload - Batch Mode",
        "/direct - Live Mode",
        "/cancel - Stop Process",
        "/auto_restart - Restart Settings"
    ]
    return create_box("😈 RS ANIME BOT❱━➣", lines)

def direct_active_box():
    lines = [
        "Send any video file",
        "Auto link generation",
        "Unlimited files",
        "/cancel to stop"
    ]
    return create_box("😈 DIRECT MODE ACTIVE❱━➣", lines)

def direct_link_box(episode, link):
    lines = [
        f"EPISODE {episode:02d}",
        f"URL :-\n`{link}`"
    ]
    return create_box("🔗 LINK GENERATED❱━➣", lines)

def processing_box(progress, current, total, eta, episode, quality):
    filled = int(progress / 100 * 10)
    bar = "█" * filled + "░" * (10 - filled)
    lines = [
        bar,
        f"📊 {progress:.1f}%",
        f"📺 EP {episode} - {quality.upper()}",
        f"🚀 {current}/{total}",
        f"⏰ {eta}s"
    ]
    return create_box("😈 PROCESSING...❱━➣", lines)

def complete_box(total_ep, total_req, total_time):
    lines = [
        f"Episodes: {total_ep}",
        f"Requests: {total_req}",
        f"Time: {total_time}s"
    ]
    return create_box("😈 COMPLETE!❱━➣", lines)

def upload_box(text):
    lines = [text]
    return create_box("😈 RS ANIME❱━➣", lines)

def cancel_box():
    lines = ["Process cancelled by user"]
    return create_box("😈 CANCELLED❱━➣", lines)

def auto_delete_box(minutes):
    lines = [f"Will delete in {minutes} minutes"]
    return create_box("😈 AUTO DELETE❱━➣", lines)

def output_box(episodes):
    lines = [f"Episodes: {episodes}"]
    return create_box("😈 OUTPUT READY❱━➣", lines)

def is_admin(uid):
    if ADMIN_ID == 0:
        return True
    return uid == ADMIN_ID

# ==================== Per-message delete tracking ====================
def _ensure_tracking(uid):
    if uid not in bot_messages:
        bot_messages[uid] = set()
    if uid not in delete_tasks:
        delete_tasks[uid] = {}

async def _delete_after(uid, msg_id, delay):
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    try:
        await TechVJBot.delete_messages(uid, msg_id)
    except Exception:
        pass
    bot_messages.get(uid, set()).discard(msg_id)
    delete_tasks.get(uid, {}).pop(msg_id, None)

async def track_bot_msg(uid, msg_id, delay=None):
    """
    Track a message.
    delay=None → মেসেজ ট্র্যাক হবে কিন্তু অটো-ডিলিট হবে না।
    delay=N → N সেকেন্ড পরে অটো-ডিলিট।
    """
    _ensure_tracking(uid)
    bot_messages[uid].add(msg_id)
    if delay and delay > 0:
        old = delete_tasks[uid].get(msg_id)
        if old:
            old.cancel()
        delete_tasks[uid][msg_id] = asyncio.create_task(
            _delete_after(uid, msg_id, delay)
        )

def forget_msg(uid, msg_id):
    bot_messages.get(uid, set()).discard(msg_id)
    t = delete_tasks.get(uid, {}).pop(msg_id, None)
    if t:
        t.cancel()

async def cleanup_uid_messages(uid):
    for t in list(delete_tasks.get(uid, {}).values()):
        t.cancel()
    delete_tasks[uid] = {}
    msgs = list(bot_messages.get(uid, set()))
    bot_messages[uid] = set()
    for msg_id in msgs:
        try:
            await TechVJBot.delete_messages(uid, msg_id)
        except Exception:
            pass

# ==================== Smart Uploader (FloodWait-safe) ====================
class SmartUploader:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.last_time = 0.0
        self.gap = 0.5
        self.flood_until = 0.0
        self.success_streak = 0

    async def upload(self, fid, retries=8):
        async with self.lock:
            last_err = None
            for attempt in range(retries):
                now = time.time()
                if now < self.flood_until:
                    await asyncio.sleep(self.flood_until - now)

                gap_needed = self.gap - (time.time() - self.last_time)
                if gap_needed > 0:
                    await asyncio.sleep(gap_needed)

                try:
                    result = await safe_send_cached_media(
                        TechVJBot, LOG_CHANNEL, fid
                    )
                    self.last_time = time.time()
                    self.success_streak += 1
                    if self.success_streak >= 5:
                        self.gap = max(0.25, self.gap - 0.05)
                        self.success_streak = 0
                    return result
                except FloodWait as e:
                    wait = int(getattr(e, "value", 5)) + 5
                    self.flood_until = time.time() + wait
                    self.gap = min(self.gap * 2.0, 6.0)
                    self.success_streak = 0
                    last_err = e
                    await asyncio.sleep(wait)
                except Exception as e:
                    last_err = e
                    if attempt == retries - 1:
                        break
                    await asyncio.sleep(2 + attempt)
            raise last_err or Exception("Upload failed after retries")

uploader = SmartUploader()

async def upload_file_with_retry(fid):
    return await uploader.upload(fid)

# ==================== Handlers ====================
@TechVJBot.on_message(filters.command("start") & filters.private)
async def start(client, m):
    uid = m.from_user.id
    if not is_admin(uid):
        return await m.reply("Access Denied")
    msg = await safe_send_message(client, uid, start_box())
    await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)

@TechVJBot.on_message(filters.command("upload") & filters.private)
async def upload_mode(client, m):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    direct_sessions.pop(uid, None)
    upload_sessions[uid] = {
        "qualities": [],
        "default": "skip",
        "files": [],
        "processing": False,
        "completed": False
    }
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("480p", callback_data="q_480"),
         InlineKeyboardButton("720p", callback_data="q_720")],
        [InlineKeyboardButton("1080p", callback_data="q_1080"),
         InlineKeyboardButton("4K", callback_data="q_4k")],
        [InlineKeyboardButton("ALL", callback_data="q_all")],
        [InlineKeyboardButton("480+720", callback_data="q_480_720")],
        [InlineKeyboardButton("720+1080", callback_data="q_720_1080")],
        [InlineKeyboardButton("480+1080", callback_data="q_480_1080")],
        [InlineKeyboardButton("480+720+1080", callback_data="q_480_720_1080")]
    ])
    msg = await safe_send_message(
        client, uid, upload_box("SELECT QUALITY"), reply_markup=kb
    )
    await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)

@TechVJBot.on_message(filters.command("direct") & filters.private)
async def direct_mode(client, m):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    upload_sessions.pop(uid, None)
    direct_sessions[uid] = {
        "active": True,
        "count": 0,
        "queue": asyncio.Queue(),
        "processing": False
    }
    msg = await safe_send_message(client, uid, direct_active_box())
    await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)

@TechVJBot.on_callback_query()
async def callback_handler(client, q):
    uid = q.from_user.id
    if not is_admin(uid):
        return
    s = upload_sessions.get(uid)
    if not s:
        return
    data = q.data
    if data.startswith("q_"):
        if data == "q_all":
            s["qualities"] = ["480", "720", "1080", "4k"]
        else:
            s["qualities"] = data.replace("q_", "").split("_")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{x.upper()}", callback_data=f"d_{x}")
             for x in s["qualities"]],
            [InlineKeyboardButton("SKIP DEFAULT", callback_data="d_skip")]
        ])
        await safe_edit_message_text(
            client, uid, q.message.id,
            upload_box("SELECT DEFAULT QUALITY OR SKIP"),
            reply_markup=kb
        )
        await track_bot_msg(uid, q.message.id, delay=TEXT_DELETE)
    elif data.startswith("d_"):
        s["default"] = data.replace("d_", "")
        await safe_edit_message_text(
            client, uid, q.message.id,
            upload_box("SEND VIDEO FILES\nThen type /finish")
        )
        await track_bot_msg(uid, q.message.id, delay=TEXT_DELETE)

@TechVJBot.on_message(filters.private & (filters.video | filters.document))
async def collect_files(client, m):
    uid = m.from_user.id
    if uid in upload_sessions:
        s = upload_sessions[uid]
        if s.get("processing"):
            msg = await safe_send_message(
                client, uid,
                upload_box("Processing in progress! Wait for /finish to complete.")
            )
            await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)
            return
        if s.get("completed"):
            msg = await safe_send_message(
                client, uid,
                upload_box("Session completed! Use /upload for new session.")
            )
            await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)
            return
        file = m.video or m.document
        s["files"].append({
            "id": file.file_id,
            "name": file.file_name
        })
    elif uid in direct_sessions:
        s = direct_sessions.get(uid)
        if s and s.get("active"):
            file = m.video or m.document
            s["count"] += 1
            ep_num = s["count"]
            await s["queue"].put((ep_num, file.file_id))
            if not s.get("processing"):
                s["processing"] = True
                asyncio.create_task(process_direct_queue(client, uid))

async def process_direct_queue(client, uid):
    s = direct_sessions.get(uid)
    if not s:
        return
    while not s["queue"].empty():
        ep_num, fid = await s["queue"].get()
        try:
            upload_result = await upload_file_with_retry(fid)
            if upload_result:
                name = quote_plus(get_name(upload_result))
                link = f"{URL}{upload_result.id}/{name}?hash={get_hash(upload_result)}"
                msg = await safe_send_message(
                    client, uid,
                    direct_link_box(ep_num, link),
                    disable_web_page_preview=True
                )
                await track_bot_msg(uid, msg.id, delay=DIRECT_DELETE)
        except Exception:
            error_msg = await safe_send_message(
                client, uid,
                upload_box(f"FAILED EPISODE {ep_num}")
            )
            await track_bot_msg(uid, error_msg.id, delay=DIRECT_DELETE)
    if s.get("active"):
        note = await safe_send_message(
            client, uid,
            auto_delete_box(DIRECT_DELETE // 60)
        )
        await track_bot_msg(uid, note.id, delay=DIRECT_DELETE)
    s["processing"] = False

@TechVJBot.on_message(filters.command("finish") & filters.private)
async def finish_upload(client, m):
    uid = m.from_user.id
    if not is_admin(uid):
        return
    s = upload_sessions.get(uid)

    if not s:
        msg = await safe_send_message(
            client, uid,
            upload_box("No active session. Use /upload first")
        )
        await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)
        return
    if s.get("processing"):
        msg = await safe_send_message(
            client, uid,
            upload_box("Already processing! Please wait...")
        )
        await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)
        return
    if s.get("completed"):
        msg = await safe_send_message(
            client, uid,
            upload_box("Already completed! Use /upload for new session")
        )
        await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)
        return

    qualities = s["qualities"]
    if not qualities:
        msg = await safe_send_message(
            client, uid,
            upload_box("No quality selected. Use /upload first")
        )
        await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)
        return

    group = len(qualities)
    files = s["files"]

    if len(files) == 0:
        msg = await safe_send_message(
            client, uid,
            upload_box("No files received. Send video files first")
        )
        await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)
        return

    if len(files) % group != 0:
        msg = await safe_send_message(
            client, uid,
            upload_box(f"File count mismatch! Need multiples of {group}. Current: {len(files)}")
        )
        await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)
        return

    total_episodes = len(files) // group
    total_requests = total_episodes * group

    s["processing"] = True
    status_msg = await safe_send_message(
        client, uid,
        processing_box(0, 0, total_requests, 0, 0, "start")
    )
    # Status msg: ট্র্যাক হবে, কিন্তু অটো-ডিলিট হবে না
    await track_bot_msg(uid, status_msg.id)

    result = []
    request_count = 0
    start_time = time.time()
    last_update = time.time()
    failed_any = False

    try:
        for ep_num in range(total_episodes):
            start_idx = ep_num * group
            chunk = files[start_idx:start_idx + group]

            episode_data = {
                "episodeNumber": ep_num + 1,
                "title": f"Episode {ep_num + 1}"
            }

            default_link = None

            for quality_idx, quality in enumerate(qualities):
                request_count += 1
                file_data = chunk[quality_idx]

                now = time.time()
                if now - last_update >= 5:
                    progress = (request_count / total_requests) * 100
                    elapsed = now - start_time
                    avg_time = elapsed / request_count if request_count > 0 else 0
                    eta = avg_time * (total_requests - request_count)
                    try:
                        await safe_edit_message_text(
                            client, uid, status_msg.id,
                            processing_box(
                                progress, request_count, total_requests,
                                int(eta), ep_num + 1, quality
                            )
                        )
                    except Exception:
                        pass
                    last_update = now

                try:
                    upload_result = await upload_file_with_retry(file_data["id"])
                    if upload_result:
                        name = quote_plus(get_name(upload_result))
                        link = f"{URL}{upload_result.id}/{name}?hash={get_hash(upload_result)}"
                        episode_data[QUALITY_MAP[quality]] = link
                        if s["default"] != "skip" and quality == s["default"]:
                            default_link = link
                    else:
                        failed_any = True
                except Exception:
                    failed_any = True

            if default_link:
                episode_data["link"] = default_link

            if len(episode_data) > 2:
                result.append(episode_data)

        total_time = time.time() - start_time

        try:
            await safe_edit_message_text(
                client, uid, status_msg.id,
                complete_box(len(result), total_requests, int(total_time))
            )
        except Exception:
            pass

        await asyncio.sleep(2)

        try:
            await client.delete_messages(uid, status_msg.id)
        except Exception:
            pass
        forget_msg(uid, status_msg.id)

        if len(result) == 0:
            msg = await safe_send_message(
                client, uid,
                upload_box("No episodes processed! All files failed.")
            )
            await track_bot_msg(uid, msg.id, delay=TEXT_DELETE)
        else:
            fname = "RS_ANIME_OUTPUT.txt"
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)

            doc_msg = await client.send_document(
                uid, fname,
                caption=output_box(len(result))
            )
            # ★★★ JSON ফাইল মেসেজ ট্র্যাক হবে কিন্তু অটো-ডিলিট হবে না ★★★
            await track_bot_msg(uid, doc_msg.id)

            # সার্ভার থেকে ফাইল ডিলিট করব না (JSON থাকবে)
            # os.remove(fname)  ← বাদ দেওয়া হলো

        if failed_any:
            warn_msg = await safe_send_message(
                client, uid,
                upload_box("WARNING: Some files failed to upload. Check output file.")
            )
            await track_bot_msg(uid, warn_msg.id, delay=TEXT_DELETE)

        note = await safe_send_message(
            client, uid,
            auto_delete_box(TEXT_DELETE // 60)
        )
        await track_bot_msg(uid, note.id, delay=TEXT_DELETE)
        s["completed"] = True

    except Exception as e:
        try:
            await safe_edit_message_text(
                client, uid, status_msg.id,
                upload_box(f"ERROR: {str(e)[:50]}")
            )
        except Exception:
            pass
    finally:
        s["processing"] = False
        if uid in upload_sessions:
            del upload_sessions[uid]

@TechVJBot.on_message(filters.command("cancel") & filters.private)
async def cancel_process(client, m):
    uid = m.from_user.id
    if uid in upload_sessions:
        upload_sessions[uid]["processing"] = False
        del upload_sessions[uid]
    if uid in direct_sessions:
        direct_sessions[uid]["active"] = False
        direct_sessions[uid]["queue"] = asyncio.Queue()
        del direct_sessions[uid]
    await cleanup_uid_messages(uid)
    msg = await safe_send_message(client, uid, cancel_box())
    await track_bot_msg(uid, msg.id, delay=5)
