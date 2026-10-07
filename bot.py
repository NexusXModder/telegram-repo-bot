import os
import zipfile
import shutil
import base64
import requests
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler
)

# Flask Keep-Alive Server
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

LICENSE_API_URL = os.environ.get("LICENSE_API_URL", "https://nexus-license.onrender.com")
CLIENT_ID = os.environ.get("CLIENT_ID", "default_client")

WAITING_FOR_LICENSE, WAITING_FOR_ZIP, WAITING_FOR_REPO = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('is_verified'):
        await update.message.reply_text("✅ You are already logged in.\n\nSend your ZIP file to upload, or use /github for GitHub Manager.")
        return WAITING_FOR_ZIP

    await update.message.reply_text("🔐 Ei bot-ti use korar jonno apnar License Key-ti din:")
    return WAITING_FOR_LICENSE

async def github_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('is_verified'):
        await update.message.reply_text("🔐 Prothome /start likhe License Key verified korun.")
        return WAITING_FOR_LICENSE
    
    await update.message.reply_text("📦 Send your ZIP file now.\nI will ask for your repository after receiving it.")
    return WAITING_FOR_ZIP

async def verify_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = update.message.text.strip()
    await update.message.reply_text("License key verify kora hocche... ⏳")

    base_domain = LICENSE_API_URL.rstrip('/')
    endpoint = f"{base_domain}/api/v1/licenses/verify"

    payload = {
        "clientId": CLIENT_ID,
        "key": user_key,
        "deviceToken": f"tg_user_{update.effective_user.id}"
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=10)
        
        try:
            res_data = response.json()
        except Exception:
            res_data = {"raw": response.text}

        if response.status_code in [200, 201]:
            context.user_data['is_verified'] = True
            await update.message.reply_text("✅ License Key Verified!\n\nEbar ZIP file-ti pathan.")
            return WAITING_FOR_ZIP
        else:
            error_msg = res_data.get("message") or res_data.get("error") or str(res_data)
            await update.message.reply_text(f"❌ Verification Failed!\n\nServer Response: `{error_msg}`\n\nSothik Key din ba /cancel likhun.", parse_mode="Markdown")
            return WAITING_FOR_LICENSE

    except Exception as e:
        await update.message.reply_text(f"⚠️ Network Error: {str(e)}")
        return WAITING_FOR_LICENSE

async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    
    # Strictly check if document exists and ends with .zip or is a zip file
    if not document or not (document.file_name.endswith('.zip') or 'zip' in document.mime_type.lower()):
        await update.message.reply_text("⚠️ Send a valid ZIP file, or use /github to open the manager.")
        return WAITING_FOR_ZIP

    file = await context.bot.get_file(document.file_id)
    zip_path = f"temp_{document.file_name}"
    await file.download_to_drive(zip_path)
    
    context.user_data['zip_path'] = zip_path
    await update.message.reply_text(
        "📦 ZIP file peyechi!\n\n"
        "Ebar GitHub Repo-r naam ebong Path din.\n"
        "Format: `Username/RepositoryName` ba `Username/RepositoryName/folder`",
        parse_mode="Markdown"
    )
    return WAITING_FOR_REPO

async def invalid_zip_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚠️ Send a ZIP file, or use /github to open the manager.")
    return WAITING_FOR_ZIP

async def handle_repo_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip().strip('/')
    parts = user_input.split('/')

    if len(parts) < 2:
        await update.message.reply_text("Bhul format! Sothik format: `Username/RepositoryName`")
        return WAITING_FOR_REPO

    repo_fullname = f"{parts[0]}/{parts[1]}"
    target_path = "/".join(parts[2:]) if len(parts) > 2 else ""

    zip_path = context.user_data.get('zip_path')
    extract_dir = "extracted_files"

    await update.message.reply_text("GitHub-e file upload shuru hocche... ⏳")

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        for root, _, files in os.walk(extract_dir):
            for file_name in files:
                local_file_path = os.path.join(root, file_name)
                relative_path = os.path.relpath(local_file_path, extract_dir).replace("\\", "/")
                
                github_file_path = f"{target_path}/{relative_path}" if target_path else relative_path
                url = f"https://api.github.com/repos/{repo_fullname}/contents/{github_file_path}"

                with open(local_file_path, 'rb') as f:
                    content_encoded = base64.b64encode(f.read()).decode('utf-8')

                get_response = requests.get(url, headers=headers)
                data = {
                    "message": f"Upload {github_file_path} via Bot",
                    "content": content_encoded
                }

                if get_response.status_code == 200:
                    data['sha'] = get_response.json().get('sha')

                put_response = requests.put(url, headers=headers, json=data)

                if put_response.status_code not in [200, 201]:
                    raise Exception(f"Failed to upload {github_file_path}: {put_response.json().get('message')}")

        await update.message.reply_text(f"Shofolbhabe `{repo_fullname}`-e shob file upload hoye geche! ✅")

    except Exception as e:
        await update.message.reply_text(f"Shomoshya hoyeche: {str(e)}")

    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)

    return WAITING_FOR_ZIP

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Batil kora hoyeche.")
    return ConversationHandler.END

def main():
    server_thread = Thread(target=run_flask)
    server_thread.daemon = True
    server_thread.start()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('github', github_cmd)
        ],
        states={
            WAITING_FOR_LICENSE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, verify_license)
            ],
            WAITING_FOR_ZIP: [
                MessageHandler(filters.ATTACHMENT | filters.Document.ALL, handle_zip),
                MessageHandler(filters.TEXT & ~filters.COMMAND, invalid_zip_input)
            ],
            WAITING_FOR_REPO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_repo_info)
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CommandHandler('github', github_cmd),
            CommandHandler('start', start)
        ],
        allow_reentry=True
    )

    app.add_handler(conv_handler)
    print("Bot running...✅")
    app.run_polling()

if __name__ == '__main__':
    main()
