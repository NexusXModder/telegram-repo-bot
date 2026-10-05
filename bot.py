import os
import zipfile
import shutil
import base64
import requests
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

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
WAITING_FOR_EDIT = 4



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
    await update.message.reply_text(
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

        await update.message.reply_text(
            f"❌ {license_error_message(response)}"
        )
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
            await update.message.reply_text("✅ License device deactivated.")
        else:
            await update.message.reply_text(f"❌ {license_error_message(response)}")
    except requests.RequestException:
        await update.message.reply_text("❌ Could not connect to the license server.")
    return ConversationHandler.END


async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE

    document = update.message.document
    if not document:
        await update.message.reply_text("⚠️ এটি কোনো ফাইল নয়! অনুগ্রহ করে গিটহাবে আপলোড করার জন্য একটি ZIP ফাইল পাঠান।")
        return WAITING_FOR_ZIP

    file = await context.bot.get_file(document.file_id)
    zip_path = f"temp_{document.file_name}"
    await file.download_to_drive(zip_path)
    
    context.user_data['zip_path'] = zip_path
    await update.message.reply_text(
        "ZIP ফাইল পেয়েছি! 📦\n\n"
        "এখন GitHub Repo-র নাম এবং Path দিন।\n"
        "ফরম্যাট: `Username/RepositoryName` অথবা `Username/RepositoryName/folder`\n\n"
        "উদাহরণ: `NexusXModder/my-app` অথবা `NexusXModder/my-app/src`"
    )
    return WAITING_FOR_REPO

# ফাইল না পাঠিয়ে টেক্সট পাঠালে এই ফাংশন উত্তর দেবে
async def invalid_zip_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚠️ আমি ZIP ফাইলের জন্য অপেক্ষা করছি। অনুগ্রহ করে মেসেজ না পাঠিয়ে একটি ZIP ফাইল অ্যাটাচ করে পাঠান। (বাতিল করতে /cancel লিখুন)")
    return WAITING_FOR_ZIP

async def handle_repo_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE

    user_input = update.message.text.strip().strip('/')
    parts = user_input.split('/')

    if len(parts) < 2:
        await update.message.reply_text("ভুল ফরম্যাট! সঠিক ফরম্যাট: `Username/RepositoryName`")
        return WAITING_FOR_REPO

    repo_fullname = f"{parts[0]}/{parts[1]}"
    target_path = "/".join(parts[2:]) if len(parts) > 2 else ""

    zip_path = context.user_data.get('zip_path')
    extract_dir = "extracted_files"

    await update.message.reply_text("ফাইল Unzip করা হচ্ছে এবং GitHub-এ আপলোড শুরু হচ্ছে... ⏳")

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        for root, _, files in os.walk(extract_dir):
            # Never upload Python cache or compiled files.
            files = [f for f in files if f != "__pycache__" and not f.endswith((".pyc", ".pyo"))]
            for file_name in files:
                local_file_path = os.path.join(root, file_name)
                if "__pycache__" in local_file_path.split(os.sep):
                    continue
                relative_path = os.path.relpath(local_file_path, extract_dir).replace("\\", "/")
                
                github_file_path = f"{target_path}/{relative_path}" if target_path else relative_path
                url = f"https://api.github.com/repos/{repo_fullname}/contents/{github_file_path}"

                with open(local_file_path, 'rb') as f:
                    content_encoded = base64.b64encode(f.read()).decode('utf-8')

                get_response = requests.get(url, headers=headers)
                data = {
                    "message": f"Upload/Update {github_file_path} via Telegram Bot",
                    "content": content_encoded
                }

                if get_response.status_code == 200:
                    sha = get_response.json().get('sha')
                    data['sha'] = sha

                put_response = requests.put(url, headers=headers, json=data)

                if put_response.status_code not in [200, 201]:
                    raise Exception(f"Failed to upload {github_file_path}: {put_response.json().get('message')}")

        await update.message.reply_text(f"সফলভাবে সব ফাইল `{repo_fullname}`-এ আপলোড ও আপডেট হয়ে গেছে! ✅")

    except Exception as e:
        await update.message.reply_text(f"একটি সমস্যা হয়েছে: {str(e)}")

    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)

    return ConversationHandler.END


# =========================
# GitHub A-Z management
# =========================
GITHUB_API = "https://api.github.com"


def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def github_error(response):
    try:
        data = response.json()
        return data.get("message", f"GitHub API error (HTTP {response.status_code})")
    except ValueError:
        return f"GitHub API error (HTTP {response.status_code})"


def parse_repo(value):
    value = value.strip().strip("/")
    parts = value.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return parts[0], parts[1]


def github_ready():
    return bool(GITHUB_TOKEN)


async def github_guard(update: Update):
    if not await require_license(update, update.get_bot().application.user_data if False else update.get_bot()):
        return False
    return True


async def repos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    if not github_ready():
        await update.message.reply_text("❌ GITHUB_TOKEN Render Environment-এ পাওয়া যায়নি।")
        return ConversationHandler.END
    try:
        r = requests.get(f"{GITHUB_API}/user/repos?per_page=100&sort=updated", headers=github_headers(), timeout=15)
        if not r.ok:
            await update.message.reply_text(f"❌ {github_error(r)}")
            return ConversationHandler.END
        items = r.json()
        if not items:
            await update.message.reply_text("📂 কোনো repository পাওয়া যায়নি।")
            return ConversationHandler.END
        lines = ["📚 Your GitHub Repositories:\n"]
        for x in items[:100]:
            visibility = "🔒 Private" if x.get("private") else "🌐 Public"
            lines.append(f"• `{x['full_name']}` — {visibility}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except requests.RequestException as e:
        await update.message.reply_text(f"❌ GitHub connection failed: {e}")
    return ConversationHandler.END


async def create_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    if not github_ready():
        await update.message.reply_text("❌ GITHUB_TOKEN Render Environment-এ পাওয়া যায়নি।")
        return ConversationHandler.END
    if not context.args or len(context.args) > 2:
        await update.message.reply_text("Usage:\n/create_repo RepoName [private|public]\n\nExample:\n/create_repo my-project private")
        return ConversationHandler.END
    name = context.args[0]
    private = len(context.args) == 2 and context.args[1].lower() == "private"
    payload = {"name": name, "private": private, "auto_init": False}
    try:
        r = requests.post(f"{GITHUB_API}/user/repos", headers=github_headers(), json=payload, timeout=15)
        if r.status_code == 201:
            data = r.json()
            await update.message.reply_text(f"✅ Repository created!\n\n📦 `{data['full_name']}`\n🔗 {data['html_url']}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ {github_error(r)}")
    except requests.RequestException as e:
        await update.message.reply_text(f"❌ GitHub connection failed: {e}")
    return ConversationHandler.END


async def delete_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    if not github_ready():
        await update.message.reply_text("❌ GITHUB_TOKEN Render Environment-এ পাওয়া যায়নি।")
        return ConversationHandler.END
    if len(context.args) != 2 or context.args[1].upper() != "CONFIRM":
        await update.message.reply_text("⚠️ Repository permanently delete করতে:\n/delete_repo owner/repo CONFIRM")
        return ConversationHandler.END
    repo = parse_repo(context.args[0])
    if not repo:
        await update.message.reply_text("❌ Format: owner/repo")
        return ConversationHandler.END
    try:
        r = requests.delete(f"{GITHUB_API}/repos/{repo[0]}/{repo[1]}", headers=github_headers(), timeout=15)
        if r.status_code == 204:
            await update.message.reply_text(f"🗑️ `{repo[0]}/{repo[1]}` deleted successfully.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ {github_error(r)}")
    except requests.RequestException as e:
        await update.message.reply_text(f"❌ GitHub connection failed: {e}")
    return ConversationHandler.END


async def repo_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /repo_info owner/repo")
        return ConversationHandler.END
    repo = parse_repo(context.args[0])
    if not repo:
        await update.message.reply_text("❌ Format: owner/repo")
        return ConversationHandler.END
    try:
        r = requests.get(f"{GITHUB_API}/repos/{repo[0]}/{repo[1]}", headers=github_headers(), timeout=15)
        if not r.ok:
            await update.message.reply_text(f"❌ {github_error(r)}")
            return ConversationHandler.END
        d = r.json()
        await update.message.reply_text(
            f"📦 {d['full_name']}\n\n"
            f"🔒 {'Private' if d['private'] else 'Public'}\n"
            f"⭐ {d['stargazers_count']}\n"
            f"🌿 Default branch: `{d['default_branch']}`\n"
            f"📁 {d.get('size', 0)} KB\n"
            f"🔗 {d['html_url']}", parse_mode="Markdown")
    except requests.RequestException as e:
        await update.message.reply_text(f"❌ GitHub connection failed: {e}")
    return ConversationHandler.END


async def browse_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    if len(context.args) not in (1, 2):
        await update.message.reply_text("Usage: /browse owner/repo [folder/path]")
        return ConversationHandler.END
    repo = parse_repo(context.args[0])
    if not repo:
        await update.message.reply_text("❌ Format: owner/repo")
        return ConversationHandler.END
    path = context.args[1] if len(context.args) == 2 else ""
    try:
        url = f"{GITHUB_API}/repos/{repo[0]}/{repo[1]}/contents/{path}" if path else f"{GITHUB_API}/repos/{repo[0]}/{repo[1]}/contents"
        r = requests.get(url, headers=github_headers(), timeout=15)
        if not r.ok:
            await update.message.reply_text(f"❌ {github_error(r)}")
            return ConversationHandler.END
        data = r.json()
        if isinstance(data, dict):
            await update.message.reply_text(f"📄 `{data.get('path')}`\n🔗 {data.get('html_url')}", parse_mode="Markdown")
            return ConversationHandler.END
        lines = [f"📂 `{repo[0]}/{repo[1]}/{path}`" if path else f"📂 `{repo[0]}/{repo[1]}`", ""]
        for x in data:
            icon = "📁" if x.get("type") == "dir" else "📄"
            lines.append(f"{icon} `{x.get('path')}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except requests.RequestException as e:
        await update.message.reply_text(f"❌ GitHub connection failed: {e}")
    return ConversationHandler.END


async def view_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /view_file owner/repo/path/to/file")
        return ConversationHandler.END
    repo = parse_repo(context.args[0])
    if not repo:
        await update.message.reply_text("❌ Format: owner/repo")
        return ConversationHandler.END
    path = context.args[1]
    try:
        r = requests.get(f"{GITHUB_API}/repos/{repo[0]}/{repo[1]}/contents/{path}", headers=github_headers(), timeout=15)
        if not r.ok:
            await update.message.reply_text(f"❌ {github_error(r)}")
            return ConversationHandler.END
        d = r.json()
        if d.get("encoding") != "base64":
            await update.message.reply_text("❌ This file cannot be displayed as text.")
            return ConversationHandler.END
        content = base64.b64decode(d["content"]).decode("utf-8", errors="replace")
        if len(content) > 3500:
            content = content[:3500] + "\n... [truncated]"
        await update.message.reply_text(f"📄 `{path}`\n\n```text\n{content}\n```", parse_mode="Markdown")
    except (requests.RequestException, ValueError) as e:
        await update.message.reply_text(f"❌ Could not read file: {e}")
    return ConversationHandler.END


async def edit_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /edit_file owner/repo path/to/file")
        return ConversationHandler.END
    repo = parse_repo(context.args[0])
    if not repo:
        await update.message.reply_text("❌ Format: owner/repo")
        return ConversationHandler.END
    context.user_data["edit_repo"] = f"{repo[0]}/{repo[1]}"
    context.user_data["edit_path"] = context.args[1]
    await update.message.reply_text("✏️ এখন নতুন file content পাঠাও। এই message-টাই পুরো file replace করবে।\n\n/cancel দিয়ে বাতিল করতে পারো।")
    return WAITING_FOR_EDIT


async def edit_file_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    repo = context.user_data.get("edit_repo")
    path = context.user_data.get("edit_path")
    if not repo or not path:
        return ConversationHandler.END
    try:
        owner, name = repo.split("/", 1)
        url = f"{GITHUB_API}/repos/{owner}/{name}/contents/{path}"
        old = requests.get(url, headers=github_headers(), timeout=15)
        sha = old.json().get("sha") if old.status_code == 200 else None
        payload = {"message": f"Edit {path} via Telegram Bot", "content": base64.b64encode((update.message.text or "").encode()).decode()}
        if sha:
            payload["sha"] = sha
        r = requests.put(url, headers=github_headers(), json=payload, timeout=15)
        if r.status_code in (200, 201):
            await update.message.reply_text(f"✅ `{path}` updated successfully.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ {github_error(r)}")
    except requests.RequestException as e:
        await update.message.reply_text(f"❌ GitHub connection failed: {e}")
    finally:
        context.user_data.pop("edit_repo", None)
        context.user_data.pop("edit_path", None)
    return ConversationHandler.END


async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    if len(context.args) != 3 or context.args[2].upper() != "CONFIRM":
        await update.message.reply_text("⚠️ File permanently delete করতে:\n/delete_file owner/repo path/to/file CONFIRM")
        return ConversationHandler.END
    repo = parse_repo(context.args[0])
    if not repo:
        await update.message.reply_text("❌ Format: owner/repo")
        return ConversationHandler.END
    path = context.args[1]
    try:
        url = f"{GITHUB_API}/repos/{repo[0]}/{repo[1]}/contents/{path}"
        old = requests.get(url, headers=github_headers(), timeout=15)
        if old.status_code != 200:
            await update.message.reply_text(f"❌ {github_error(old)}")
            return ConversationHandler.END
        sha = old.json().get("sha")
        r = requests.delete(url, headers=github_headers(), json={"message": f"Delete {path} via Telegram Bot", "sha": sha}, timeout=15)
        if r.status_code == 200:
            await update.message.reply_text(f"🗑️ `{path}` deleted successfully.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ {github_error(r)}")
    except requests.RequestException as e:
        await update.message.reply_text(f"❌ GitHub connection failed: {e}")
    return ConversationHandler.END


async def set_visibility(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    if len(context.args) != 2 or context.args[1].lower() not in ("public", "private"):
        await update.message.reply_text("Usage: /visibility owner/repo public|private")
        return ConversationHandler.END
    repo = parse_repo(context.args[0])
    if not repo:
        await update.message.reply_text("❌ Format: owner/repo")
        return ConversationHandler.END
    try:
        r = requests.patch(f"{GITHUB_API}/repos/{repo[0]}/{repo[1]}", headers=github_headers(), json={"private": context.args[1].lower() == "private"}, timeout=15)
        if r.ok:
            await update.message.reply_text(f"✅ `{repo[0]}/{repo[1]}` is now {context.args[1].lower()}.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ {github_error(r)}")
    except requests.RequestException as e:
        await update.message.reply_text(f"❌ GitHub connection failed: {e}")
    return ConversationHandler.END


async def github_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    await update.message.reply_text(
        "🐙 GitHub Manager\n\n"
        "/repos — list repositories\n"
        "/create_repo name [private|public] — create repo\n"
        "/delete_repo owner/repo CONFIRM — delete repo\n"
        "/repo_info owner/repo — repo details\n"
        "/browse owner/repo [folder] — browse files\n"
        "/view_file owner/repo path — view file\n"
        "/edit_file owner/repo path — replace file content\n"
        "/delete_file owner/repo path CONFIRM — delete file\n"
        "/visibility owner/repo public|private — change visibility\n\n"
        "📦 Send a ZIP normally to upload/update an entire project."
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("প্রসেস বাতিল করা হয়েছে। ❎")
    return ConversationHandler.END

def main():
    server_thread = Thread(target=run_flask)
    server_thread.daemon = True
    server_thread.start()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # GitHub management commands are added only once here.
    # Existing ZIP upload/license flow remains unchanged.
    github_commands = [
        ("github", github_help), ("repos", repos), ("create_repo", create_repo),
        ("delete_repo", delete_repo), ("repo_info", repo_info), ("browse", browse_repo),
        ("view_file", view_file), ("edit_file", edit_file_start), ("delete_file", delete_file),
        ("visibility", set_visibility),
    ]
    for command, handler in github_commands:
        app.add_handler(CommandHandler(command, handler))

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
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_repo_info),
            ],
            WAITING_FOR_EDIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_file_finish),
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
