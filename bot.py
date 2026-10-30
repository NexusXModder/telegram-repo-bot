
import os
import zipfile
import shutil
import base64
from threading import Thread
import requests
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# Render Web Service
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host="0.0.0.0", port=port)


# Environment Variables
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

WAITING_FOR_ZIP = 1
WAITING_FOR_REPO = 2


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Nexus GitHub Uploader\n\n"
        "Welcome! No login or license verification required.\n\n"
        "📦 Please send your ZIP file to continue."
    )
    return WAITING_FOR_ZIP


async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document

    if not document:
        await update.message.reply_text(
            "⚠️ Please attach and send a ZIP file."
        )
        return WAITING_FOR_ZIP

    if not document.file_name.lower().endswith(".zip"):
        await update.message.reply_text(
            "⚠️ Only ZIP files are supported."
        )
        return WAITING_FOR_ZIP

    file = await context.bot.get_file(document.file_id)

    zip_path = f"temp_{update.effective_user.id}.zip"

    await file.download_to_drive(zip_path)

    context.user_data["zip_path"] = zip_path

    await update.message.reply_text(
        "✅ ZIP file received!\n\n"
        "Now send your GitHub Repository name and Path.\n\n"
        "Format:\n"
        "Username/RepositoryName\n"
        "or\n"
        "Username/RepositoryName/folder\n\n"
        "Example:\n"
        "NexusXModder/my-app\n"
        "NexusXModder/my-app/src"
    )

    return WAITING_FOR_REPO


async def invalid_zip_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ I'm waiting for your ZIP file.\n\n"
        "Please attach a ZIP file or use /cancel."
    )
    return WAITING_FOR_ZIP


async def handle_repo_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = (update.message.text or "").strip().strip("/")
    parts = user_input.split("/")

    if len(parts) < 2 or not all(parts[:2]):
        await update.message.reply_text(
            "❌ Invalid format!\n\n"
            "Correct format: Username/RepositoryName"
        )
        return WAITING_FOR_REPO

    repo_fullname = f"{parts[0]}/{parts[1]}"
    target_path = "/".join(parts[2:]) if len(parts) > 2 else ""

    zip_path = context.user_data.get("zip_path")

    if not zip_path or not os.path.exists(zip_path):
        await update.message.reply_text(
            "❌ ZIP file not found. Please send it again."
        )
        return WAITING_FOR_ZIP

    extract_dir = f"extracted_{update.effective_user.id}"

    await update.message.reply_text(
        "⏳ Extracting ZIP and uploading files to GitHub..."
    )

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    try:
        if not GITHUB_TOKEN:
            raise Exception("GitHub token is missing from environment variables.")

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)

        uploaded = 0

        for root, _, files in os.walk(extract_dir):
            for file_name in files:

                local_file_path = os.path.join(root, file_name)

                relative_path = os.path.relpath(
                    local_file_path, extract_dir
                ).replace("\\", "/")

                github_file_path = (
                    f"{target_path}/{relative_path}"
                    if target_path else relative_path
                )

                url = (
                    f"https://api.github.com/repos/"
                    f"{repo_fullname}/contents/{github_file_path}"
                )

                with open(local_file_path, "rb") as f:
                    content_encoded = base64.b64encode(
                        f.read()
                    ).decode("utf-8")

                get_response = requests.get(
                    url,
                    headers=headers,
                    timeout=20,
                )

                data = {
                    "message": f"Upload/Update {github_file_path} via Telegram Bot",
                    "content": content_encoded,
                }

                if get_response.status_code == 200:
                    data["sha"] = get_response.json().get("sha")

                elif get_response.status_code not in (404,):
                    raise Exception(
                        f"GitHub error checking {github_file_path}: "
                        f"{get_response.text}"
                    )

                put_response = requests.put(
                    url,
                    headers=headers,
                    json=data,
                    timeout=30,
                )

                if put_response.status_code not in (200, 201):
                    error = put_response.json().get(
                        "message", put_response.text
                    )
                    raise Exception(
                        f"Failed to upload {github_file_path}: {error}"
                    )

                uploaded += 1

        await update.message.reply_text(
            f"✅ Successfully uploaded/updated all files!\n\n"
            f"📁 Repository: {repo_fullname}\n"
            f"📦 Files uploaded: {uploaded}\n\n"
            f"🚀 GitHub upload completed!"
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ An error occurred:\n\n{str(e)}"
        )

    finally:
        context.user_data.pop("zip_path", None)

        if os.path.exists(zip_path):
            os.remove(zip_path)

        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir, ignore_errors=True)

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    zip_path = context.user_data.pop("zip_path", None)

    if zip_path and os.path.exists(zip_path):
        os.remove(zip_path)

    await update.message.reply_text(
        "❎ Process cancelled."
    )

    return ConversationHandler.END


def main():

    server_thread = Thread(target=run_flask)
    server_thread.daemon = True
    server_thread.start()

    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is missing!")

    application = Application.builder().token(
        TELEGRAM_BOT_TOKEN
    ).build()

    conv_handler = ConversationHandler(

        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Document.ALL, handle_zip),
        ],

        states={

            WAITING_FOR_ZIP: [
                MessageHandler(filters.Document.ALL, handle_zip),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    invalid_zip_input
                ),
            ],

            WAITING_FOR_REPO: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    handle_repo_info
                ),
            ],

        },

        fallbacks=[
            CommandHandler("cancel", cancel),
        ],

        allow_reentry=True,
    )

    application.add_handler(conv_handler)

    print("Nexus GitHub Uploader started successfully! ✅")

    application.run_polling()


if __name__ == "__main__":
    main()
