import os
import zipfile
import shutil
import base64
import requests
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host='0.0.0.0', port=port)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

# Render Variables
LICENSE_API_URL = os.environ.get("LICENSE_API_URL", "https://nexus-license.onrender.com")
CLIENT_ID = os.environ.get("CLIENT_ID", "default_client")

WAITING_FOR_LICENSE, WAITING_FOR_ZIP, WAITING_FOR_REPO = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('is_verified'):
        await update.message.reply_text("আপনি ইতোমধ্যে ভেরিফাইড! ZIP ফাইলটি পাঠান।")
        return WAITING_FOR_ZIP

    await update.message.reply_text("🔐 আপনার License Key-টি দিন:")
    return WAITING_FOR_LICENSE

async def verify_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = update.message.text.strip()
    await update.message.reply_text("License key verify করা হচ্ছে... ⏳")

    # Direct Route
    base_domain = LICENSE_API_URL.rstrip('/')
    endpoint = f"{base_domain}/api/v1/public/verify"

    # Exact Schema
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
            await update.message.reply_text("✅ License Key Verified!\n\nএখন GitHub-এ আপলোড করার জন্য ZIP ফাইলটি পাঠান।")
            return WAITING_FOR_ZIP
        else:
            error_msg = res_data.get("message") or res_data.get("error") or str(res_data)
            await update.message.reply_text(f"❌ Verification Failed!\n\nServer Response: `{error_msg}`\n\nসঠিক Key দিন বা /cancel লিখুন।", parse_mode="Markdown")
            return WAITING_FOR_LICENSE

    except Exception as e:
        await update.message.reply_text(f"⚠️ Network Error: {str(e)}")
        return WAITING_FOR_LICENSE

async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document:
        await update.message.reply_text("⚠️ অনুগ্রহ করে একটি ZIP ফাইল পাঠান।")
        return WAITING_FOR_ZIP

    file = await context.bot.get_file(document.file_id)
    zip_path = f"temp_{document.file_name}"
    await file.download_to_drive(zip_path)
    
    context.user_data['zip_path'] = zip_path
    await update.message.reply_text(
        "ZIP ফাইল পেয়েছি! 📦\n\n"
        "এখন GitHub Repo-র নাম এবং Path দিন।\n"
        "ফরম্যাট: `Username/RepositoryName`"
    )
    return WAITING_FOR_REPO

async def invalid_zip_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚠️ একটি ZIP ফাইল পাঠাতে হবে। (/cancel লিখুন বাতিল করতে)")
    return WAITING_FOR_ZIP

async def handle_repo_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip().strip('/')
    parts = user_input.split('/')

    if len(parts) < 2:
        await update.message.reply_text("ভুল ফরম্যাট! সঠিক ফরম্যাট: `Username/RepositoryName`")
        return WAITING_FOR_REPO

    repo_fullname = f"{parts[0]}/{parts[1]}"
    target_path = "/".join(parts[2:]) if len(parts) > 2 else ""

    zip_path = context.user_data.get('zip_path')
    extract_dir = "extracted_files"

    await update.message.reply_text("GitHub-এ ফাইল আপলোড শুরু হচ্ছে... ⏳")

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

        await update.message.reply_text(f"সফলভাবে `{repo_fullname}`-এ সব ফাইল আপলোড হয়ে গেছে! ✅")

    except Exception as e:
        await update.message.reply_text(f"সমস্যা হয়েছে: {str(e)}")

    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        if os.path.exists(extract_dir):
            shutil.rmtree(extract_dir)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("বাতিল করা হয়েছে।")
    return ConversationHandler.END

def main():
    server_thread = Thread(target=run_flask)
    server_thread.daemon = True
    server_thread.start()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            WAITING_FOR_LICENSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, verify_license)],
            WAITING_FOR_ZIP: [
                MessageHandler(filters.Document.ALL, handle_zip),
                MessageHandler(filters.TEXT & ~filters.COMMAND, invalid_zip_input)
            ],
            WAITING_FOR_REPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_repo_info)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler)
    print("বট রানিং...✅")
    app.run_polling()

if __name__ == '__main__':
    main()
