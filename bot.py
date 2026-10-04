import os
import zipfile
import shutil
import base64
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# Environment variables theke token newa hocche
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

WAITING_FOR_ZIP = 1
WAITING_FOR_REPO = 2

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("স্বাগতম! গিটহাবে আপলোড করার জন্য আপনার প্রজেক্টের ZIP ফাইলটি পাঠান।")
    return WAITING_FOR_ZIP

async def handle_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document:
        await update.message.reply_text("অনুগ্রহ করে একটি ফাইল পাঠান।")
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
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            MessageHandler(filters.Document.ALL, handle_zip)
        ],
        states={
            WAITING_FOR_ZIP: [MessageHandler(filters.Document.ALL, handle_zip)],
            WAITING_FOR_REPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_repo_info)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    app.add_handler(conv_handler)
    print("বট সফলভাবে চালু হয়েছে...✅")
    app.run_polling()

if __name__ == '__main__':
    main()
