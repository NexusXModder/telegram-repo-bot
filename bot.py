import os
import zipfile
import shutil
import base64
import requests
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# Render Web Service-এর জন্য ছোট একটি Flask App
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

# Environment variables থেকে টোকেন নেওয়া
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

LICENSE_API = "https://nexus-license.onrender.com/api/v1/licenses"
CLIENT_ID = "app_15347417c1994c92997e"

WAITING_FOR_LICENSE = 1
WAITING_FOR_ZIP = 2
WAITING_FOR_REPO = 3
REPO_PAGE_SIZE = 8


def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def license_error_message(response):
    """Return the API's useful error message without crashing on bad JSON."""
    try:
        data = response.json()
    except ValueError:
        return f"License server error (HTTP {response.status_code}). Please try again later."

    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        if code == "maintenance":
            return str(message or "System is under maintenance. Please try again later.")
        return str(message or "License authentication failed.")

    return f"License server error (HTTP {response.status_code}). Please try again later."


def get_device_token(context):
    return context.user_data.get("license_device_token")


async def require_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_device_token(context):
        return True
    target = update.effective_message
    if target:
        await target.reply_text(
            "🔐 License required.\n\n"
            "Please send your License Key to continue."
        )
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_device_token(context):
        await update.message.reply_text(
            "✅ You are already logged in.\n\n"
            "Send your ZIP file to continue, or use /logout to deactivate this device."
        )
        return WAITING_FOR_ZIP

    await update.message.reply_text(
        "🔐 Nexus License Login\n\n"
        "Please send your License Key to access this bot."
    )
    return WAITING_FOR_LICENSE


async def handle_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    license_key = (update.message.text or "").strip()
    if not license_key:
        await update.message.reply_text("⚠️ Please send a valid License Key.")
        return WAITING_FOR_LICENSE

    user_id = update.effective_user.id
    fingerprint = f"telegram:{user_id}"

    payload = {
        "clientId": CLIENT_ID,
        "licenseKey": license_key,
        "fingerprint": fingerprint,
        "deviceLabel": f"Telegram:{user_id}",
    }

    await update.message.reply_text("🔐 Checking your license...")

    try:
        response = requests.post(
            f"{LICENSE_API}/activate",
            json=payload,
            timeout=15,
        )

        if response.ok:
            data = response.json()
            token = data.get("data", {}).get("deviceToken")
            if not token:
                await update.message.reply_text(
                    "⚠️ License server returned an unexpected response. Please try again later."
                )
                return WAITING_FOR_LICENSE

            context.user_data["license_device_token"] = token
            context.user_data["license_expires_at"] = data.get("data", {}).get("expiresAt")
            await update.message.reply_text(
                "✅ License verified successfully!\n\n"
                "Now send your ZIP file to continue."
            )
            return WAITING_FOR_ZIP

        await update.message.reply_text(f"❌ {license_error_message(response)}")
        return WAITING_FOR_LICENSE

    except requests.RequestException:
        await update.message.reply_text(
            "❌ Could not connect to the license server. Please try again later."
        )
        return WAITING_FOR_LICENSE
    except Exception:
        await update.message.reply_text(
            "❌ An unexpected license error occurred. Please try again later."
        )
        return WAITING_FOR_LICENSE


async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = get_device_token(context)
    if not token:
        await update.message.reply_text("ℹ️ You are not logged in.")
        return ConversationHandler.END

    try:
        response = requests.post(
            f"{LICENSE_API}/deactivate",
            json={"clientId": CLIENT_ID, "deviceToken": token},
            timeout=15,
        )
        if response.ok:
            context.user_data.pop("license_device_token", None)
            context.user_data.pop("license_expires_at", None)
            context.user_data.pop("zip_path", None)
            context.user_data.pop("github_repos", None)
            await update.message.reply_text("✅ License device deactivated.")
        else:
            await update.message.reply_text(f"❌ {license_error_message(response)}")
    except requests.RequestException:
        await update.message.reply_text("❌ Could not connect to the license server.")
    return ConversationHandler.END


def github_error(response):
    try:
        data = response.json()
        return data.get("message") or f"GitHub API error (HTTP {response.status_code})."
    except ValueError:
        return f"GitHub API error (HTTP {response.status_code})."


def get_repositories():
    """Fetch all repositories accessible by the configured GitHub token."""
    repos = []
    page = 1
    while True:
        response = requests.get(
            "https://api.github.com/user/repos",
            headers=github_headers(),
            params={"per_page": 100, "page": page, "sort": "updated", "direction": "desc"},
            timeout=20,
        )
        if response.status_code != 200:
            raise RuntimeError(github_error(response))
        batch = response.json()
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        if page > 20:
            break
    return repos


def repo_picker_keyboard(repos, page=0):
    start = page * REPO_PAGE_SIZE
    page_repos = repos[start:start + REPO_PAGE_SIZE]
    keyboard = []

    for index, repo in enumerate(page_repos, start=start):
        lock = "🔒" if repo.get("private") else "🌐"
        name = repo.get("full_name") or repo.get("name") or "Unknown repo"
        keyboard.append([
            InlineKeyboardButton(
                f"{lock} {name}",
                callback_data=f"repo_pick:{index}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"repo_page:{page - 1}"))
    if start + REPO_PAGE_SIZE < len(repos):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"repo_page:{page + 1}"))
    if nav:
        keyboard.append(nav)

    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data="repo_refresh"),
        InlineKeyboardButton("❌ Cancel", callback_data="repo_cancel"),
    ])
    return InlineKeyboardMarkup(keyboard)


async def show_repo_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0, edit=False):
    if not GITHUB_TOKEN:
        text = "❌ GITHUB_TOKEN is not configured on the server."
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.effective_message.reply_text(text)
        return WAITING_FOR_ZIP

    try:
        repos = context.user_data.get("github_repos")
        if repos is None or page == -1:
            repos = get_repositories()
            context.user_data["github_repos"] = repos
            page = 0

        if not repos:
            text = "📂 No GitHub repositories were found for this token."
            if edit and update.callback_query:
                await update.callback_query.edit_message_text(text)
            else:
                await update.effective_message.reply_text(text)
            return WAITING_FOR_REPO

        total_pages = (len(repos) + REPO_PAGE_SIZE - 1) // REPO_PAGE_SIZE
        text = (
            "📦 ZIP received successfully!\n\n"
            "📁 Choose the GitHub repository where you want to upload it:\n\n"
            f"Page {page + 1}/{total_pages} • {len(repos)} repositories"
        )
        markup = repo_picker_keyboard(repos, page)
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        else:
            await update.effective_message.reply_text(text, reply_markup=markup)
        return WAITING_FOR_REPO
    except requests.RequestException:
        text = "❌ Could not connect to GitHub. Please try again."
    except Exception as exc:
        text = f"❌ Could not load GitHub repositories.\n\n{exc}"

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text)
    else:
        await update.effective_message.reply_text(text)
    return WAITING_FOR_REPO


async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE

    document = update.message.document
    if not document:
        await update.message.reply_text(
            "⚠️ Please send a ZIP file."
        )
        return WAITING_FOR_ZIP

    filename = document.file_name or "project.zip"
    if not filename.lower().endswith(".zip"):
        await update.message.reply_text("⚠️ Please send a ZIP file (.zip).")
        return WAITING_FOR_ZIP

    file = await context.bot.get_file(document.file_id)
    zip_path = f"temp_{update.effective_user.id}_{filename}"
    await file.download_to_drive(zip_path)

    context.user_data["zip_path"] = zip_path
    context.user_data["github_repos"] = None

    return await show_repo_picker(update, context, page=0)


async def invalid_zip_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ Please send a ZIP file, or use /cancel to stop."
    )
    return WAITING_FOR_ZIP


async def repo_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        page = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Invalid page.", show_alert=True)
        return WAITING_FOR_REPO
    return await show_repo_picker(update, context, page=page, edit=True)


async def repo_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Refreshing repositories...")
    return await show_repo_picker(update, context, page=-1, edit=True)


async def repo_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    zip_path = context.user_data.pop("zip_path", None)
    context.user_data.pop("github_repos", None)
    if zip_path and os.path.exists(zip_path):
        os.remove(zip_path)
    await query.edit_message_text("❎ Upload cancelled.")
    return ConversationHandler.END


async def repo_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Repository selected.")

    try:
        index = int(query.data.split(":", 1)[1])
        repos = context.user_data.get("github_repos") or []
        repo = repos[index]
    except (ValueError, IndexError, TypeError):
        await query.edit_message_text("❌ That repository selection is no longer available. Please send the ZIP again.")
        return ConversationHandler.END

    repo_fullname = repo.get("full_name")
    if not repo_fullname:
        await query.edit_message_text("❌ Could not determine the selected repository.")
        return ConversationHandler.END

    zip_path = context.user_data.get("zip_path")
    if not zip_path or not os.path.exists(zip_path):
        await query.edit_message_text("❌ ZIP file is no longer available. Please send it again.")
        return WAITING_FOR_ZIP

    await query.edit_message_text(
        f"📤 Uploading to `{repo_fullname}`...\n\nPlease wait ⏳",
        parse_mode="Markdown",
    )

    extract_dir = f"extracted_{update.effective_user.id}"
    headers = github_headers()
    uploaded = 0
    skipped = 0

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            bad = zip_ref.testzip()
            if bad:
                raise RuntimeError(f"The ZIP file is corrupted near: {bad}")
            zip_ref.extractall(extract_dir)

        for root, _, files in os.walk(extract_dir):
            for file_name in files:
                local_file_path = os.path.join(root, file_name)
                relative_path = os.path.relpath(local_file_path, extract_dir).replace("\\", "/")

                # Python cache / VCS metadata should never be uploaded.
                parts = relative_path.split("/")
                if "__pycache__" in parts or file_name.endswith((".pyc", ".pyo")) or ".git" in parts:
                    skipped += 1
                    continue

                url = f"https://api.github.com/repos/{repo_fullname}/contents/{relative_path}"
                with open(local_file_path, "rb") as f:
                    content_encoded = base64.b64encode(f.read()).decode("utf-8")

                get_response = requests.get(url, headers=headers, timeout=20)
                data = {
                    "message": f"Upload/Update {relative_path} via Telegram Bot",
                    "content": content_encoded,
                }

                if get_response.status_code == 200:
                    sha = get_response.json().get("sha")
                    if sha:
                        data["sha"] = sha
                elif get_response.status_code != 404:
                    raise RuntimeError(f"Could not check {relative_path}: {github_error(get_response)}")

                put_response = requests.put(url, headers=headers, json=data, timeout=30)
                if put_response.status_code not in (200, 201):
                    raise RuntimeError(f"Failed to upload {relative_path}: {github_error(put_response)}")
                uploaded += 1

        await query.edit_message_text(
            f"✅ Upload complete!\n\n"
            f"📦 Repository: `{repo_fullname}`\n"
            f"📄 Files uploaded/updated: {uploaded}\n"
            f"⏭️ Cache files skipped: {skipped}",
            parse_mode="Markdown",
        )
    except zipfile.BadZipFile:
        await query.edit_message_text("❌ Invalid or corrupted ZIP file.")
    except requests.RequestException:
        await query.edit_message_text("❌ Could not connect to GitHub while uploading.")
    except Exception as exc:
        await query.edit_message_text(f"❌ Upload failed:\n\n{exc}")
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)
        context.user_data.pop("zip_path", None)
        context.user_data.pop("github_repos", None)

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    zip_path = context.user_data.pop("zip_path", None)
    context.user_data.pop("github_repos", None)
    if zip_path and os.path.exists(zip_path):
        os.remove(zip_path)
    await update.message.reply_text("প্রসেস বাতিল করা হয়েছে। ❎")
    return ConversationHandler.END


def main():
    server_thread = Thread(target=run_flask)
    server_thread.daemon = True
    server_thread.start()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('login', start),
            MessageHandler(filters.Document.ALL, handle_zip),
        ],
        states={
            WAITING_FOR_LICENSE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_license),
            ],
            WAITING_FOR_ZIP: [
                MessageHandler(filters.Document.ALL, handle_zip),
                MessageHandler(filters.TEXT & ~filters.COMMAND, invalid_zip_input),
            ],
            WAITING_FOR_REPO: [
                CallbackQueryHandler(repo_pick_callback, pattern=r"^repo_pick:\d+$"),
                CallbackQueryHandler(repo_page_callback, pattern=r"^repo_page:\d+$"),
                CallbackQueryHandler(repo_refresh_callback, pattern=r"^repo_refresh$"),
                CallbackQueryHandler(repo_cancel_callback, pattern=r"^repo_cancel$"),
                MessageHandler(filters.Document.ALL, handle_zip),
                MessageHandler(filters.TEXT & ~filters.COMMAND, invalid_zip_input),
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CommandHandler('logout', logout),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)
    print("বট সফলভাবে চালু হয়েছে...✅")
    app.run_polling()


if __name__ == '__main__':
    main()
