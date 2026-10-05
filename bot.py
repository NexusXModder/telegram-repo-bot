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

# -------------------- Render keep-alive --------------------
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is alive!"


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host="0.0.0.0", port=port)


# -------------------- Environment --------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

LICENSE_API = "https://nexus-license.onrender.com/api/v1/licenses"
CLIENT_ID = "app_15347417c1994c92997e"

# Conversation states
WAITING_FOR_LICENSE = 1
WAITING_FOR_ZIP = 2
WAITING_FOR_REPO = 3
WAITING_FOR_GITHUB_INPUT = 4
REPO_PAGE_SIZE = 8


def github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def license_error_message(response):
    try:
        data = response.json()
    except ValueError:
        return f"License server error (HTTP {response.status_code}). Please try again later."
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or "License authentication failed.")
    return f"License server error (HTTP {response.status_code}). Please try again later."


def get_device_token(context):
    return context.user_data.get("license_device_token")


def github_error(response):
    try:
        data = response.json()
        return data.get("message") or f"GitHub API error (HTTP {response.status_code})."
    except ValueError:
        return f"GitHub API error (HTTP {response.status_code})."


def get_repositories():
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
        if len(batch) < 100 or page >= 20:
            break
        page += 1
    return repos


# -------------------- License --------------------
async def require_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_device_token(context):
        return True
    target = update.effective_message
    if target:
        await target.reply_text("🔐 License required.\n\nPlease send your License Key to continue.")
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if get_device_token(context):
        await update.message.reply_text(
            "✅ You are already logged in.\n\n"
            "Send your ZIP file to upload, or use /github for GitHub Manager."
        )
        return WAITING_FOR_ZIP
    await update.message.reply_text(
        "🔐 Nexus License Login\n\nPlease send your License Key to access this bot."
    )
    return WAITING_FOR_LICENSE


async def handle_license(update: Update, context: ContextTypes.DEFAULT_TYPE):
    license_key = (update.message.text or "").strip()
    if not license_key:
        await update.message.reply_text("⚠️ Please send a valid License Key.")
        return WAITING_FOR_LICENSE

    user_id = update.effective_user.id
    payload = {
        "clientId": CLIENT_ID,
        "licenseKey": license_key,
        "fingerprint": f"telegram:{user_id}",
        "deviceLabel": f"Telegram:{user_id}",
    }
    await update.message.reply_text("🔐 Checking your license...")
    try:
        response = requests.post(f"{LICENSE_API}/activate", json=payload, timeout=15)
        if response.ok:
            data = response.json()
            token = data.get("data", {}).get("deviceToken")
            if not token:
                await update.message.reply_text("⚠️ License server returned an unexpected response.")
                return WAITING_FOR_LICENSE
            context.user_data["license_device_token"] = token
            context.user_data["license_expires_at"] = data.get("data", {}).get("expiresAt")
            await update.message.reply_text(
                "✅ License verified successfully!\n\n"
                "Now send your ZIP file to continue.\n\n"
                "You can also use /github for the GitHub Manager."
            )
            return WAITING_FOR_ZIP
        await update.message.reply_text(f"❌ {license_error_message(response)}")
    except requests.RequestException:
        await update.message.reply_text("❌ Could not connect to the license server. Please try again later.")
    except Exception:
        await update.message.reply_text("❌ An unexpected license error occurred. Please try again later.")
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
            context.user_data.clear()
            await update.message.reply_text("✅ License device deactivated.")
        else:
            await update.message.reply_text(f"❌ {license_error_message(response)}")
    except requests.RequestException:
        await update.message.reply_text("❌ Could not connect to the license server.")
    return ConversationHandler.END


# -------------------- ZIP upload + repo picker --------------------
def repo_picker_keyboard(repos, page=0, action="upload"):
    start = page * REPO_PAGE_SIZE
    page_repos = repos[start:start + REPO_PAGE_SIZE]
    keyboard = []
    for index, repo in enumerate(page_repos, start=start):
        lock = "🔒" if repo.get("private") else "🌐"
        name = repo.get("full_name") or repo.get("name") or "Unknown repo"
        keyboard.append([InlineKeyboardButton(f"{lock} {name}", callback_data=f"pick:{action}:{index}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page:{action}:{page - 1}"))
    if start + REPO_PAGE_SIZE < len(repos):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"page:{action}:{page + 1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh:{action}"),
        InlineKeyboardButton("❌ Cancel", callback_data="gh_cancel"),
    ])
    return InlineKeyboardMarkup(keyboard)


async def show_repo_picker(update: Update, context: ContextTypes.DEFAULT_TYPE, action="upload", page=0, edit=False):
    if not GITHUB_TOKEN:
        text = "❌ GITHUB_TOKEN is not configured on the server."
    else:
        try:
            repos = context.user_data.get("github_repos")
            if repos is None or page == -1:
                repos = get_repositories()
                context.user_data["github_repos"] = repos
                page = 0
            if not repos:
                text = "📂 No GitHub repositories were found for this token."
            else:
                labels = {
                    "upload": "📦 Choose where to upload the ZIP:",
                    "info": "ℹ️ Choose a repository:",
                    "browse": "📂 Choose a repository to browse:",
                    "view": "👁️ Choose a repository:",
                    "edit": "✏️ Choose a repository to edit:",
                    "delete_file": "🗑️ Choose a repository:",
                    "delete_repo": "🗑️ Choose the repository to delete:",
                    "visibility": "🔐 Choose a repository:",
                }
                total_pages = (len(repos) + REPO_PAGE_SIZE - 1) // REPO_PAGE_SIZE
                text = f"{labels.get(action, '📁 Choose a repository:')}\n\nPage {page + 1}/{total_pages} • {len(repos)} repositories"
                markup = repo_picker_keyboard(repos, page, action)
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
    if not document or not (document.file_name or "").lower().endswith(".zip"):
        await update.message.reply_text("⚠️ Please send a ZIP file (.zip).")
        return WAITING_FOR_ZIP
    file = await context.bot.get_file(document.file_id)
    zip_path = f"temp_{update.effective_user.id}_{document.file_name}"
    await file.download_to_drive(zip_path)
    context.user_data["zip_path"] = zip_path
    context.user_data["github_repos"] = None
    return await show_repo_picker(update, context, "upload")


async def invalid_zip_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚠️ Send a ZIP file, or use /github to open the manager.")
    return WAITING_FOR_ZIP


async def upload_selected_repo(update: Update, context: ContextTypes.DEFAULT_TYPE, repo):
    query = update.callback_query
    zip_path = context.user_data.get("zip_path")
    if not zip_path or not os.path.exists(zip_path):
        await query.edit_message_text("❌ ZIP file is no longer available. Please send it again.")
        return WAITING_FOR_ZIP
    repo_fullname = repo.get("full_name")
    await query.edit_message_text(f"📤 Uploading to `{repo_fullname}`...\n\nPlease wait ⏳", parse_mode="Markdown")
    extract_dir = f"extracted_{update.effective_user.id}"
    uploaded = skipped = 0
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            bad = zip_ref.testzip()
            if bad:
                raise RuntimeError(f"The ZIP file is corrupted near: {bad}")
            zip_ref.extractall(extract_dir)
        headers = github_headers()
        for root, _, files in os.walk(extract_dir):
            for file_name in files:
                local = os.path.join(root, file_name)
                relative = os.path.relpath(local, extract_dir).replace("\\", "/")
                parts = relative.split("/")
                if "__pycache__" in parts or file_name.endswith((".pyc", ".pyo")) or ".git" in parts:
                    skipped += 1
                    continue
                url = f"https://api.github.com/repos/{repo_fullname}/contents/{relative}"
                with open(local, "rb") as f:
                    encoded = base64.b64encode(f.read()).decode()
                check = requests.get(url, headers=headers, timeout=20)
                data = {"message": f"Upload/Update {relative} via Telegram Bot", "content": encoded}
                if check.status_code == 200:
                    sha = check.json().get("sha")
                    if sha:
                        data["sha"] = sha
                elif check.status_code != 404:
                    raise RuntimeError(f"Could not check {relative}: {github_error(check)}")
                put = requests.put(url, headers=headers, json=data, timeout=30)
                if put.status_code not in (200, 201):
                    raise RuntimeError(f"Failed to upload {relative}: {github_error(put)}")
                uploaded += 1
        await query.edit_message_text(
            f"✅ Upload complete!\n\n📦 Repository: `{repo_fullname}`\n📄 Files uploaded/updated: {uploaded}\n⏭️ Cache files skipped: {skipped}",
            parse_mode="Markdown",
        )
    except zipfile.BadZipFile:
        await query.edit_message_text("❌ Invalid or corrupted ZIP file.")
    except requests.RequestException:
        await query.edit_message_text("❌ Could not connect to GitHub while uploading.")
    except Exception as exc:
        await query.edit_message_text(f"❌ Upload failed:\n\n{exc}")
    finally:
        if os.path.exists(zip_path): os.remove(zip_path)
        if os.path.exists(extract_dir): shutil.rmtree(extract_dir)
        context.user_data.pop("zip_path", None)
        context.user_data.pop("github_repos", None)
    return WAITING_FOR_ZIP


# -------------------- GitHub manager UI --------------------
def github_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Upload ZIP", callback_data="gh_upload")],
        [InlineKeyboardButton("📚 Repositories", callback_data="gh_repos")],
        [InlineKeyboardButton("➕ Create Repo", callback_data="gh_create")],
        [InlineKeyboardButton("ℹ️ Repo Info", callback_data="gh_info"), InlineKeyboardButton("📂 Browse", callback_data="gh_browse")],
        [InlineKeyboardButton("👁 View File", callback_data="gh_view"), InlineKeyboardButton("✏️ Edit File", callback_data="gh_edit")],
        [InlineKeyboardButton("🗑 Delete File", callback_data="gh_delete_file"), InlineKeyboardButton("🗑 Delete Repo", callback_data="gh_delete_repo")],
        [InlineKeyboardButton("🔐 Public / Private", callback_data="gh_visibility")],
        [InlineKeyboardButton("❌ Close", callback_data="gh_cancel")],
    ])


async def github_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    await update.effective_message.reply_text("🐙 GitHub Manager\n\nChoose an action:", reply_markup=github_menu_markup())
    return WAITING_FOR_REPO


async def list_repos_message(update, context):
    try:
        repos = get_repositories()
        context.user_data["github_repos"] = repos
        if not repos:
            await update.effective_message.reply_text("📂 No repositories found.")
            return
        lines = ["📚 Your GitHub repositories:\n"]
        for r in repos:
            lines.append(f"{'🔒' if r.get('private') else '🌐'} {r.get('full_name')}")
        # Telegram message limit safety
        text = "\n".join(lines)
        if len(text) > 3900:
            text = text[:3900] + "\n…"
        await update.effective_message.reply_text(text)
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Could not load repositories.\n\n{exc}")


async def github_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "gh_cancel":
        await query.edit_message_text("❎ GitHub action cancelled.")
        context.user_data.pop("github_mode", None)
        return WAITING_FOR_ZIP

    if data == "gh_repos":
        try:
            repos = get_repositories()
            context.user_data["github_repos"] = repos
            if not repos:
                await query.edit_message_text("📂 No repositories found.")
            else:
                lines = ["📚 Your GitHub repositories:\n"]
                for r in repos:
                    lines.append(f"{'🔒' if r.get('private') else '🌐'} {r.get('full_name')}")
                text = "\n".join(lines)
                await query.edit_message_text(text[:4000])
        except Exception as exc:
            await query.edit_message_text(f"❌ Could not load repositories.\n\n{exc}")
        return WAITING_FOR_REPO

    if data == "gh_upload":
        await query.edit_message_text("📦 Send your ZIP file now.\n\nI will show your repositories as buttons after receiving it.")
        return WAITING_FOR_ZIP

    if data == "gh_create":
        context.user_data["github_mode"] = "create_name"
        await query.edit_message_text("➕ Send the new repository name.\n\nExample: `my-project`", parse_mode="Markdown")
        return WAITING_FOR_GITHUB_INPUT

    action_map = {
        "gh_info": "info", "gh_browse": "browse", "gh_view": "view",
        "gh_edit": "edit", "gh_delete_file": "delete_file",
        "gh_delete_repo": "delete_repo", "gh_visibility": "visibility",
    }
    if data in action_map:
        return await show_repo_picker(update, context, action_map[data], 0, True)

    if data.startswith("page:"):
        _, action, page = data.split(":", 2)
        return await show_repo_picker(update, context, action, int(page), True)

    if data.startswith("refresh:"):
        action = data.split(":", 1)[1]
        return await show_repo_picker(update, context, action, -1, True)

    if data.startswith("pick:"):
        _, action, index_text = data.split(":", 2)
        try:
            repo = (context.user_data.get("github_repos") or [])[int(index_text)]
        except (ValueError, IndexError, TypeError):
            await query.edit_message_text("❌ Repository selection expired. Please open /github again.")
            return WAITING_FOR_REPO

        full_name = repo.get("full_name")
        if action == "upload":
            return await upload_selected_repo(update, context, repo)
        if action == "info":
            try:
                r = requests.get(f"https://api.github.com/repos/{full_name}", headers=github_headers(), timeout=20)
                if r.status_code != 200: raise RuntimeError(github_error(r))
                d = r.json()
                await query.edit_message_text(
                    f"ℹ️ *{d.get('full_name')}*\n\n"
                    f"Visibility: {'Private 🔒' if d.get('private') else 'Public 🌐'}\n"
                    f"Default branch: `{d.get('default_branch')}`\n"
                    f"Stars: {d.get('stargazers_count', 0)}\n"
                    f"Forks: {d.get('forks_count', 0)}\n"
                    f"Open issues: {d.get('open_issues_count', 0)}",
                    parse_mode="Markdown",
                )
            except Exception as exc:
                await query.edit_message_text(f"❌ {exc}")
            return WAITING_FOR_REPO
        if action == "visibility":
            context.user_data["selected_repo"] = full_name
            await query.edit_message_text(
                f"🔐 `{full_name}`\n\nChoose new visibility:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌐 Public", callback_data="setvis:public"), InlineKeyboardButton("🔒 Private", callback_data="setvis:private")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="gh_cancel")],
                ]), parse_mode="Markdown")
            return WAITING_FOR_REPO
        if action == "delete_repo":
            context.user_data["selected_repo"] = full_name
            await query.edit_message_text(
                f"⚠️ Delete `{full_name}` permanently?\n\nThis cannot be undone.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗑 YES, DELETE", callback_data="confirm:delete_repo"), InlineKeyboardButton("❌ Cancel", callback_data="gh_cancel")]
                ]), parse_mode="Markdown")
            return WAITING_FOR_REPO
        if action in ("view", "edit", "delete_file", "browse"):
            context.user_data["selected_repo"] = full_name
            if action == "browse":
                return await browse_root(update, context, full_name)
            prompts = {
                "view": "👁 Send the file path.\n\nExample: `bot.py`",
                "edit": "✏️ Send the file path.\n\nExample: `bot.py`",
                "delete_file": "🗑 Send the file path to delete.\n\nExample: `old.py`",
            }
            context.user_data["github_mode"] = action + "_path"
            await query.edit_message_text(prompts[action], parse_mode="Markdown")
            return WAITING_FOR_GITHUB_INPUT

    if data.startswith("setvis:"):
        repo = context.user_data.get("selected_repo")
        if not repo:
            await query.edit_message_text("❌ No repository selected.")
            return WAITING_FOR_REPO
        private = data.endswith(":private")
        try:
            r = requests.patch(f"https://api.github.com/repos/{repo}", headers=github_headers(), json={"private": private}, timeout=20)
            if r.status_code != 200: raise RuntimeError(github_error(r))
            await query.edit_message_text(f"✅ `{repo}` is now {'Private 🔒' if private else 'Public 🌐'}.", parse_mode="Markdown")
        except Exception as exc:
            await query.edit_message_text(f"❌ {exc}")
        return WAITING_FOR_REPO

    if data == "confirm:delete_repo":
        repo = context.user_data.get("selected_repo")
        try:
            r = requests.delete(f"https://api.github.com/repos/{repo}", headers=github_headers(), timeout=20)
            if r.status_code != 204: raise RuntimeError(github_error(r))
            await query.edit_message_text(f"✅ Repository `{repo}` deleted.", parse_mode="Markdown")
        except Exception as exc:
            await query.edit_message_text(f"❌ {exc}")
        context.user_data.pop("selected_repo", None)
        return WAITING_FOR_REPO

    if data.startswith("browse:"):
        return await browse_path_callback(update, context)

    if data.startswith("confirm_delete_file:"):
        return await confirm_delete_file(update, context)

    return WAITING_FOR_REPO


async def browse_root(update, context, repo):
    return await render_browse(update, context, repo, "")


async def render_browse(update, context, repo, path):
    query = update.callback_query
    try:
        url = f"https://api.github.com/repos/{repo}/contents/{path}" if path else f"https://api.github.com/repos/{repo}/contents"
        r = requests.get(url, headers=github_headers(), timeout=20)
        if r.status_code != 200: raise RuntimeError(github_error(r))
        items = r.json()
        if isinstance(items, dict): items = [items]
        buttons = []
        for item in items[:40]:
            icon = "📁" if item.get("type") == "dir" else "📄"
            buttons.append([InlineKeyboardButton(f"{icon} {item.get('name')}", callback_data=f"browse:{repo}:{item.get('path')}")])
        if path:
            parent = "/".join(path.split("/")[:-1])
            buttons.append([InlineKeyboardButton("⬆️ Parent", callback_data=f"browse:{repo}:{parent}")])
        buttons.append([InlineKeyboardButton("❌ Close", callback_data="gh_cancel")])
        await query.edit_message_text(f"📂 `{repo}`\n\nPath: `{path or '/'}`", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    except Exception as exc:
        await query.edit_message_text(f"❌ Browse failed:\n\n{exc}")
    return WAITING_FOR_REPO


async def browse_path_callback(update, context):
    query = update.callback_query
    await query.answer()
    _, repo, path = query.data.split(":", 2)
    return await render_browse(update, context, repo, path)


async def github_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    mode = context.user_data.get("github_mode")
    text = (update.message.text or "").strip()
    if not mode:
        return WAITING_FOR_GITHUB_INPUT

    if mode == "create_name":
        if not text or len(text) > 100:
            await update.message.reply_text("⚠️ Please send a valid repository name.")
            return WAITING_FOR_GITHUB_INPUT
        context.user_data["create_repo_name"] = text
        context.user_data["github_mode"] = "create_visibility"
        await update.message.reply_text("Choose visibility:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Public", callback_data="createvis:public"), InlineKeyboardButton("🔒 Private", callback_data="createvis:private")],
            [InlineKeyboardButton("❌ Cancel", callback_data="gh_cancel")],
        ]))
        return WAITING_FOR_REPO

    if mode in ("view_path", "edit_path", "delete_file_path"):
        repo = context.user_data.get("selected_repo")
        if not repo:
            await update.message.reply_text("❌ No repository selected.")
            return WAITING_FOR_REPO
        path = text.lstrip("/")
        if mode == "view_path":
            try:
                r = requests.get(f"https://api.github.com/repos/{repo}/contents/{path}", headers=github_headers(), timeout=20)
                if r.status_code != 200: raise RuntimeError(github_error(r))
                d = r.json()
                if d.get("type") != "file": raise RuntimeError("That path is not a file.")
                content = base64.b64decode(d.get("content", "")).decode("utf-8", errors="replace")
                if len(content) > 3800: content = content[:3800] + "\n…[truncated]"
                await update.message.reply_text(f"👁 `{repo}/{path}`\n\n```\n{content}\n```", parse_mode="Markdown")
            except Exception as exc:
                await update.message.reply_text(f"❌ Could not read file:\n\n{exc}")
            context.user_data.pop("github_mode", None)
            return WAITING_FOR_REPO
        if mode == "edit_path":
            context.user_data["edit_path"] = path
            context.user_data["github_mode"] = "edit_content"
            await update.message.reply_text("✏️ Send the **new complete file content** now.", parse_mode="Markdown")
            return WAITING_FOR_GITHUB_INPUT
        if mode == "delete_file_path":
            context.user_data["delete_file_path"] = path
            context.user_data["github_mode"] = None
            await update.message.reply_text(
                f"⚠️ Delete `{repo}/{path}`?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🗑 YES, DELETE", callback_data="confirm_delete_file:yes"), InlineKeyboardButton("❌ Cancel", callback_data="gh_cancel")]
                ]), parse_mode="Markdown")
            return WAITING_FOR_REPO

    if mode == "edit_content":
        repo = context.user_data.get("selected_repo")
        path = context.user_data.get("edit_path")
        try:
            old = requests.get(f"https://api.github.com/repos/{repo}/contents/{path}", headers=github_headers(), timeout=20)
            if old.status_code != 200: raise RuntimeError(github_error(old))
            sha = old.json().get("sha")
            payload = {"message": f"Edit {path} via Telegram Bot", "content": base64.b64encode(text.encode()).decode(), "sha": sha}
            r = requests.put(f"https://api.github.com/repos/{repo}/contents/{path}", headers=github_headers(), json=payload, timeout=30)
            if r.status_code not in (200, 201): raise RuntimeError(github_error(r))
            await update.message.reply_text(f"✅ `{repo}/{path}` updated successfully.", parse_mode="Markdown")
        except Exception as exc:
            await update.message.reply_text(f"❌ Edit failed:\n\n{exc}")
        for k in ("github_mode", "edit_path", "selected_repo"): context.user_data.pop(k, None)
        return WAITING_FOR_REPO

    return WAITING_FOR_GITHUB_INPUT


async def create_visibility_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = context.user_data.get("create_repo_name")
    if not name:
        await query.edit_message_text("❌ Repository name expired. Try /github again.")
        return WAITING_FOR_REPO
    private = query.data.endswith(":private")
    try:
        r = requests.post("https://api.github.com/user/repos", headers=github_headers(), json={"name": name, "private": private}, timeout=20)
        if r.status_code not in (201,): raise RuntimeError(github_error(r))
        await query.edit_message_text(f"✅ Repository `{r.json().get('full_name', name)}` created.", parse_mode="Markdown")
    except Exception as exc:
        await query.edit_message_text(f"❌ Could not create repository:\n\n{exc}")
    context.user_data.pop("create_repo_name", None)
    context.user_data.pop("github_mode", None)
    return WAITING_FOR_REPO


async def confirm_delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    repo = context.user_data.get("selected_repo")
    path = context.user_data.get("delete_file_path")
    try:
        old = requests.get(f"https://api.github.com/repos/{repo}/contents/{path}", headers=github_headers(), timeout=20)
        if old.status_code != 200: raise RuntimeError(github_error(old))
        sha = old.json().get("sha")
        r = requests.delete(
            f"https://api.github.com/repos/{repo}/contents/{path}",
            headers=github_headers(),
            json={"message": f"Delete {path} via Telegram Bot", "sha": sha},
            timeout=20,
        )
        if r.status_code != 200: raise RuntimeError(github_error(r))
        await query.edit_message_text(f"✅ Deleted `{repo}/{path}`.", parse_mode="Markdown")
    except Exception as exc:
        await query.edit_message_text(f"❌ Delete failed:\n\n{exc}")
    for k in ("selected_repo", "delete_file_path", "github_mode"): context.user_data.pop(k, None)
    return WAITING_FOR_REPO


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    zip_path = context.user_data.pop("zip_path", None)
    if zip_path and os.path.exists(zip_path): os.remove(zip_path)
    for k in ("github_repos", "github_mode", "selected_repo", "create_repo_name", "edit_path", "delete_file_path"):
        context.user_data.pop(k, None)
    await update.message.reply_text("প্রসেস বাতিল করা হয়েছে। ❎")
    return WAITING_FOR_ZIP if get_device_token(context) else WAITING_FOR_LICENSE


async def cmd_repos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    await list_repos_message(update, context)
    return WAITING_FOR_REPO


async def cmd_create_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    context.user_data["github_mode"] = "create_name"
    await update.effective_message.reply_text("➕ Send the new repository name.\n\nExample: `my-project`", parse_mode="Markdown")
    return WAITING_FOR_GITHUB_INPUT


async def cmd_repo_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    if not await require_license(update, context):
        return WAITING_FOR_LICENSE
    return await show_repo_picker(update, context, action, 0, False)


async def cmd_repo_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await cmd_repo_action(update, context, "info")


async def cmd_browse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await cmd_repo_action(update, context, "browse")


async def cmd_view_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await cmd_repo_action(update, context, "view")


async def cmd_edit_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await cmd_repo_action(update, context, "edit")


async def cmd_delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await cmd_repo_action(update, context, "delete_file")


async def cmd_delete_repo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await cmd_repo_action(update, context, "delete_repo")


async def cmd_visibility(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await cmd_repo_action(update, context, "visibility")


# -------------------- Main --------------------
def main():
    server_thread = Thread(target=run_flask, daemon=True)
    server_thread.start()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("login", start),
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
                CallbackQueryHandler(github_callback, pattern=r"^(gh_|page:|refresh:|pick:|setvis:|confirm:|browse:|confirm_delete_file:).*"),
                CallbackQueryHandler(create_visibility_callback, pattern=r"^createvis:(public|private)$"),
                MessageHandler(filters.Document.ALL, handle_zip),
                MessageHandler(filters.TEXT & ~filters.COMMAND, invalid_zip_input),
            ],
            WAITING_FOR_GITHUB_INPUT: [
                CallbackQueryHandler(github_callback, pattern=r"^(gh_|page:|refresh:|pick:|setvis:|confirm:|browse:|confirm_delete_file:).*"),
                CallbackQueryHandler(create_visibility_callback, pattern=r"^createvis:(public|private)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, github_text_input),
            ],
        },
        fallbacks=[
            CommandHandler("github", github_menu),
            CommandHandler("repos", cmd_repos),
            CommandHandler("create_repo", cmd_create_repo),
            CommandHandler("repo_info", cmd_repo_info),
            CommandHandler("browse", cmd_browse),
            CommandHandler("view_file", cmd_view_file),
            CommandHandler("edit_file", cmd_edit_file),
            CommandHandler("delete_file", cmd_delete_file),
            CommandHandler("delete_repo", cmd_delete_repo),
            CommandHandler("visibility", cmd_visibility),
            CommandHandler("cancel", cancel),
            CommandHandler("logout", logout),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)

    # These commands are available even when the conversation is not active.
    app.add_handler(CommandHandler("github", github_menu))
    app.add_handler(CommandHandler("repos", cmd_repos))
    app.add_handler(CommandHandler("create_repo", cmd_create_repo))
    app.add_handler(CommandHandler("repo_info", cmd_repo_info))
    app.add_handler(CommandHandler("browse", cmd_browse))
    app.add_handler(CommandHandler("view_file", cmd_view_file))
    app.add_handler(CommandHandler("edit_file", cmd_edit_file))
    app.add_handler(CommandHandler("delete_file", cmd_delete_file))
    app.add_handler(CommandHandler("delete_repo", cmd_delete_repo))
    app.add_handler(CommandHandler("visibility", cmd_visibility))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("logout", logout))

    print("বট সফলভাবে চালু হয়েছে...✅")
    app.run_polling()


if __name__ == "__main__":
    main()
