# ================= IMPORTS =================

import humanize
from Script import script
from pyrogram import filters, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from urllib.parse import quote_plus

from TechVJ.bot import TechVJBot
from TechVJ.util.file_properties import get_name, get_hash, get_media_file_size
from TechVJ.util.human_readable import humanbytes

from info import URL, LOG_CHANNEL
from database.users_chats_db import db
from utils import temp


# ================= /START =================

@TechVJBot.on_message(filters.command("start") & filters.private)
async def start(client, message):

    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id, message.from_user.first_name)
        await client.send_message(
            LOG_CHANNEL,
            script.LOG_TEXT_P.format(
                message.from_user.id,
                message.from_user.mention
            )
        )

    buttons = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✨ Update Channel", url="https://t.me/cartoonfunny03")]]
    )

    await message.reply_text(
        text=script.START_TXT.format(
            message.from_user.mention,
            temp.U_NAME,
            temp.B_NAME
        ),
        reply_markup=buttons,
        parse_mode=enums.ParseMode.HTML
    )


# ================= VIDEO / DOCUMENT =================

@TechVJBot.on_message(filters.private & (filters.video | filters.document))
async def stream_start(client, message):

    try:
        # ✅ Correct media access (Pyrogram v2)
        file = message.video or message.document
        if not file:
            return

        status = await message.reply_text("⏳ Processing your file...")

        # send to log channel
        log_msg = await client.send_cached_media(
            chat_id=LOG_CHANNEL,
            file_id=file.file_id
        )

        # ✅ file info (নাম না থাকলে file_id use করবে → এরর আসবে না)
        file_name = get_name(log_msg) or str(log_msg.id)
        safe_name = quote_plus(file_name)
        file_size = humanbytes(get_media_file_size(message))
        user_id = message.from_user.id
        username = message.from_user.mention

        # ✅ DIRECT LINKS (NO SHORTLINK, NO API)
        stream = f"{URL}watch/{log_msg.id}/{safe_name}?hash={get_hash(log_msg)}"
        download = f"{URL}{log_msg.id}/{safe_name}?hash={get_hash(log_msg)}"

        # log channel reply
        await log_msg.reply_text(
            text=(
                f"•• LINK GENERATED\n"
                f"•• USER ID : {user_id}\n"
                f"•• USERNAME : {username}\n\n"
                f"•• FILE : {file_name}"
            ),
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("🚀 Fast Download 🚀", url=download),
                    InlineKeyboardButton("🖥️ Watch Online 🖥️", url=stream)
                ]]
            )
        )

        # user buttons
        user_buttons = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton("🖥 Stream", url=stream),
                InlineKeyboardButton("📥 Download", url=download)
            ]]
        )

        text = (
            "<b>✅ Your Link Generated!</b>\n\n"
            f"<b>📂 File :</b> <i>{file_name}</i>\n\n"
            f"<b>📦 Size :</b> <i>{file_size}</i>\n\n"
            f"<b>🖥 Watch :</b>\n{stream}\n\n"
            f"<b>📥 Download :</b>\n{download}\n\n"
            "<b>🚸 Note :</b> Link will not expire unless deleted."
        )

        await status.edit_text(
            text=text,
            reply_markup=user_buttons,
            disable_web_page_preview=True
        )

    except Exception as e:
        await message.reply_text(f"❌ ERROR:\n<code>{e}</code>")
        print(e)