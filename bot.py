import os
import json
import re
import logging
import time
import sys
import random
import asyncio
from typing import List, Dict, Any, Optional
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    MenuButtonWebApp,
    constants,
    InlineQueryResultCachedAudio
)
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ContextTypes,
    filters
)

# رفع باگ شناخته‌شده ویندوز: ProactorEventLoop گاهی خطای واقعی رو پنهان می‌کنه.
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot_errors.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8929244085:AAGeoUHNqMtIUkPJ7KO--GDK8piSSh_2C1g"
BOT_USERNAME = "gapmusic1_bot"
DB_FILE = "songs_db.json"

# آدرس HTTPS مینی‌اپ (متصل به پورت 8000 سرور FastAPI)
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://music.mohammadcore.ir")

# اگه می‌خوای ذخیره‌ی خودکار فقط تو چت‌های مشخصی کار کنه، آیدی‌هاشون رو اینجا بذار.
# خالی = همه‌جا (پیوی، گروه، کانال) ذخیره می‌شه.
ALLOWED_CHAT_IDS: List[int] = []

# پیام تأیید بعد از چند ثانیه پاک می‌شه
CONFIRM_DELETE_SECONDS = 5

# کش دیتابیس در حافظه جهت سرچ آنی و افزایش سرعت
SONGS_CACHE: List[Dict[str, Any]] = []


def normalize_text(text: str) -> str:
    """استانداردسازی و نرمال‌سازی متون برای جستجوی هوشمند"""
    if not text:
        return ""
    text = str(text).lower()
    replacements = {
        'ي': 'ی', 'ك': 'ک', 'آ': 'ا', 'أ': 'ا', 'إ': 'ا',
        '‌': ' ', '\u200c': ' ', '_': ' ', '-': ' ', '.': ' ', '/': ' ', '\\': ' '
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r'[^\w\s]', '', text)
    return " ".join(text.split())


def load_songs() -> List[Dict[str, Any]]:
    """خوانش ایمن فایل دیتابیس JSON و بارگذاری در کش RAM"""
    global SONGS_CACHE
    if SONGS_CACHE:
        return SONGS_CACHE

    if not os.path.exists(DB_FILE):
        logger.warning(f"فایل دیتابیس {DB_FILE} پیدا نشد!")
        SONGS_CACHE = []
        return SONGS_CACHE
    try:
        with open(DB_FILE, "r", encoding="utf-8-sig") as f:
            content = f.read().strip()
        if not content:
            logger.warning("فایل دیتابیس خالی است!")
            SONGS_CACHE = []
            return SONGS_CACHE

        data = json.loads(content)

        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            raw_items = data.get("messages", data.get("songs", []))
        else:
            raw_items = []

        songs = []
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue

            title = str(item.get("title") or item.get("name") or "نامشخص").strip()
            performer = str(item.get("performer") or item.get("artist") or "نامشخص").strip()
            file_id = str(item.get("file_id") or "").strip()
            file_unique_id = str(item.get("file_unique_id") or "").strip()
            song_id = int(item.get("id", idx))
            chat_id = item.get("chat_id")
            message_id = item.get("message_id")

            # نادیده گرفتن رکوردهای ناقص که نه file_id دارند و نه chat_id/message_id معتبر
            if not file_id and not (chat_id and message_id):
                continue

            search_text = item.get("search_text")
            if not search_text:
                search_text = normalize_text(f"{title} {performer}")
            else:
                search_text = normalize_text(search_text)

            songs.append({
                "id": song_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "title": title,
                "performer": performer,
                "search_text": search_text
            })

        SONGS_CACHE = songs
        return SONGS_CACHE
    except Exception as e:
        logger.error(f"خطا در خواندن دیتابیس: {e}")
        SONGS_CACHE = []
        return SONGS_CACHE


def save_songs(songs: List[Dict[str, Any]]):
    """
    ذخیره‌ی اتمیک: اول تو فایل موقت می‌نویسه، بعد جایگزین فایل اصلی می‌کنه.
    اگه وسط نوشتن برق بره یا برنامه کرش کنه، فایل اصلی سالم می‌مونه.
    """
    global SONGS_CACHE
    tmp_file = DB_FILE + ".tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(songs, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, DB_FILE)
        SONGS_CACHE = songs
    except Exception as e:
        logger.error(f"خطا در ذخیره دیتابیس: {e}")
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except Exception:
            pass


def advanced_search(query: str, songs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """جستجوی سریع و بهینه‌شده برای جلوگیری از Timeout"""
    norm_q = normalize_text(query)
    if not norm_q:
        return []
    query_tokens = norm_q.split()

    matched = []
    for song in songs:
        if any(token in song["search_text"] for token in query_tokens):
            matched.append(song)
            if len(matched) >= 20:
                break
    return matched


# ---------------------------------------------------------------------------
# ذخیره‌ی خودکار موزیک (پیوی + گروه + کانال)
# ---------------------------------------------------------------------------

def extract_audio_info(message) -> Optional[Dict[str, str]]:
    """
    از پیام، اطلاعات فایل صوتی رو درمیاره. هم Audio معمولی و هم Document با mime صوتی
    (مثلاً mp3 که به‌صورت فایل فرستاده شده) پشتیبانی می‌شه. ویس (Voice) ذخیره نمی‌شه.
    """
    if message is None:
        return None

    if message.audio:
        a = message.audio
        file_name = a.file_name or ""
        title = (a.title or os.path.splitext(file_name)[0] or "نامشخص").strip()
        performer = (a.performer or "نامشخص").strip()
        return {
            "file_id": a.file_id,
            "file_unique_id": a.file_unique_id,
            "title": title or "نامشخص",
            "performer": performer or "نامشخص",
        }

    doc = message.document
    if doc and doc.mime_type and doc.mime_type.startswith("audio/"):
        file_name = doc.file_name or ""
        title = (os.path.splitext(file_name)[0] or "نامشخص").strip()
        return {
            "file_id": doc.file_id,
            "file_unique_id": doc.file_unique_id,
            "title": title or "نامشخص",
            "performer": "نامشخص",
        }

    return None


def add_song_if_new(info: Dict[str, str], chat_id: int, message_id: int):
    """
    بررسی تکراری‌بودن + افزودن + ذخیره، همه بدون هیچ await وسطش.
    چون asyncio تک‌ریسمانیه، یعنی دو آهنگ همزمان نمی‌تونن id یکسان بگیرن
    و رکوردی از بین نمی‌ره.
    برمی‌گردونه: (آهنگ, تازه_بود؟)
    """
    songs = load_songs()

    for s in songs:
        same_unique = info["file_unique_id"] and s.get("file_unique_id") == info["file_unique_id"]
        same_file = s.get("file_id") == info["file_id"]
        same_msg = s.get("chat_id") == chat_id and s.get("message_id") == message_id
        if same_unique or same_file or same_msg:
            if info["file_unique_id"] and not s.get("file_unique_id"):
                s["file_unique_id"] = info["file_unique_id"]
                save_songs(songs)
            return s, False

    new_id = max([s.get("id", 0) for s in songs], default=-1) + 1
    new_song = {
        "id": new_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "file_id": info["file_id"],
        "file_unique_id": info["file_unique_id"],
        "title": info["title"],
        "performer": info["performer"],
        "search_text": normalize_text(f"{info['title']} {info['performer']}"),
    }
    songs.append(new_song)
    save_songs(songs)
    return new_song, True


async def delete_later(msg, seconds: int):
    await asyncio.sleep(seconds)
    try:
        await msg.delete()
    except Exception as e:
        logger.warning(f"حذف پیام تأیید ممکن نشد: {e}")


async def auto_save_audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ذخیره‌ی خودکار فایل‌های صوتی از پیوی، گروه و کانال.
    - در پیوی و گروه: پیام تأیید می‌فرسته و بعد از چند ثانیه پاکش می‌کنه.
    - در کانال: ساکت ذخیره می‌کنه (تا پست تأیید توی کانال عمومی دیده نشه).
    """
    try:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return

        if ALLOWED_CHAT_IDS and chat.id not in ALLOWED_CHAT_IDS:
            return

        info = extract_audio_info(message)
        if not info:
            return

        song, is_new = add_song_if_new(info, chat.id, message.message_id)
        is_channel = chat.type == constants.ChatType.CHANNEL

        if is_new:
            logger.info(f"موزیک جدید ذخیره شد: id={song['id']} | {song['performer']} - {song['title']} | chat={chat.id} ({chat.type})")
        else:
            logger.info(f"موزیک تکراری نادیده گرفته شد: id={song['id']} | chat={chat.id}")

        if is_channel:
            return

        if is_new:
            text = (
                f"✅ موزیک با موفقیت در دیتابیس ذخیره شد!\n\n"
                f"🎵 خواننده: {song['performer']}\n"
                f"🎧 نام آهنگ: {song['title']}\n"
                f"📌 کد ثبت شده: `{song['id']}`"
            )
        else:
            text = f"⚠️ این آهنگ قبلاً در دیتابیس ثبت شده است.\n📌 کد آهنگ: `{song['id']}`"

        sent_msg = await message.reply_text(text, parse_mode="Markdown")
        asyncio.create_task(delete_later(sent_msg, CONFIRM_DELETE_SECONDS))

    except Exception as e:
        logger.error(f"خطا در ذخیره خودکار موزیک: {e}")


# ---------------------------------------------------------------------------
# منوها و هندلرهای ربات (مجهز به مینی‌اپ و دکمه دائمی منو)
# ---------------------------------------------------------------------------

def build_pagination_keyboard(songs: List[Dict[str, Any]], page: int = 0, per_page: int = 6, query: str = "") -> InlineKeyboardMarkup:
    """ساخت کیبورد شیشه‌ای مرتب و استاندارد همراه با دکمه مینی‌اپ"""
    total = len(songs)
    start_idx = page * per_page
    page_items = songs[start_idx: start_idx + per_page]

    keyboard = [
        [InlineKeyboardButton(f"🎵 {s['performer']} - {s['title']}"[:45], callback_data=f"dl_{s['id']}")]
        for s in page_items
    ]

    total_pages = max(1, (total + per_page - 1) // per_page)
    safe_q = query[:10] if query else ""

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ صفحه قبل", callback_data=f"page_{page - 1}_{safe_q}"))
    nav_row.append(InlineKeyboardButton(f"📄 {page + 1} از {total_pages}", callback_data="noop"))
    if start_idx + per_page < total:
        nav_row.append(InlineKeyboardButton("صفحه بعد ▶️", callback_data=f"page_{page + 1}_{safe_q}"))

    if nav_row:
        keyboard.append(nav_row)

    if MINI_APP_URL.startswith("https://"):
        keyboard.append([InlineKeyboardButton("🎧 باز کردن مینی‌اپ (پخش آنلاین)", web_app=WebAppInfo(url=MINI_APP_URL))])

    keyboard.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="main_menu")])
    keyboard.append([
        InlineKeyboardButton("🎲 موزیک شانسی", callback_data="random_song"),
        InlineKeyboardButton("🔍 سرچ آنلاین (Inline)", switch_inline_query_current_chat="")
    ])
    keyboard.append([InlineKeyboardButton("🌐 گپ ما", url="https://t.me/+k1KWTCq0FMswZjM0")])

    return InlineKeyboardMarkup(keyboard)


def main_menu_content():
    songs = load_songs()
    text = (
        f"سلام! 👋 به ربات جستجوی موزیک خوش آمدید.\n"
        f"مجموعه موزیک‌های ثبت شده در دیتابیس: {len(songs)} عدد 🎵\n"
        f"نام خواننده یا آهنگ مورد نظر خود را بفرستید یا وارد مینی‌اپ شوید:"
    )
    keyboard = []
    if MINI_APP_URL.startswith("https://"):
        keyboard.append([InlineKeyboardButton("🎧 ورود به مینی‌اپ GapMusic (پخش آنلاین)", web_app=WebAppInfo(url=MINI_APP_URL))])

    keyboard.extend([
        [InlineKeyboardButton("🎵 مشاهده موزیک های موجود", callback_data="list_all_songs")],
        [
            InlineKeyboardButton("🔍 سرچ موزیک (صفحه جدید)", callback_data="prompt_search"),
            InlineKeyboardButton("⚡ سرچ انلاین آنی", switch_inline_query_current_chat="")
        ],
        [InlineKeyboardButton("🎲 موزیک شانسی", callback_data="random_song")],
        [InlineKeyboardButton("🌐 گپ ما", url="https://t.me/+k1KWTCq0FMswZjM0")]
    ])
    return text, InlineKeyboardMarkup(keyboard)


async def post_init(application: Application):
    """تنظیم دکمه دائمی مینی‌اپ در کنار کادر تایپ تلگرام (Menu Button)"""
    if MINI_APP_URL.startswith("https://"):
        try:
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🎧 پلیر GapMusic",
                    web_app=WebAppInfo(url=MINI_APP_URL)
                )
            )
            logger.info(f"دکمه منوی مینی‌اپ (MenuButtonWebApp) روی {MINI_APP_URL} تنظیم شد.")
        except Exception as e:
            logger.warning(f"امکان تنظیم MenuButtonWebApp نبود: {e}")
    else:
        logger.warning("MINI_APP_URL باید با https:// شروع شود تا دکمه مینی‌اپ فعال گردد.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_chat.type != constants.ChatType.PRIVATE:
            return
        text, markup = main_menu_content()
        await update.message.reply_text(text, reply_markup=markup)
    except Exception as e:
        logger.error(f"خطا در دستور start: {e}")


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_chat.type != constants.ChatType.PRIVATE:
            return
        query = update.message.text
        if not query or query.startswith('/'):
            return

        songs = load_songs()
        results = advanced_search(query, songs)

        if not results:
            await update.message.reply_text("❌ آهنگی با این مشخصات در دیتابیس یافت نشد.")
            return

        reply_markup = build_pagination_keyboard(results, page=0, query=query)
        await update.message.reply_text(
            f"🔍 نتایج جستجو برای: `{query}`\nتعداد یافت شده: {len(results)} موزیک",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"خطا در پردازش جستجو: {e}")


async def inline_search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """سرچ اینلاین با ارسال مستقیم خود موزیک با یک کلیک"""
    try:
        query = update.inline_query.query.strip()
        songs = load_songs()

        if query:
            results_songs = advanced_search(query, songs)
        else:
            results_songs = songs[-20:] if len(songs) > 20 else songs

        results = []
        for song in results_songs[:15]:
            file_id = song.get("file_id")
            if file_id:
                results.append(
                    InlineQueryResultCachedAudio(
                        id=str(song["id"]),
                        audio_file_id=file_id,
                        caption=f"🎵 {song['performer']} - {song['title']}"
                    )
                )
        await update.inline_query.answer(results, cache_time=1, is_personal=False)
    except Exception as e:
        logger.error(f"خطا در Inline Search: {e}")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            pass

        data = query.data

        if data == "noop":
            return

        if data == "prompt_search":
            try:
                rows = []
                if MINI_APP_URL.startswith("https://"):
                    rows.append([InlineKeyboardButton("🎧 جستجو و پخش در مینی‌اپ", web_app=WebAppInfo(url=MINI_APP_URL))])
                rows.append([InlineKeyboardButton("⚡ سرچ آنی لحظه‌ای", switch_inline_query_current_chat="")])
                rows.append([InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="main_menu")])

                await query.edit_message_text(
                    "🔍 **نام موزیک مورد نظر را بنویسید:**\n\nهمچنین می‌توانید با کلیک روی دکمه‌های زیر در مینی‌اپ یا به صورت لحظه‌ای جستجو کنید.",
                    reply_markup=InlineKeyboardMarkup(rows),
                    parse_mode="Markdown"
                )
            except Exception as err:
                logger.error(f"خطا در تغییر صفحه سرچ: {err}")
            return

        if data == "main_menu":
            text, markup = main_menu_content()
            try:
                await query.edit_message_text(text, reply_markup=markup)
            except Exception as err:
                logger.error(f"خطا در بازگشت به منوی اصلی: {err}")
            return

        if data == "random_song":
            songs = load_songs()
            if not songs:
                try:
                    await query.answer("موزیکی در دیتابیس موجود نیست.", show_alert=True)
                except Exception:
                    pass
                return

            song = random.choice(songs)
            target_chat_id = query.message.chat_id if (query.message and query.message.chat) else update.effective_user.id

            try:
                if song.get("file_id"):
                    await context.bot.send_audio(
                        chat_id=target_chat_id,
                        audio=song["file_id"],
                        caption=f"🎲 موزیک شانسی:\n🎵 {song['performer']} - {song['title']}"
                    )
                elif song.get("chat_id") and song.get("message_id"):
                    await context.bot.copy_message(
                        chat_id=target_chat_id,
                        from_chat_id=song["chat_id"],
                        message_id=song["message_id"]
                    )
                else:
                    await query.answer("فایل صوتی این موزیک قابل ارسال نیست.", show_alert=True)
            except Exception as e:
                logger.error(f"خطا در ارسال موزیک شانسی: {e}")
                try:
                    await query.answer("خطا در ارسال موزیک شانسی، دوباره امتحان کنید.", show_alert=True)
                except Exception:
                    pass
            return

        if data == "list_all_songs":
            songs = load_songs()
            if not songs:
                try:
                    await query.answer("موزیکی در دیتابیس موجود نیست.", show_alert=True)
                except Exception:
                    pass
                return
            reply_markup = build_pagination_keyboard(songs, page=0, query="")
            try:
                await query.edit_message_text(
                    f"🎵 لیست کل موزیک‌های موجود در دیتابیس (تعداد کل: {len(songs)}):",
                    reply_markup=reply_markup
                )
            except Exception as err:
                logger.error(f"خطا در نمایش لیست موزیک‌ها: {err}")
            return

        if data.startswith("dl_"):
            try:
                song_id = int(data.split("_")[1])
                songs = load_songs()
                song = next((s for s in songs if s["id"] == song_id), None)

                if not song:
                    try:
                        await query.answer("موزیک مورد نظر در دیتابیس یافت نشد.", show_alert=True)
                    except Exception:
                        pass
                    return

                target_chat_id = query.message.chat_id if (query.message and query.message.chat) else update.effective_user.id

                if song.get("file_id"):
                    await context.bot.send_audio(
                        chat_id=target_chat_id,
                        audio=song["file_id"],
                        caption=f"🎵 {song['performer']} - {song['title']}"
                    )
                elif song.get("chat_id") and song.get("message_id"):
                    try:
                        await context.bot.copy_message(
                            chat_id=target_chat_id,
                            from_chat_id=song["chat_id"],
                            message_id=song["message_id"]
                        )
                    except Exception as copy_err:
                        logger.error(f"خطا در کپی پیام: {copy_err}")
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            text=f"❌ امکان ارسال فایل «{song['performer']} - {song['title']}» وجود ندارد."
                        )
                else:
                    try:
                        await query.answer("فایل صوتی این موزیک قابل ارسال نیست.", show_alert=True)
                    except Exception:
                        pass
            except Exception as e:
                logger.error(f"خطا در ارسال فایل: {e}")

        elif data.startswith("page_"):
            parts = data.split("_")
            page = int(parts[1])
            search_q = parts[2] if len(parts) > 2 else ""

            songs = load_songs()
            results = advanced_search(search_q, songs) if search_q else songs
            reply_markup = build_pagination_keyboard(results, page=page, query=search_q)
            try:
                await query.edit_message_reply_markup(reply_markup=reply_markup)
            except Exception:
                pass
    except Exception as e:
        logger.error(f"خطا در مدیریت دکمه شیشه‌ای: {e}")


def main():
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN خالیه. توکن رو توی کد بذار.")
        sys.exit(1)

    while True:
        loop = None
        try:
            logger.info("در حال راه‌اندازی ربات...")

            # هر تلاش با یه event loop تازه شروع می‌شه تا خطای "Event loop is closed" رخ نده
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            global SONGS_CACHE
            SONGS_CACHE = []
            load_songs()

            app = (
                ApplicationBuilder()
                .token(BOT_TOKEN)
                .connect_timeout(15.0)
                .read_timeout(15.0)
                .write_timeout(15.0)
                .pool_timeout(15.0)
                .concurrent_updates(256)
                .post_init(post_init)
                .build()
            )

            app.add_handler(CommandHandler("start", start_command))
            app.add_handler(CallbackQueryHandler(button_callback))
            app.add_handler(InlineQueryHandler(inline_search_handler))

            # ذخیره‌ی خودکار: پیام‌های پیوی/گروه + پست‌های کانال، هم Audio و هم Document صوتی
            audio_filter = (
                (filters.UpdateType.MESSAGE | filters.UpdateType.CHANNEL_POST)
                & (filters.AUDIO | filters.Document.AUDIO)
            )
            app.add_handler(MessageHandler(audio_filter, auto_save_audio_handler))

            app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, search_handler))

            logger.info("ربات با موفقیت اجرا شد.")
            app.run_polling(
                drop_pending_updates=True,
                close_loop=False,
                allowed_updates=["message", "channel_post", "callback_query", "inline_query"],
            )
        except Exception as e:
            logger.critical(f"خطای بحرانی منجر به بسته شدن ربات: {e}")
            logger.info("تلاش مجدد برای روشن شدن ربات تا ۵ ثانیه دیگر...")
        finally:
            if loop is not None:
                try:
                    loop.close()
                except Exception:
                    pass
            time.sleep(5)


if __name__ == "__main__":
    main()
