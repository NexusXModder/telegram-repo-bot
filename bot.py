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

# Apnar License Server-er API & Client ID
LICENSE_API_URL = os.environ.get("LICENSE_API_URL")
CLIENT_ID = os.environ.get("CLIENT_ID")

WAITING_FOR_LICENSE, WAITING_FOR_ZIP, WAITING_FOR_REPO = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('is_verified'):
        await update.message.reply_text("Apni alrdy verified! গিটহাবে আপলোড করার জন্য আপনার প্রজেক্টের ZIP ফাইলটি পাঠান।")
        return WAITING_FOR_ZIP

    await update.message.reply_text("🔐 Ei bot-ti use korar jonno apnar License Key-ti din:")
    return WAITING_FOR_LICENSE

async def verify_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = update.message.text.strip()
    await update.message.reply_text("License key verify kora hocche... ⏳")

    # License API Call
    payload = {
        "client_id": CLIENT_ID,
        "license_key": user_key
    }

    try:
        response = requests.post(LICENSE_API_URL, json=payload, timeout=10)
        res_data = response.json()

        # Api response check (Status code 200 ebong success/valid status)
        if response.status_code == 200 and (res_data.get("status") == "success" or res_data.get("valid") == True):
            context.user_data['is_verified'] = True
            await update.message.reply_text("✅ License Key Verified!\n\nEbar গিটহাবে আপলোড করার জন্য আপনার প্রজেক্টের ZIP ফাইলটি পাঠান।")
            return WAITING_FOR_ZIP
        else:
            msg = res_data.get("message", "Invalid License Key!")
            await update.message.reply_text(f"❌ Verification Failed: {msg}\nSothik key-ti abar din ba /cancel likhun.")
            return WAITING_FOR_LICENSE

    except Exception as e:
        await update.message.reply_text(f"⚠️ License Server Error: {str(e)}\nAbar chesta korun ba /cancel likhun.")
        return WAITING_FOR_LICENSE

async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document:
        await update.message.reply_text("⚠️ এটি কোনো ফাইল নয়! অনুগ্রহ করে একটি ZIP ফাইল পাঠান।")
        return WAITING_FOR_ZIP

    file = await context.bot.get_file(document.file_id)
    zip_path = f"temp_{document.file_name}"
    await file.download_to_drive(zip_path)
    
    context.user_data['zip_path'] = zip_path
    await update.message.reply_text(
        "ZIP ফাইল পেয়েছি! 📦\n\n"
        "এখন GitHub Repo-র নাম এবং Path দিন।\n"
        "ফরম্যাট: `Username/RepositoryName` অথবা `Username/RepositoryName/folder`"
    )
    return WAITING_FOR_REPO

async def invalid_zip_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚠️ আমি ZIP ফাইলের জন্য অপেক্ষা করছি। অনুগ্রহ করে একটি ZIP ফাইল অ্যাটাচ করে পাঠান। (/cancel লিখুন বাতিল করতে)")
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

    await update.message.reply_text("ফাইল Unzip করা হচ্ছে এবং GitHub-এ আপলোড শুরু হচ্ছে... ⏳")

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

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("প্রসেস বাতিল করা হয়েছে।")
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
    print("বট সফলভাবে চালু হয়েছে...✅")
    app.run_polling()

if __name__ == '__main__':
    main()
