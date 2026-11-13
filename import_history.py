import json
import os

# مسیر فایل خروجی تلگرام دسکتاپ
EXPORTED_FILE = "result.json"
# مسیر دیتابیس ربات
DB_FILE = "songs_db.json"

def import_history():
    if not os.path.exists(EXPORTED_FILE):
        print(f"❌ فایل {EXPORTED_FILE} پیدا نشد! ابتدا از تلگرام خروجی JSON بگیرید.")
        return

    # خواندن فایل خروجی تلگرام
    with open(EXPORTED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # گرفتن ID گروه (اگر ساپورت شود)
    chat_id = data.get("id")
    if chat_id and not str(chat_id).startswith("-100"):
        chat_id = int(f"-100{chat_id}")

    # خواندن دیتابیس فعلی موزیک‌ها
    existing_songs = []
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content:
                existing_songs = json.loads(content)

    existing_ids = {s.get("message_id") for s in existing_songs}
    count = 0

    messages = data.get("messages", [])
    for msg in messages:
        # بررسی اینکه پیام حاوی فایل صوتی یا موزیک است
        if msg.get("type") == "message" and ("media_type" in msg and msg.get("media_type") == "audio_file"):
            msg_id = msg.get("id")
            if msg_id in existing_ids:
                continue

            title = msg.get("title") or ""
            performer = msg.get("performer") or ""
            file_name = msg.get("file") or ""

            searchable_text = f"{title} {performer} {file_name}".strip().lower()
            if not searchable_text:
                searchable_text = "آهنگ بدون نام"

            song_data = {
                "id": len(existing_songs) + count,
                "chat_id": chat_id,
                "message_id": msg_id,
                "file_id": msg.get("file_id", ""),
                "title": title or "نامشخص",
                "performer": performer or "نامشخص",
                "search_text": searchable_text
            }
            existing_songs.append(song_data)
            count += 1

    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(existing_songs, f, ensure_ascii=False, indent=2)

    print(f"✅ موفقیت‌آمیز بود! تعداد {count} آهنگ جدید به دیتابیس اضافه شد.")

if __name__ == "__main__":
    import_history()