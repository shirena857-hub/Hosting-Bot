"""
╔═══════════════════════════════════════════════════════════╗
║  🌟 APON HOSTING PANEL — Premium Edition v6.0 🌟         ║
║  Developer: @developer_apon                               ║
║  Main Entry Point                                         ║
╚═══════════════════════════════════════════════════════════╝
"""

import telebot, subprocess, os, zipfile, tempfile, shutil, time
import logging, signal, threading, re, sys, atexit
import requests, traceback
from telebot import types
from datetime import datetime, timedelta
from flask import Flask, jsonify
from threading import Thread

# ─── Project modules ───
from config import (
    TOKEN, OWNER_ID, ADMIN_ID, BOT_USERNAME, YOUR_USERNAME,
    UPDATE_CHANNEL, BRAND, BRAND_VER, BRAND_TAG, BRAND_FOOTER,
    PLAN_LIMITS, PAYMENT_METHODS, DEFAULT_FORCE_CHANNELS,
    FLASK_PORT, LOGS_DIR, UPLOAD_DIR, BACKUP_DIR, REF_BONUS_DAYS, REF_COMMISSION
)
from database import db
from core.state import state, bot_scripts
from core.runner import (
    det, run_bot_script, thread_monitor, thread_backup,
    thread_expiry, thread_storage_monitor, thread_daily_report,
    thread_free_bot_limit, thread_main_bot_restart,
    init_runner
)
from handlers.bot_safe import (
    init_safe, safe_send, safe_edit, safe_delete,
    safe_answer, safe_reply, forward_error, forward_crash
)
from handlers.keyboards import (
    init_keyboards, main_menu_kb, help_menu_kb, back_btn, back_help_btn,
    bot_action_kb, plan_kb, pay_method_kb, admin_kb, pay_approve_kb,
    channels_manage_kb
)
from utils.helpers import (
    get_uptime, fmt_size, gen_ref_code, time_left, user_folder,
    is_running, bot_running, cleanup_script, kill_tree, bot_res,
    sys_stats, rate_check
)

# ═══════════════════════════════════════════════════
#  LOGGING SETUP
# ═══════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, 'apon.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('APON')

# ═══════════════════════════════════════════════════
#  BOT INITIALIZATION
# ═══════════════════════════════════════════════════
bot = telebot.TeleBot(
    TOKEN, parse_mode='HTML', threaded=True, num_threads=32,
    use_class_middlewares=True   # needed for the private-chat-only guard below
)

# Initialize all modules
init_safe(bot, OWNER_ID)
init_keyboards(db)
init_runner(bot, db, forward_error, safe_send)

# ═══════════════════════════════════════════════════
#  PRIVATE-CHAT-ONLY GUARD (personal use only)
#  Blocks every command/text/callback from groups &
#  supergroups so the bot never talks in a group chat.
#  If someone adds the bot to a group, it auto-leaves.
# ═══════════════════════════════════════════════════
from telebot.handler_backends import BaseMiddleware, CancelUpdate

class PrivateOnlyMiddleware(BaseMiddleware):
    """Silently drops any update that isn't from a 1-to-1 (private) chat.
    Doesn't touch any existing handler — runs before them."""
    def __init__(self):
        self.update_types = ['message', 'callback_query']

    def pre_process(self, update_obj, data):
        chat = getattr(update_obj, 'chat', None) or getattr(getattr(update_obj, 'message', None), 'chat', None)
        if chat is not None and chat.type != 'private':
            return CancelUpdate()

    def post_process(self, update_obj, data, exception=None):
        pass

bot.setup_middleware(PrivateOnlyMiddleware())


@bot.message_handler(content_types=['new_chat_members'])
def on_added_to_group(msg):
    """If the bot itself gets added to any group, leave immediately —
    this bot is for personal (private chat) use only."""
    try:
        bot_id = bot.get_me().id
        added_ids = [m.id for m in (msg.new_chat_members or [])]
        if bot_id in added_ids:
            try:
                bot.send_message(
                    msg.chat.id,
                    f"⚠️ This bot is for <b>personal use only</b> and doesn't operate in groups.\n"
                    f"{BRAND_FOOTER}"
                )
            except Exception:
                pass
            bot.leave_chat(msg.chat.id)
    except Exception as e:
        logger.warning(f"on_added_to_group: {e}")

# ═══════════════════════════════════════════════════
#  FLASK KEEP-ALIVE
# ═══════════════════════════════════════════════════
flask_app = Flask('AponHosting')

@flask_app.route('/')
def flask_home():
    return "<h1>🌟 SB HOSTING PANEL 🌟</h1><p>Status: ✅ Online</p>"

@flask_app.route('/health')
def flask_health():
    return jsonify({
        "status": "ok",
        "uptime": get_uptime(),
        "version": "6.0",
        "running_bots": len([k for k in bot_scripts if is_running(k)])
    })

def keep_alive():
    Thread(
        target=lambda: flask_app.run(host='0.0.0.0', port=FLASK_PORT),
        daemon=True
    ).start()


# ═══════════════════════════════════════════════════
#  FORCE SUBSCRIBE SYSTEM
# ═══════════════════════════════════════════════════
def check_joined(uid):
    if not state.force_sub_enabled:
        return True, []
    if state.is_admin(uid):
        return True, []
    channels = db.get_active_channels()
    if not channels:
        ch_list = [(u, n) for u, n in DEFAULT_FORCE_CHANNELS.items()]
    else:
        ch_list = [(c['channel_username'], c['channel_name']) for c in channels]
    not_joined = []
    for cu, cn in ch_list:
        try:
            mem = bot.get_chat_member(f"@{cu}", uid)
            if mem.status in ['left', 'kicked']:
                not_joined.append((cu, cn))
        except telebot.apihelper.ApiTelegramException:
            not_joined.append((cu, cn))
        except:
            continue
    return len(not_joined) == 0, not_joined


def force_sub_kb(not_joined):
    m = types.InlineKeyboardMarkup(row_width=1)
    for cu, cn in not_joined:
        m.add(types.InlineKeyboardButton(f"📢 Join {cn}", url=f"https://t.me/{cu}"))
    m.add(types.InlineKeyboardButton("✅ I've Joined — Verify", callback_data="verify_join"))
    return m


def send_force_sub(cid, nj):
    ch_text = ""
    for i, (cu, cn) in enumerate(nj, 1):
        ch_text += f"  {i}. <b>{cn}</b> — @{cu}\n"
    safe_send(cid,
        f"🔒 <b>CHANNEL VERIFICATION REQUIRED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ You must join our channels to use this bot!\n\n"
        f"{ch_text}\n"
        f"👇 Join all channels, then press <b>Verify</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        reply_markup=force_sub_kb(nj)
    )


# ═══════════════════════════════════════════════════
#  ADMIN HELPERS
# ═══════════════════════════════════════════════════
def show_admin_panel(uid):
    s = db.stats()
    rn = len([k for k in bot_scripts if is_running(k)])
    tickets = len(db.open_tickets())
    safe_send(uid,
        f"👑 <b>ADMIN PANEL</b>\n"
        f"{BRAND_TAG}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Total Users: {s['users']} (+{s['today']} today)\n"
        f"🤖 Running Bots: {rn}\n"
        f"💎 Active Subs: {s['active_subs']}\n"
        f"🚫 Banned: {s['banned']}\n"
        f"💳 Pending Payments: {s['pending']}\n"
        f"🎫 Open Tickets: {tickets}\n"
        f"💰 Total Revenue: {s['revenue']} BDT\n\n"
        f"🔐 Force Sub: {'🟢 ON' if state.force_sub_enabled else '🔴 OFF'}\n"
        f"🔒 Bot Lock: {'🔒 LOCKED' if state.bot_locked else '🔓 OPEN'}\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        reply_markup=admin_kb()
    )


def show_user_info(admin_uid, target_uid):
    u = db.get_user(target_uid)
    if not u:
        safe_send(admin_uid, f"❌ User <code>{target_uid}</code> not found!")
        return
    pl = PLAN_LIMITS.get(u.get('plan', 'free'), PLAN_LIMITS['free'])
    bc = db.bot_count(target_uid)
    bots_list = db.get_bots(target_uid)
    running = sum(1 for b in bots_list if bot_running(target_uid, b['bot_name']))
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("🚫 Ban", callback_data=f"adm_ban_direct:{target_uid}"),
        types.InlineKeyboardButton("✅ Unban", callback_data=f"adm_unban_direct:{target_uid}")
    )
    m.add(types.InlineKeyboardButton("🔙 Admin", callback_data="menu_admin"))
    safe_send(admin_uid,
        f"👤 <b>User Info</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 ID: <code>{target_uid}</code>\n"
        f"📛 Name: {u.get('full_name', '?')}\n"
        f"👤 @{u.get('username', 'N/A')}\n"
        f"🚫 Banned: {'Yes — ' + str(u.get('ban_reason', '')) if u.get('is_banned') else 'No'}\n\n"
        f"📦 Plan: {pl['name']}\n"
        f"📅 Expires: {time_left(u.get('subscription_end'))}\n"
        f"👑 Lifetime: {'Yes' if u.get('is_lifetime') else 'No'}\n\n"
        f"🤖 Bots: {bc} (🟢 {running})\n"
        f"💰 Wallet: {u.get('wallet_balance', 0)} BDT\n"
        f"💳 Spent: {u.get('total_spent', 0)} BDT\n\n"
        f"👥 Refs: {u.get('referral_count', 0)}\n"
        f"🔑 Code: <code>{u.get('referral_code', '?')}</code>\n"
        f"📅 Joined: {str(u.get('created_at', '?'))[:16]}\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        reply_markup=m
    )


def do_broadcast_send(admin_uid, text, reply_cid=None):
    users = db.get_all_users()
    sent_count = [0]
    failed_count = [0]
    lock = threading.Lock()
    cid = reply_cid or admin_uid
    prog = safe_send(cid, f"📢 Broadcasting to {len(users)} users...")
    total = len(users)

    def send_one(u):
        try:
            safe_send(u['user_id'], f"📢 <b>Announcement</b>\n\n{text}\n{BRAND_FOOTER}")
            with lock:
                sent_count[0] += 1
        except Exception:
            with lock:
                failed_count[0] += 1

    # Use ThreadPoolExecutor — 10 concurrent senders, respects Telegram ~30msg/s limit
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import math
    chunk_size = 50
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(send_one, u) for u in users]
        for i, future in enumerate(as_completed(futures)):
            if i % chunk_size == 0 and prog:
                safe_edit(
                    f"📢 Progress: {sent_count[0]+failed_count[0]}/{total}\n✅ {sent_count[0]} | ❌ {failed_count[0]}",
                    cid, prog.message_id
                )
            time.sleep(0.033)  # ~30 msg/s max across all threads

    if prog:
        safe_edit(
            f"📢 <b>Broadcast Complete!</b>\n\n✅ Sent: {sent_count[0]}\n❌ Failed: {failed_count[0]}\n👥 Total: {total}",
            cid, prog.message_id,
            reply_markup=back_btn("menu_admin", "🔙 Admin")
        )
    db.admin_log(admin_uid, 'broadcast', det=f"sent:{sent_count[0]} failed:{failed_count[0]}")


# ═══════════════════════════════════════════════════
#  PAYMENT TEXT HANDLER
# ═══════════════════════════════════════════════════
def handle_pay_text(msg):
    uid = msg.from_user.id
    s = state.get_pay_state(uid)
    if not s or s.get('step') != 'wait_trx':
        return
    try:
        trx = msg.text.strip() if msg.text else 'SCREENSHOT'
        if not trx or len(trx) < 3:
            return safe_reply(msg, "❌ Please send a valid Transaction ID!")
        pid = db.add_pay(uid, s['amount'], s['method'], trx, s['plan'], 30)
        state.clear_pay_state(uid)
        safe_send(uid,
            f"✅ <b>PAYMENT SUBMITTED!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 Payment ID: #{pid}\n"
            f"💰 Amount: {s['amount']} BDT\n"
            f"💳 Method: {s['method']}\n"
            f"📦 Plan: {PLAN_LIMITS.get(s['plan'], {}).get('name', s['plan'])}\n"
            f"🔖 TRX: <code>{trx}</code>\n\n"
            f"⏳ Waiting for admin approval...\n"
            f"━━━━━━━━━━━━━━━━━━━━",
            reply_markup=back_btn()
        )
        u = db.get_user(uid)
        method_info = PAYMENT_METHODS.get(s['method'], {})
        for aid in state.admin_ids:
            safe_send(aid,
                f"💳 <b>NEW PAYMENT!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👤 {u.get('full_name', '?') if u else '?'} (<code>{uid}</code>)\n"
                f"📦 Plan: {s['plan']}\n"
                f"💰 Amount: {s['amount']} BDT\n"
                f"{method_info.get('icon', '💳')} {method_info.get('name', s['method'])}\n"
                f"🔖 TRX: <code>{trx}</code>\n"
                f"🆔 #{pid}\n"
                f"━━━━━━━━━━━━━━━━━━━━",
                reply_markup=pay_approve_kb(pid)
            )
    except Exception as e:
        forward_crash("handle_pay_text", e, uid)
        state.clear_pay_state(uid)


# ═══════════════════════════════════════════════════
#  STATE HANDLER
# ═══════════════════════════════════════════════════
def handle_user_state(msg):
    uid = msg.from_user.id
    s = state.get_state(uid)
    if not s:
        return
    action = s.get('action')

    try:
        if action == 'broadcast':
            if not state.is_admin(uid):
                state.clear_state(uid)
                return
            # FIX: Run broadcast in background thread — never block handler
            bc_text = msg.text
            bc_cid = msg.chat.id
            threading.Thread(
                target=do_broadcast_send,
                args=(uid, bc_text, bc_cid),
                daemon=True, name="broadcast"
            ).start()
            state.clear_state(uid)

        elif action == 'adm_addsub_uid':
            try:
                target = int(msg.text.strip())
                target_user = db.get_user(target)
                if not target_user:
                    safe_reply(msg, f"❌ User <code>{target}</code> not found!")
                    state.clear_state(uid)
                    return
                m = types.InlineKeyboardMarkup(row_width=2)
                for k, p in PLAN_LIMITS.items():
                    if k != 'free':
                        m.add(types.InlineKeyboardButton(p['name'], callback_data=f"adm_setplan:{k}:{target}"))
                m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_admin"))
                safe_reply(msg,
                    f"👤 User: <code>{target}</code> — {target_user.get('full_name', '?')}\n"
                    f"Current: {PLAN_LIMITS.get(target_user.get('plan', 'free'), PLAN_LIMITS['free'])['name']}\n\n"
                    f"Select new plan:",
                    reply_markup=m
                )
                state.clear_state(uid)
            except ValueError:
                safe_reply(msg, "❌ Invalid user ID!")
                state.clear_state(uid)

        elif action == 'adm_addsub_days':
            try:
                days = int(msg.text.strip())
                target = s['target']
                plan = s['plan']
                if days == 0:
                    db.set_sub(target, 'lifetime')
                    plan_name = "👑 Lifetime"
                else:
                    db.set_sub(target, plan, days)
                    plan_name = PLAN_LIMITS.get(plan, {}).get('name', plan)
                safe_reply(msg,
                    f"✅ <b>Subscription Added!</b>\n\n"
                    f"👤 User: <code>{target}</code>\n"
                    f"📦 Plan: {plan_name}\n"
                    f"📅 Duration: {'Lifetime' if days == 0 else f'{days} days'}",
                    reply_markup=back_btn("menu_admin", "🔙 Admin")
                )
                db.admin_log(uid, 'add_sub', target, f"{plan}/{days}d")
                safe_send(target, f"🎉 <b>Plan Upgraded!</b>\n📦 {plan_name}\n📅 {'Lifetime' if days==0 else f'{days} days'}\n{BRAND_FOOTER}")
            except ValueError:
                safe_reply(msg, "❌ Send a number! (0 = lifetime)")
            state.clear_state(uid)

        elif action == 'adm_remsub_uid':
            try:
                target = int(msg.text.strip())
                db.rem_sub(target)
                safe_reply(msg, f"✅ Subscription removed: <code>{target}</code>", reply_markup=back_btn("menu_admin", "🔙 Admin"))
                db.admin_log(uid, 'remove_sub', target)
                safe_send(target, "⚠️ Your subscription has been removed by admin.")
            except:
                safe_reply(msg, "❌ Invalid user ID!")
            state.clear_state(uid)

        elif action == 'adm_ban_uid':
            parts = msg.text.strip().split(maxsplit=1)
            try:
                target = int(parts[0])
                reason = parts[1] if len(parts) > 1 else "Banned by admin"
                db.ban(target, reason)
                db.admin_log(uid, 'ban', target, reason)
                for b in db.get_bots(target):
                    sk = f"{target}_{b['bot_name']}"
                    if sk in bot_scripts:
                        kill_tree(bot_scripts[sk])
                        cleanup_script(sk)
                    db.update_bot(b['bot_id'], status='stopped')
                safe_reply(msg, f"🚫 Banned <code>{target}</code>\nReason: {reason}", reply_markup=back_btn("menu_admin", "🔙 Admin"))
                safe_send(target, f"🚫 <b>You have been banned!</b>\nReason: {reason}\n\nContact {YOUR_USERNAME}")
            except:
                safe_reply(msg, "❌ Format: USER_ID [REASON]")
            state.clear_state(uid)

        elif action == 'adm_unban_uid':
            try:
                target = int(msg.text.strip())
                db.unban(target)
                db.admin_log(uid, 'unban', target)
                safe_reply(msg, f"✅ Unbanned <code>{target}</code>", reply_markup=back_btn("menu_admin", "🔙 Admin"))
                safe_send(target, "✅ You have been unbanned! Welcome back.")
            except:
                safe_reply(msg, "❌ Invalid user ID!")
            state.clear_state(uid)

        elif action == 'adm_give_balance':
            parts = msg.text.strip().split()
            if len(parts) >= 2:
                try:
                    target = int(parts[0])
                    amount = float(parts[1])
                    if not db.get_user(target):
                        safe_reply(msg, f"❌ User {target} not found!")
                    else:
                        db.wallet_tx(target, amount, 'bonus', f"Admin bonus by {uid}")
                        safe_reply(msg, f"✅ +{amount} BDT → <code>{target}</code>", reply_markup=back_btn("menu_admin", "🔙 Admin"))
                        safe_send(target, f"🎁 <b>Admin Bonus!</b>\n💰 +{amount} BDT\n{BRAND_FOOTER}")
                except:
                    safe_reply(msg, "❌ Error!")
            else:
                safe_reply(msg, "❌ Format: USER_ID AMOUNT")
            state.clear_state(uid)

        elif action == 'adm_userinfo_uid':
            try:
                target = int(msg.text.strip())
                show_user_info(uid, target)
            except ValueError:
                safe_reply(msg, "❌ Invalid user ID!")
            state.clear_state(uid)

        elif action == 'adm_notify_uid':
            try:
                parts = msg.text.strip().split(maxsplit=1)
                target = int(parts[0])
                text = parts[1] if len(parts) > 1 else "Notification from admin"
                db.add_notif(target, "Admin Notice", text)
                safe_reply(msg, f"✅ Sent to <code>{target}</code>", reply_markup=back_btn("menu_admin", "🔙 Admin"))
                safe_send(target, f"🔔 <b>Notification</b>\n\n{text}\n{BRAND_FOOTER}")
            except:
                safe_reply(msg, "❌ Format: USER_ID MESSAGE")
            state.clear_state(uid)

        elif action == 'adm_promo_create':
            parts = msg.text.strip().split()
            if len(parts) >= 3:
                try:
                    code = parts[0].upper()
                    discount = int(parts[1])
                    max_uses = int(parts[2])
                    db.add_promo(code, discount, max_uses, uid)
                    safe_reply(msg,
                        f"✅ <b>Promo Created!</b>\n\n🎟 Code: <code>{code}</code>\n💰 {discount}% | 🔢 {max_uses} uses",
                        reply_markup=back_btn("menu_admin", "🔙 Admin")
                    )
                    db.admin_log(uid, 'create_promo', det=f"{code}/{discount}%/{max_uses}")
                except:
                    safe_reply(msg, "❌ Error creating promo!")
            else:
                safe_reply(msg, "❌ Format: CODE DISCOUNT% MAX_USES\nEx: SAVE50 50 100")
            state.clear_state(uid)

        elif action == 'ch_add':
            parts = msg.text.strip().split(maxsplit=1)
            ch_username = parts[0].lstrip('@').lower()
            ch_name = parts[1] if len(parts) > 1 else ch_username
            try:
                chat_info = bot.get_chat(f"@{ch_username}")
                ch_name = chat_info.title or ch_name
            except:
                pass
            db.add_channel(ch_username, ch_name, uid)
            db.admin_log(uid, 'add_channel', det=f"@{ch_username}")
            safe_reply(msg, f"✅ <b>Channel Added!</b>\n📢 @{ch_username}\n⚠️ Make sure bot is admin!", reply_markup=back_btn("adm_channels", "🔙 Channels"))
            state.clear_state(uid)

        elif action == 'ch_remove':
            text = msg.text.strip().lstrip('@').lower()
            db.remove_channel(text)
            db.admin_log(uid, 'remove_channel', det=f"@{text}")
            safe_reply(msg, f"✅ Removed @{text}", reply_markup=back_btn("adm_channels", "🔙 Channels"))
            state.clear_state(uid)

        elif action == 'ticket':
            text = msg.text.strip()
            if len(text) < 5:
                safe_reply(msg, "❌ Message too short! Min 5 chars.")
                state.clear_state(uid)
                return
            tid = db.add_ticket(uid, "Support Request", text)
            safe_reply(msg,
                f"✅ <b>Ticket #{tid} Created!</b>\n\n📝 {text[:100]}\n\nOur team will respond soon.\n📞 Direct: {YOUR_USERNAME}\n{BRAND_FOOTER}",
                reply_markup=back_btn()
            )
            u = db.get_user(uid)
            for aid in state.admin_ids:
                m = types.InlineKeyboardMarkup()
                m.add(types.InlineKeyboardButton(f"💬 Reply #{tid}", callback_data=f"adm_ticket_reply:{tid}"))
                safe_send(aid,
                    f"🎫 <b>New Ticket #{tid}</b>\n\n"
                    f"👤 {u.get('full_name', uid) if u else uid} (<code>{uid}</code>)\n"
                    f"📝 {text[:200]}",
                    reply_markup=m
                )
            state.clear_state(uid)

        elif action == 'ticket_reply':
            tid = s.get('ticket_id')
            text = msg.text.strip()
            if not text or not tid:
                state.clear_state(uid)
                return
            ticket = db.get_ticket(tid)
            if ticket:
                db.reply_ticket(tid, text)
                safe_reply(msg, f"✅ Replied to ticket #{tid}", reply_markup=back_btn("adm_tickets", "🔙 Tickets"))
                safe_send(ticket['user_id'], f"📩 <b>Ticket #{tid} — Reply</b>\n\n💬 {text}\n{BRAND_FOOTER}")
            state.clear_state(uid)

        else:
            state.clear_state(uid)

    except Exception as e:
        forward_crash("handle_user_state", e, uid)
        state.clear_state(uid)


# ═══════════════════════════════════════════════════
#  /START COMMAND
# ═══════════════════════════════════════════════════
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    uid = msg.from_user.id
    un = msg.from_user.username or ''
    fn = f"{msg.from_user.first_name or ''} {msg.from_user.last_name or ''}".strip()
    state.active_users.add(uid)

    # Remove any old Reply Keyboard buttons from chat
    try:
        rm = bot.send_message(msg.chat.id, "👋", reply_markup=types.ReplyKeyboardRemove())
        bot.delete_message(msg.chat.id, rm.message_id)
    except Exception:
        pass

    try:
        joined, nj = check_joined(uid)
        if not joined:
            send_force_sub(msg.chat.id, nj)
            return

        ex = db.get_user(uid)
        if ex and ex.get('is_banned'):
            return safe_reply(msg, f"🚫 <b>You are banned!</b>\nReason: {ex.get('ban_reason', 'N/A')}\n\nContact {YOUR_USERNAME}")
        if state.bot_locked and not state.is_admin(uid):
            return safe_reply(msg, "🔒 <b>Bot is in maintenance mode.</b>\nPlease try again later.")

        is_new = ex is None
        ref_by = None
        args = msg.text.split()

        if len(args) > 1:
            rc = args[1].strip()
            # Use direct DB lookup instead of loading all users (much faster)
            rr = db.get_user_by_ref_code(rc)
            if rr and rr['user_id'] != uid and is_new:
                ref_by = rr['user_id']

        code = gen_ref_code(uid)

        if is_new:
            db.create_user(uid, un, fn, code, ref_by)
            if ref_by:
                db.add_ref(ref_by, uid, REF_BONUS_DAYS, REF_COMMISSION)
                rd = db.get_user(ref_by)
                safe_send(ref_by,
                    f"🎉 <b>NEW REFERRAL!</b>\n\n"
                    f"👤 <b>{fn}</b> joined via your link!\n"
                    f"💰 +{REF_COMMISSION} BDT wallet bonus!\n"
                    f"📅 +{REF_BONUS_DAYS} days premium!\n"
                    f"👥 Total Referrals: {rd.get('referral_count', '?') if rd else '?'}\n"
                    f"{BRAND_FOOTER}"
                )
            for aid in state.admin_ids:
                safe_send(aid, f"👤 <b>New User!</b>\n{fn} (<code>{uid}</code>)\nRef: {ref_by or 'Direct'}")
        else:
            db.update_user(uid, username=un, full_name=fn, last_active=datetime.now().isoformat())

        u = db.get_user(uid)
        pl = PLAN_LIMITS.get(u.get('plan', 'free'), PLAN_LIMITS['free']) if u else PLAN_LIMITS['free']
        bc = db.bot_count(uid)
        mx = '♾️' if pl['max_bots'] == -1 else str(pl['max_bots'])
        role = '👑 Owner' if uid == OWNER_ID else '⭐ Admin' if state.is_admin(uid) else pl['name']

        welcome = (
            f"🌟 <b>SB HOSTING PANEL</b> {BRAND_VER}\n"
            f"<i>Premium Bot Hosting Platform</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👋 Welcome, <b>{fn}</b>!\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"📦 Plan: {role}\n"
            f"🤖 Bots: {bc}/{mx}\n"
            f"💰 Wallet: {u.get('wallet_balance', 0) if u else 0} BDT\n"
            f"👥 Referrals: {u.get('referral_count', 0) if u else 0}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🚀 <b>What you can do:</b>\n"
            f"  📤 Deploy Python &amp; Node.js bots\n"
            f"  🔍 Smart auto-detection\n"
            f"  🎁 Earn with referrals\n"
            f"  💳 Easy payments\n\n"
            f"👇 <b>Choose from the menu below:</b>"
        )
        safe_send(msg.chat.id, welcome, reply_markup=main_menu_kb(uid))

    except Exception as e:
        forward_crash("cmd_start", e, uid)
        safe_send(msg.chat.id, "❌ An error occurred. Please try again.")


# ═══════════════════════════════════════════════════
#  OTHER COMMANDS
# ═══════════════════════════════════════════════════
@bot.message_handler(commands=['help'])
def cmd_help(msg):
    uid = msg.from_user.id
    try:
        safe_send(uid,
            f"📚 <b>HELP CENTER</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Welcome to {BRAND}!\nSelect a topic below.\n━━━━━━━━━━━━━━━━━━━━",
            reply_markup=help_menu_kb()
        )
    except Exception as e:
        forward_crash("cmd_help", e, uid)


@bot.message_handler(commands=['admin'])
def cmd_admin(msg):
    uid = msg.from_user.id
    if not state.is_admin(uid):
        return safe_reply(msg, "❌ Admin access only!")
    show_admin_panel(uid)


@bot.message_handler(commands=['id'])
def cmd_id(msg):
    uid = msg.from_user.id
    safe_send(msg.chat.id,
        f"🆔 <b>Your Info</b>\n\n"
        f"👤 ID: <code>{uid}</code>\n"
        f"📛 Name: {msg.from_user.first_name or ''} {msg.from_user.last_name or ''}\n"
        f"👤 Username: @{msg.from_user.username or 'N/A'}\n"
        f"{BRAND_FOOTER}",
        reply_markup=back_btn()
    )


@bot.message_handler(commands=['ping'])
def cmd_ping(msg):
    start = time.time()
    m = safe_reply(msg, "🏓 Pinging...")
    if m:
        latency = round((time.time() - start) * 1000, 2)
        rn = len([k for k in bot_scripts if is_running(k)])
        safe_edit(
            f"🏓 <b>Pong!</b>\n\n"
            f"⚡ Latency: {latency}ms\n"
            f"⏱️ Uptime: {get_uptime()}\n"
            f"🤖 Running: {rn} bots\n"
            f"{BRAND_FOOTER}",
            msg.chat.id, m.message_id, reply_markup=back_btn()
        )


@bot.message_handler(commands=['status'])
def cmd_status(msg):
    uid = msg.from_user.id
    try:
        bots_list = db.get_bots(uid)
        if not bots_list:
            return safe_reply(msg,
                f"📭 <b>No bots deployed yet!</b>\n\n"
                f"Send a .py, .js or .zip file to deploy your first bot.\n"
                f"{BRAND_FOOTER}",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("📤 Deploy Bot", callback_data="menu_deploy")
                )
            )

        running = [(b, bot_running(uid, b['bot_name'])) for b in bots_list]
        active  = sum(1 for _, r in running if r)
        total   = len(bots_list)

        t = (
            f"📊 <b>YOUR BOT STATUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🟢 Running: {active} / {total}\n\n"
        )

        m = types.InlineKeyboardMarkup(row_width=1)
        for b, r in running:
            sk    = f"{uid}_{b['bot_name']}"
            icon  = "🟢" if r else "🔴"
            ftype = "🐍" if b['file_type'] == 'py' else "🟨"
            uptime_str = "—"
            if r and sk in bot_scripts:
                st = bot_scripts[sk].get('start_time')
                if st:
                    uptime_str = str(datetime.now() - st).split('.')[0]
            t += f"{icon} {ftype} <code>{b['bot_name'][:20]}</code>\n"
            if r:
                t += f"   ⏱️ Uptime: {uptime_str}\n"
            t += "\n"
            m.add(types.InlineKeyboardButton(
                f"{icon} {b['bot_name'][:18]} #{b['bot_id']}",
                callback_data=f"bot_detail:{b['bot_id']}"
            ))

        t += "━━━━━━━━━━━━━━━━━━━━"
        m.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
        safe_reply(msg, t, reply_markup=m)
    except Exception as e:
        forward_crash("cmd_status", e, uid)


@bot.message_handler(commands=['ban'])
def cmd_ban(msg):
    if not state.is_admin(msg.from_user.id):
        return
    p = msg.text.split(maxsplit=2)
    if len(p) < 2:
        return safe_reply(msg, "Usage: /ban UID [REASON]")
    try:
        target = int(p[1])
        reason = p[2] if len(p) > 2 else "Banned by admin"
        db.ban(target, reason)
        safe_reply(msg, f"🚫 Banned <code>{target}</code>")
    except:
        safe_reply(msg, "❌ Error!")


@bot.message_handler(commands=['unban'])
def cmd_unban(msg):
    if not state.is_admin(msg.from_user.id):
        return
    try:
        target = int(msg.text.split()[1])
        db.unban(target)
        safe_reply(msg, f"✅ Unbanned <code>{target}</code>")
    except:
        safe_reply(msg, "❌ Error!")


@bot.message_handler(commands=['broadcast', 'bc'])
def cmd_broadcast(msg):
    uid = msg.from_user.id
    if not state.is_admin(uid):
        return
    text = msg.text.split(maxsplit=1)
    if len(text) < 2:
        state.set_state(uid, {'action': 'broadcast'})
        return safe_reply(msg, "📢 Send broadcast message now:")
    do_broadcast_send(uid, text[1], msg.chat.id)


@bot.message_handler(commands=['give'])
def cmd_give(msg):
    if not state.is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 3:
        return safe_reply(msg, "Usage: /give UID AMOUNT")
    try:
        target = int(parts[1])
        amount = float(parts[2])
        if not db.get_user(target):
            return safe_reply(msg, f"❌ User {target} not found!")
        db.wallet_tx(target, amount, 'bonus', f"Admin bonus by {msg.from_user.id}")
        safe_reply(msg, f"✅ +{amount} BDT → <code>{target}</code>")
        safe_send(target, f"🎁 <b>Admin Bonus!</b>\n💰 +{amount} BDT added!\n{BRAND_FOOTER}")
    except:
        safe_reply(msg, "❌ Error!")


@bot.message_handler(commands=['notify'])
def cmd_notify(msg):
    if not state.is_admin(msg.from_user.id):
        return
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        return safe_reply(msg, "Usage: /notify USER_ID MESSAGE")
    try:
        target = int(parts[1])
        text = parts[2]
        db.add_notif(target, "Admin Notice", text)
        safe_reply(msg, f"✅ Notification sent to <code>{target}</code>")
        safe_send(target, f"🔔 <b>Notification</b>\n\n{text}\n{BRAND_FOOTER}")
    except:
        safe_reply(msg, "❌ Error!")


@bot.message_handler(commands=['subscribe'])
def cmd_sub_admin(msg):
    if not state.is_admin(msg.from_user.id):
        return
    p = msg.text.split()
    if len(p) < 3:
        return safe_reply(msg, "Usage: /subscribe UID DAYS")
    try:
        target_uid = int(p[1])
        days = int(p[2])
        db.set_sub(target_uid, 'pro' if days > 0 else 'lifetime', days)
        safe_reply(msg, f"✅ Subscription set for <code>{target_uid}</code> — {days}d")
    except:
        safe_reply(msg, "❌ Error!")


@bot.message_handler(commands=['addchannel'])
def cmd_add_channel(msg):
    uid = msg.from_user.id
    if not state.is_admin(uid):
        return
    parts = msg.text.split(maxsplit=2)
    if len(parts) < 2:
        return safe_reply(msg, "Usage: /addchannel @username [Channel Name]")
    ch_username = parts[1].lstrip('@').lower()
    ch_name = parts[2] if len(parts) > 2 else ch_username
    try:
        chat_info = bot.get_chat(f"@{ch_username}")
        ch_name = chat_info.title or ch_name
    except:
        pass
    db.add_channel(ch_username, ch_name, uid)
    db.admin_log(uid, 'add_channel', det=f"@{ch_username}")
    safe_reply(msg, f"✅ Channel @{ch_username} added!\n⚠️ Make sure bot is admin!")


@bot.message_handler(commands=['channels'])
def cmd_channels(msg):
    if not state.is_admin(msg.from_user.id):
        return
    channels = db.get_all_channels()
    t = f"📢 <b>Force Subscribe Channels</b>\nStatus: {'🟢 ON' if state.force_sub_enabled else '🔴 OFF'}\n\n"
    if channels:
        for ch in channels:
            st = "🟢" if ch['is_active'] else "🔴"
            t += f"  {st} @{ch['channel_username']} — {ch['channel_name']}\n"
    else:
        t += "No channels. Default: @developer_apon_07\n"
    safe_send(msg.from_user.id, t, reply_markup=back_btn("menu_admin", "🔙 Admin"))


# ═══════════════════════════════════════════════════
#  TEXT HANDLER
# ═══════════════════════════════════════════════════
@bot.message_handler(content_types=['text'])
def handle_text(msg):
    uid = msg.from_user.id
    state.active_users.add(uid)
    try:
        if not rate_check(uid):
            return
        joined, nj = check_joined(uid)
        if not joined:
            send_force_sub(msg.chat.id, nj)
            return
        u = db.get_user(uid)
        if u and u.get('is_banned'):
            return
        if state.bot_locked and not state.is_admin(uid):
            return safe_reply(msg, "🔒 <b>Maintenance mode.</b> Please wait.")
        if state.get_pay_state(uid):
            return handle_pay_text(msg)
        if state.get_state(uid):
            return handle_user_state(msg)
        if not u:
            safe_send(uid, "Please press /start first!")
            return
        safe_send(uid,
            f"🏠 <b>Main Menu</b>\n\nUse the buttons below.\n━━━━━━━━━━━━━━━━━━━━",
            reply_markup=main_menu_kb(uid)
        )
    except Exception as e:
        forward_crash("handle_text", e, uid)


# ═══════════════════════════════════════════════════
#  PHOTO HANDLER (Payment screenshots)
# ═══════════════════════════════════════════════════
@bot.message_handler(content_types=['photo'])
def handle_photo(msg):
    uid = msg.from_user.id
    s = state.get_pay_state(uid)
    if s and s.get('step') == 'wait_trx':
        try:
            trx = f"SCREENSHOT_{datetime.now().strftime('%H%M%S')}"
            pid = db.add_pay(uid, s['amount'], s['method'], trx, s['plan'], 30)
            state.clear_pay_state(uid)
            safe_send(uid,
                f"✅ <b>PAYMENT SUBMITTED!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🆔 #{pid}\n📸 Screenshot received\n⏳ Waiting for approval...\n"
                f"━━━━━━━━━━━━━━━━━━━━",
                reply_markup=back_btn()
            )
            u = db.get_user(uid)
            for aid in state.admin_ids:
                try:
                    bot.forward_message(aid, uid, msg.message_id)
                except:
                    pass
                safe_send(aid,
                    f"💳 <b>Payment #{pid}</b> (Screenshot)\n"
                    f"👤 {u.get('full_name', uid) if u else uid} (<code>{uid}</code>)\n"
                    f"💰 {s['amount']} BDT | {s['method']} | {s['plan']}",
                    reply_markup=pay_approve_kb(pid)
                )
        except Exception as e:
            forward_crash("handle_photo", e, uid)
            state.clear_pay_state(uid)


# ═══════════════════════════════════════════════════
#  DOCUMENT HANDLER
# ═══════════════════════════════════════════════════
@bot.message_handler(content_types=['document'])
def handle_doc(msg):
    uid = msg.from_user.id
    try:
        joined, nj = check_joined(uid)
        if not joined:
            send_force_sub(msg.chat.id, nj)
            return
        u = db.get_user(uid)
        if not u:
            return safe_reply(msg, "Please /start first!")
        if u.get('is_banned'):
            return
        pl = db.get_plan(uid)
        cur = db.bot_count(uid)
        mx = pl['max_bots']
        if mx != -1 and cur >= mx:
            return safe_reply(msg,
                f"❌ <b>Bot limit reached!</b> ({cur}/{mx})\nUpgrade your plan.",
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("💎 Upgrade", callback_data="menu_sub")
                )
            )

        fn = msg.document.file_name
        fs = msg.document.file_size
        ext = fn.rsplit('.', 1)[-1].lower() if '.' in fn else ''
        allowed = ['py', 'js', 'zip', 'json', 'txt', 'env', 'yml', 'yaml', 'cfg', 'ini', 'toml']
        if ext not in allowed:
            return safe_reply(msg, f"❌ Unsupported file: .{ext}\n\nSupported: {', '.join(allowed)}")
        if fs > 100 * 1024 * 1024:
            return safe_reply(msg, "❌ File too large! Max 100MB.")

        pm = safe_reply(msg, f"📤 Uploading <code>{fn[:25]}</code> ({fmt_size(fs)})...")
        fi = bot.get_file(msg.document.file_id)
        dl = bot.download_file(fi.file_path)
        uf = user_folder(uid)

        if ext == 'zip':
            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
                tmp.write(dl)
                tp = tmp.name
            try:
                with zipfile.ZipFile(tp, 'r') as z:
                    # Define ed FIRST before using it in security check
                    bn = fn.replace('.zip', '').replace(' ', '_')
                    ed = os.path.join(uf, bn)
                    # Secure path traversal check
                    extract_path = os.path.realpath(ed)
                    for n in z.namelist():
                        # Check for absolute paths and traversal attempts
                        member_path = os.path.realpath(os.path.join(extract_path, n))
                        if not member_path.startswith(extract_path + os.sep) and member_path != extract_path:
                            if pm:
                                safe_edit("❌ Suspicious file paths in ZIP! Upload rejected for safety.", msg.chat.id, pm.message_id)
                            os.unlink(tp)
                            return
                    if os.path.exists(ed):
                        shutil.rmtree(ed, ignore_errors=True)
                    os.makedirs(ed, exist_ok=True)
                    z.extractall(ed)
                    items = os.listdir(ed)
                    if len(items) == 1 and os.path.isdir(os.path.join(ed, items[0])):
                        inner = os.path.join(ed, items[0])
                        for item in os.listdir(inner):
                            src = os.path.join(inner, item)
                            dst = os.path.join(ed, item)
                            if os.path.exists(dst):
                                shutil.rmtree(dst) if os.path.isdir(dst) else os.remove(dst)
                            shutil.move(src, dst)
                        try:
                            os.rmdir(inner)
                        except:
                            pass
                os.unlink(tp)
                entry, ft, report = det.report(ed)
                if not entry:
                    af = [os.path.relpath(os.path.join(r, f), ed) for r, d_, fs_l in os.walk(ed) for f in fs_l if f.endswith(('.py', '.js'))]
                    err_text = f"❌ <b>No entry file detected!</b>\n\n📁 Files in ZIP:\n"
                    for f in af[:15]:
                        err_text += f"  • <code>{f}</code>\n"
                    if not af:
                        err_text += "  (No .py or .js files)\n"
                    err_text += "\n💡 Make sure ZIP has main.py, app.py, or bot.py"
                    if pm:
                        safe_edit(err_text, msg.chat.id, pm.message_id, reply_markup=back_btn())
                    return
                bid = db.add_bot(uid, bn, ed, entry, ft, '', fs, '')
                mk = types.InlineKeyboardMarkup(row_width=2)
                mk.add(
                    types.InlineKeyboardButton("▶️ Start Now", callback_data=f"bot_start:{bid}"),
                    types.InlineKeyboardButton("🤖 My Bots", callback_data="menu_mybots")
                )
                mk.add(types.InlineKeyboardButton("🔍 Re-detect", callback_data=f"bot_redetect:{bid}"))
                if pm:
                    safe_edit(f"✅ <b>ZIP DEPLOYED!</b>\n\n📦 <code>{bn[:20]}</code>\n🆔 Bot ID: #{bid}\n\n🔍 <b>Detection:</b>\n{report}", msg.chat.id, pm.message_id, reply_markup=mk)
            except zipfile.BadZipFile:
                if pm:
                    safe_edit("❌ Invalid or corrupted ZIP file!", msg.chat.id, pm.message_id)
                try:
                    os.unlink(tp)
                except:
                    pass

        elif ext in ['py', 'js']:
            file_path = os.path.join(uf, fn)
            with open(file_path, 'wb') as f:
                f.write(dl)
            bid = db.add_bot(uid, fn, uf, fn, ext, '', fs, 'exact')
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("▶️ Run Now", callback_data=f"bot_start:{bid}"),
                types.InlineKeyboardButton("🤖 My Bots", callback_data="menu_mybots")
            )
            if pm:
                safe_edit(
                    f"✅ <b>FILE UPLOADED!</b>\n\n📄 <code>{fn[:25]}</code>\n🆔 Bot ID: #{bid}\n🔤 {'🐍 Python' if ext == 'py' else '🟨 Node.js'}\n📊 Size: {fmt_size(fs)}",
                    msg.chat.id, pm.message_id, reply_markup=mk
                )
        else:
            file_path = os.path.join(uf, fn)
            with open(file_path, 'wb') as f:
                f.write(dl)
            if pm:
                safe_edit(f"✅ Config file <code>{fn}</code> saved!", msg.chat.id, pm.message_id, reply_markup=back_btn())

    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        forward_crash("handle_doc", e, uid)
        safe_send(msg.chat.id, f"❌ Upload error: {str(e)[:100]}")


# ═══════════════════════════════════════════════════
#  MASTER CALLBACK HANDLER
# ═══════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    uid = call.from_user.id
    data = call.data
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    try:
        if data == "go_home":
            safe_answer(call.id)
            u = db.get_user(uid)
            if not u:
                db.create_user(uid, call.from_user.username or '',
                               f"{call.from_user.first_name or ''} {call.from_user.last_name or ''}".strip(),
                               gen_ref_code(uid))
            safe_edit(f"🏠 <b>Main Menu</b>\n\nWelcome back!\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=main_menu_kb(uid))

        elif data == "verify_join":
            joined, nj = check_joined(uid)
            if joined:
                safe_answer(call.id, "✅ Verified! Welcome!", show_alert=True)
                safe_delete(chat_id, msg_id)
                u = db.get_user(uid)
                fn = f"{call.from_user.first_name or ''} {call.from_user.last_name or ''}".strip()
                if not u:
                    db.create_user(uid, call.from_user.username or '', fn, gen_ref_code(uid))
                safe_send(uid, f"✅ <b>Verification Successful!</b>\n\nWelcome, <b>{fn}</b>!\n━━━━━━━━━━━━━━━━━━━━", reply_markup=main_menu_kb(uid))
            else:
                safe_answer(call.id, "❌ Join all channels first!", show_alert=True)

        elif data == "menu_mybots":
            safe_answer(call.id)
            bots_list = db.get_bots(uid)
            pl = db.get_plan(uid)
            mx = '♾️' if pl['max_bots'] == -1 else str(pl['max_bots'])
            if not bots_list:
                m = types.InlineKeyboardMarkup(row_width=2)
                m.add(types.InlineKeyboardButton("📤 Deploy Bot", callback_data="menu_deploy"))
                m.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
                safe_edit(f"📭 <b>No bots yet!</b>\n\nDeploy your first bot!\n📦 Slots: 0/{mx}\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)
                return
            rn = sum(1 for b in bots_list if bot_running(uid, b['bot_name']))
            t = f"🤖 <b>My Bots</b> ({len(bots_list)})\n🟢 Running: {rn} | 🔴 Stopped: {len(bots_list) - rn}\n📦 Limit: {mx}\n━━━━━━━━━━━━━━━━━━━━\n\n"
            m = types.InlineKeyboardMarkup(row_width=1)
            for b in bots_list:
                r = bot_running(uid, b['bot_name'])
                ic = "🐍" if b['file_type'] == 'py' else "🟨"
                st_icon = "🟢" if r else "🔴"
                t += f"{st_icon} {ic} <code>{b['bot_name'][:20]}</code> — #{b['bot_id']}\n"
                m.add(types.InlineKeyboardButton(f"{st_icon} {ic} {b['bot_name'][:15]} — #{b['bot_id']}", callback_data=f"bot_detail:{b['bot_id']}"))
            m.add(types.InlineKeyboardButton("📤 Deploy New Bot", callback_data="menu_deploy"))
            m.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
            safe_edit(t, chat_id, msg_id, reply_markup=m)

        elif data == "menu_deploy":
            safe_answer(call.id)
            pl = db.get_plan(uid)
            cur = db.bot_count(uid)
            mx = pl['max_bots']
            if mx != -1 and cur >= mx:
                m = types.InlineKeyboardMarkup()
                m.add(types.InlineKeyboardButton("💎 Upgrade Plan", callback_data="menu_sub"))
                m.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
                safe_edit(f"⚠️ <b>Bot Limit Reached!</b>\n\nCurrent: {cur}/{mx}\nUpgrade your plan.", chat_id, msg_id, reply_markup=m)
                return
            rem = '♾️' if mx == -1 else str(mx - cur)
            safe_edit(
                f"📤 <b>DEPLOY YOUR BOT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📎 Send your file now!\n\n"
                f"<b>Supported:</b>\n  🐍 Python (.py)\n  🟨 Node.js (.js)\n  📦 ZIP archive\n\n"
                f"<b>Smart Detection:</b>\n  🔍 Auto-finds entry file\n  📦 Auto-install requirements\n\n"
                f"📊 Slots remaining: <b>{rem}</b>\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=back_btn()
            )

        elif data == "menu_sub":
            safe_answer(call.id)
            u = db.get_user(uid)
            pl = PLAN_LIMITS.get(u.get('plan', 'free') if u else 'free', PLAN_LIMITS['free'])
            t = (
                f"💎 <b>SUBSCRIPTION</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 Current Plan: {pl['name']}\n"
                f"📅 Expires: {time_left(u.get('subscription_end') if u else None)}\n"
                f"🤖 Slots: {'♾️' if pl['max_bots'] == -1 else pl['max_bots']}\n"
                f"💾 RAM: {pl['ram']}MB\n"
                f"🔄 Auto Restart: {'✅' if pl['auto_restart'] else '❌'}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n<b>Available Plans:</b>\n\n"
            )
            for k, p in PLAN_LIMITS.items():
                if k == 'free':
                    continue
                slots = '♾️' if p['max_bots'] == -1 else str(p['max_bots'])
                t += f"{p['name']}\n  🤖 {slots} bots | 💾 {p['ram']}MB\n  💰 {p['price']} BDT/month\n\n"
            safe_edit(t + "━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=plan_kb())

        elif data == "menu_help":
            safe_answer(call.id)
            safe_edit(f"📚 <b>HELP CENTER</b>\n━━━━━━━━━━━━━━━━━━━━\n\nSelect a topic below.\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=help_menu_kb())

        elif data == "menu_admin":
            if not state.is_admin(uid):
                return safe_answer(call.id, "❌ Admins only!", show_alert=True)
            safe_answer(call.id)
            s = db.stats()
            rn = len([k for k in bot_scripts if is_running(k)])
            tickets = len(db.open_tickets())
            safe_edit(
                f"👑 <b>ADMIN PANEL</b>\n{BRAND_TAG}\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👥 Total Users: {s['users']} (+{s['today']} today)\n"
                f"🤖 Running Bots: {rn}\n💎 Active Subs: {s['active_subs']}\n"
                f"🚫 Banned: {s['banned']}\n💳 Pending Payments: {s['pending']}\n"
                f"🎫 Open Tickets: {tickets}\n💰 Total Revenue: {s['revenue']} BDT\n\n"
                f"🔐 Force Sub: {'🟢 ON' if state.force_sub_enabled else '🔴 OFF'}\n"
                f"🔒 Bot Lock: {'🔒 LOCKED' if state.bot_locked else '🔓 OPEN'}\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=admin_kb()
            )

        elif data == "menu_wallet":
            safe_answer(call.id)
            u = db.get_user(uid)
            hist = db.wallet_hist(uid, 10)
            t = f"💰 <b>WALLET</b>\n━━━━━━━━━━━━━━━━━━━━\n\nBalance: <b>{u.get('wallet_balance', 0) if u else 0} BDT</b>\n\n📋 Recent Transactions:\n"
            for tx in hist[:8]:
                icon = "+" if tx.get('tx_type') in ('credit', 'referral', 'refund', 'bonus') else "-"
                t += f"  {icon}{tx.get('amount', 0)} BDT — {tx.get('tx_type', '?')} | {str(tx.get('created_at', ''))[:10]}\n"
            if not hist:
                t += "  No transactions yet."
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
            safe_edit(t + "\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "menu_ref":
            safe_answer(call.id)
            u = db.get_user(uid)
            rc = u.get('referral_code', gen_ref_code(uid)) if u else gen_ref_code(uid)
            lnk = f"https://t.me/{BOT_USERNAME}?start={rc}"
            t = (
                f"🎁 <b>REFERRAL PROGRAM</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🔗 Your Link:\n<code>{lnk}</code>\n\n"
                f"💰 Earn per referral:\n  • +{REF_COMMISSION} BDT wallet bonus\n  • +{REF_BONUS_DAYS} days premium\n\n"
                f"📊 Your Stats:\n"
                f"  👥 Referrals: {u.get('referral_count', 0) if u else 0}\n"
                f"  💰 Earnings: {u.get('referral_earnings', 0) if u else 0} BDT\n"
                f"  🏆 Level: {u.get('referral_level', 'Bronze').title() if u else 'Bronze'}\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            m = types.InlineKeyboardMarkup(row_width=2)
            m.add(
                types.InlineKeyboardButton("📋 Copy Link", callback_data=f"ref_copy:{rc}"),
                types.InlineKeyboardButton("👥 My Referrals", callback_data="ref_list")
            )
            m.add(types.InlineKeyboardButton("🏆 Leaderboard", callback_data="ref_board"))
            m.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
            safe_edit(t, chat_id, msg_id, reply_markup=m)

        elif data == "menu_stats":
            safe_answer(call.id)
            ss = sys_stats()
            rn = len([k for k in bot_scripts if is_running(k)])
            u = db.get_user(uid)
            bc = db.bot_count(uid)
            t = (
                f"📊 <b>STATISTICS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🖥 <b>System</b>\n  CPU: {ss['cpu']}% | RAM: {ss['mem']}%\n"
                f"  Disk: {ss['disk']}% | Uptime: {ss['up']}\n\n"
                f"🤖 <b>Your Stats</b>\n  Bots: {bc} | Running: {rn}\n"
                f"  Plan: {PLAN_LIMITS.get(u.get('plan','free'), PLAN_LIMITS['free'])['name'] if u else 'Free'}\n"
                f"  Wallet: {u.get('wallet_balance', 0) if u else 0} BDT\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            safe_edit(t, chat_id, msg_id, reply_markup=back_btn())

        elif data == "menu_running":
            safe_answer(call.id)
            bots_list = db.get_bots(uid)
            running = [(b, bot_running(uid, b['bot_name'])) for b in bots_list]
            active = [(b, r) for b, r in running if r]
            t = f"🟢 <b>Running Bots ({len(active)})</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            m = types.InlineKeyboardMarkup(row_width=1)
            for b, _ in active:
                sk = f"{uid}_{b['bot_name']}"
                ram, cpu = bot_res(sk)
                t += f"  🐍 <code>{b['bot_name'][:20]}</code>\n  💾 {ram}MB | ⚡ {cpu}%\n\n"
                m.add(types.InlineKeyboardButton(f"⚙️ {b['bot_name'][:15]}", callback_data=f"bot_detail:{b['bot_id']}"))
            if not active:
                t += "No bots running!"
            m.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
            safe_edit(t + "━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "menu_notif":
            safe_answer(call.id)
            db.mark_read(uid)
            notifs = db.get_notifs(uid, 10)
            t = f"🔔 <b>NOTIFICATIONS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for n in notifs:
                t += f"📌 <b>{n.get('title', 'Notice')}</b>\n{n.get('message', '')}\n📅 {str(n.get('created_at', ''))[:16]}\n\n"
            if not notifs:
                t += "No notifications!"
            safe_edit(t + "━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=back_btn())

        elif data == "menu_support":
            safe_answer(call.id)
            state.set_state(uid, {'action': 'ticket'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="go_home"))
            safe_edit(
                f"🎫 <b>CREATE SUPPORT TICKET</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📝 Describe your issue below:\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=m
            )

        elif data == "menu_settings":
            safe_answer(call.id)
            u = db.get_user(uid)
            t = (
                f"⚙️ <b>SETTINGS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🆔 ID: <code>{uid}</code>\n"
                f"📛 Name: {u.get('full_name', '?') if u else '?'}\n"
                f"👤 @{u.get('username', 'N/A') if u else 'N/A'}\n"
                f"🔑 Ref Code: <code>{u.get('referral_code', '?') if u else '?'}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            safe_edit(t, chat_id, msg_id, reply_markup=back_btn())

        elif data == "menu_speed":
            safe_answer(call.id)
            ss = sys_stats()
            # FIX: Measure real latency without blocking main thread
            start_t = time.time()
            try:
                import requests as _req
                _req.get(f"https://api.telegram.org/bot{TOKEN}/getMe", timeout=5)
                latency = round((time.time() - start_t) * 1000, 2)
            except Exception:
                latency = round((time.time() - start_t) * 1000, 2)
            safe_edit(
                f"⚡ <b>SPEED TEST</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🏓 Telegram Latency: {latency}ms\n⏱️ Uptime: {ss['up']}\n"
                f"💻 CPU: {ss['cpu']}% | RAM: {ss['mem']}%\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=back_btn()
            )

        # ─── BOT OPERATIONS ───
        elif data.startswith("bot_detail:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            bd = db.get_bot(bid)
            if not bd:
                return safe_answer(call.id, "❌ Bot not found!", show_alert=True)
            sk = f"{bd['user_id']}_{bd['bot_name']}"
            rn = is_running(sk)
            ram, cpu = bot_res(sk) if rn else (0, 0)
            uptime_str = "—"
            if rn and sk in bot_scripts:
                st = bot_scripts[sk].get('start_time')
                if st:
                    uptime_str = str(datetime.now() - st).split('.')[0]
            icon = "🐍" if bd['file_type'] == 'py' else "🟨"
            t = (
                f"{icon} <b>{bd['bot_name'][:22]}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🆔 Bot ID: #{bid}\n📄 Entry: <code>{bd['entry_file']}</code>\n"
                f"🔤 Type: {bd['file_type'].upper()}\n"
                f"📊 Status: {'🟢 Running' if rn else '🔴 Stopped'}\n"
                f"💾 RAM: {ram}MB | ⚡ CPU: {cpu}%\n"
                f"⏱️ Uptime: {uptime_str}\n"
                f"🔄 Restarts: {bd.get('total_restarts', 0)}\n"
                f"📅 Created: {str(bd.get('created_at', '?'))[:10]}\n━━━━━━━━━━━━━━━━━━━━"
            )
            safe_edit(t, chat_id, msg_id, reply_markup=bot_action_kb(bid, rn))
            safe_answer(call.id)

        elif data.startswith("bot_start:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            bd = db.get_bot(bid)
            if not bd:
                return safe_answer(call.id, "❌ Not found!", show_alert=True)
            if not db.is_active(bd['user_id']):
                return safe_answer(call.id, "⚠️ Subscription expired!", show_alert=True)
            sk = f"{bd['user_id']}_{bd['bot_name']}"
            if is_running(sk):
                return safe_answer(call.id, "⚠️ Already running!", show_alert=True)
            safe_answer(call.id, "🚀 Starting...")
            threading.Thread(target=run_bot_script, args=(bid, chat_id), daemon=True).start()

        elif data.startswith("bot_stop:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            bd = db.get_bot(bid)
            if not bd:
                return safe_answer(call.id, "❌ Not found!", show_alert=True)
            sk = f"{bd['user_id']}_{bd['bot_name']}"
            if sk in bot_scripts:
                kill_tree(bot_scripts[sk])
                cleanup_script(sk)
            db.update_bot(bid, status='stopped', last_stopped=datetime.now().isoformat())
            safe_answer(call.id, "✅ Stopped!")
            # Show bot detail directly without recursive call
            rn = False
            icon = "🐍" if bd['file_type'] == 'py' else "🟨"
            t = (
                f"{icon} <b>{bd['bot_name'][:22]}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🆔 Bot ID: #{bid}\n📄 Entry: <code>{bd['entry_file']}</code>\n"
                f"🔤 Type: {bd['file_type'].upper()}\n"
                f"📊 Status: 🔴 Stopped\n"
                f"💾 RAM: 0MB | ⚡ CPU: 0%\n"
                f"⏱️ Uptime: —\n"
                f"🔄 Restarts: {bd.get('total_restarts', 0)}\n"
                f"📅 Created: {str(bd.get('created_at', '?'))[:10]}\n━━━━━━━━━━━━━━━━━━━━"
            )
            safe_edit(t, chat_id, msg_id, reply_markup=bot_action_kb(bid, rn))

        elif data.startswith("bot_restart:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            bd = db.get_bot(bid)
            if not bd:
                return safe_answer(call.id, "❌ Not found!", show_alert=True)
            sk = f"{bd['user_id']}_{bd['bot_name']}"
            if sk in bot_scripts:
                kill_tree(bot_scripts[sk])
                cleanup_script(sk)
            db.update_bot(bid, total_restarts=bd.get('total_restarts', 0) + 1)
            time.sleep(2)
            safe_answer(call.id, "🔄 Restarting...")
            threading.Thread(target=run_bot_script, args=(bid, chat_id), daemon=True).start()

        elif data.startswith("bot_logs:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            bd = db.get_bot(bid)
            if not bd:
                return safe_answer(call.id, "❌ Bot not found!", show_alert=True)
            # Check ownership
            if bd['user_id'] != uid and not state.is_admin(uid):
                return safe_answer(call.id, "❌ Access denied!", show_alert=True)
            sk = f"{bd['user_id']}_{bd['bot_name']}"
            lp = os.path.join(LOGS_DIR, f"{sk}.log")
            logs = "📭 No logs available."
            if os.path.exists(lp):
                try:
                    with open(lp, 'r', encoding='utf-8', errors='ignore') as f:
                        raw = f.read()
                    if raw.strip():
                        # Get last 3000 chars of log to fit in Telegram message
                        logs = raw[-3000:]
                    else:
                        logs = "📭 Log file is empty."
                except Exception as log_err:
                    logs = f"❌ Error reading log: {str(log_err)[:100]}"
            # Escape HTML in logs to prevent parse errors
            import html as _html
            logs_escaped = _html.escape(logs)
            m = types.InlineKeyboardMarkup(row_width=2)
            m.add(
                types.InlineKeyboardButton("🔄 Refresh", callback_data=f"bot_logs:{bid}"),
                types.InlineKeyboardButton("🗑 Clear Logs", callback_data=f"bot_clearlogs:{bid}")
            )
            m.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"bot_detail:{bid}"))
            log_text = f"📋 <b>Logs — Bot #{bid}</b>\n<code>{bd['bot_name'][:20]}</code>\n━━━━━━━━━━━━━━━━━━━━\n\n<code>{logs_escaped}</code>"
            # Telegram message limit is 4096 chars
            if len(log_text) > 4000:
                log_text = log_text[:3950] + "\n...</code>"
            safe_edit(log_text, chat_id, msg_id, reply_markup=m)
            safe_answer(call.id)

        elif data.startswith("bot_clearlogs:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            bd = db.get_bot(bid)
            if bd:
                sk = f"{bd['user_id']}_{bd['bot_name']}"
                lp = os.path.join(LOGS_DIR, f"{sk}.log")
                try:
                    with open(lp, 'w') as f:
                        f.write("")
                except Exception:
                    pass
            safe_answer(call.id, "🗑 Logs cleared!")
            # Show empty log directly without recursive call
            m = types.InlineKeyboardMarkup(row_width=2)
            m.add(
                types.InlineKeyboardButton("🔄 Refresh", callback_data=f"bot_logs:{bid}"),
                types.InlineKeyboardButton("🗑 Clear Logs", callback_data=f"bot_clearlogs:{bid}")
            )
            m.add(types.InlineKeyboardButton("🔙 Back", callback_data=f"bot_detail:{bid}"))
            safe_edit(f"📋 <b>Logs — Bot #{bid}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n<code>📭 Logs cleared.</code>", chat_id, msg_id, reply_markup=m)

        elif data.startswith("bot_del:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            m = types.InlineKeyboardMarkup(row_width=2)
            m.add(
                types.InlineKeyboardButton("✅ Yes, Delete", callback_data=f"bot_confirm_del:{bid}"),
                types.InlineKeyboardButton("❌ Cancel", callback_data=f"bot_detail:{bid}")
            )
            safe_edit(f"🗑 <b>Delete Bot #{bid}?</b>\n\n⚠️ This cannot be undone!", chat_id, msg_id, reply_markup=m)
            safe_answer(call.id)

        elif data.startswith("bot_confirm_del:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            bd = db.get_bot(bid)
            if bd:
                sk = f"{bd['user_id']}_{bd['bot_name']}"
                if sk in bot_scripts:
                    kill_tree(bot_scripts[sk])
                    cleanup_script(sk)
                if os.path.isdir(bd['file_path']):
                    shutil.rmtree(bd['file_path'], ignore_errors=True)
                db.del_bot(bid)
            safe_answer(call.id, "✅ Bot deleted!")
            # Show my bots directly without recursive call
            bots_list = db.get_bots(uid)
            pl = db.get_plan(uid)
            mx = '♾️' if pl['max_bots'] == -1 else str(pl['max_bots'])
            if not bots_list:
                m2 = types.InlineKeyboardMarkup(row_width=2)
                m2.add(types.InlineKeyboardButton("📤 Deploy Bot", callback_data="menu_deploy"))
                m2.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
                safe_edit(f"📭 <b>No bots yet!</b>\n\nDeploy your first bot!\n📦 Slots: 0/{mx}\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m2)
            else:
                rn2 = sum(1 for b in bots_list if bot_running(uid, b['bot_name']))
                t2 = f"🤖 <b>My Bots</b> ({len(bots_list)})\n🟢 Running: {rn2} | 🔴 Stopped: {len(bots_list) - rn2}\n📦 Limit: {mx}\n━━━━━━━━━━━━━━━━━━━━\n\n"
                m2 = types.InlineKeyboardMarkup(row_width=1)
                for b in bots_list:
                    r2 = bot_running(uid, b['bot_name'])
                    ic2 = "🐍" if b['file_type'] == 'py' else "🟨"
                    st_icon2 = "🟢" if r2 else "🔴"
                    t2 += f"{st_icon2} {ic2} <code>{b['bot_name'][:20]}</code> — #{b['bot_id']}\n"
                    m2.add(types.InlineKeyboardButton(f"{st_icon2} {ic2} {b['bot_name'][:15]} — #{b['bot_id']}", callback_data=f"bot_detail:{b['bot_id']}"))
                m2.add(types.InlineKeyboardButton("📤 Deploy New Bot", callback_data="menu_deploy"))
                m2.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
                safe_edit(t2, chat_id, msg_id, reply_markup=m2)

        elif data.startswith("bot_res:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            bd = db.get_bot(bid)
            if not bd:
                return safe_answer(call.id, "❌ Not found!", show_alert=True)
            sk = f"{bd['user_id']}_{bd['bot_name']}"
            rn = is_running(sk)
            ram, cpu = bot_res(sk) if rn else (0, 0)
            uptime_str = "—"
            if rn and sk in bot_scripts:
                st_t = bot_scripts[sk].get('start_time')
                if st_t:
                    uptime_str = str(datetime.now() - st_t).split('.')[0]
            m = types.InlineKeyboardMarkup(row_width=2)
            m.add(
                types.InlineKeyboardButton("🔄 Refresh", callback_data=f"bot_res:{bid}"),
                types.InlineKeyboardButton("🔙 Back", callback_data=f"bot_detail:{bid}")
            )
            safe_edit(
                f"📊 <b>Resources — Bot #{bid}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🟢 Status: {'Running' if rn else '🔴 Stopped'}\n"
                f"💾 RAM: {ram} MB\n"
                f"⚡ CPU: {cpu}%\n"
                f"⏱️ Uptime: {uptime_str}\n"
                f"━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=m
            )
            safe_answer(call.id)

        elif data.startswith("bot_redetect:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            bd = db.get_bot(bid)
            if not bd:
                return safe_answer(call.id, "❌!", show_alert=True)
            wd = bd['file_path'] if os.path.isdir(bd['file_path']) else user_folder(bd['user_id'])
            entry, ft, rp = det.report(wd)
            if entry:
                db.update_bot(bid, entry_file=entry, file_type=ft)
                m = types.InlineKeyboardMarkup(row_width=2)
                m.add(
                    types.InlineKeyboardButton("▶️ Start", callback_data=f"bot_start:{bid}"),
                    types.InlineKeyboardButton("🔙 Back", callback_data=f"bot_detail:{bid}")
                )
                safe_edit(f"🔍 <b>Re-Detection Complete</b>\n\n{rp}\n\n✅ Entry file updated!", chat_id, msg_id, reply_markup=m)
            else:
                safe_edit(f"❌ <b>Auto-detect failed!</b>\n\nNo runnable files found.", chat_id, msg_id, reply_markup=back_btn(f"bot_detail:{bid}", "🔙 Back"))
            safe_answer(call.id)

        elif data.startswith("bot_dl:"):
            bid = data.split(":")[1]  # ObjectId string (MongoDB) or int string (SQLite) — keep as str
            bd = db.get_bot(bid)
            if not bd:
                return safe_answer(call.id, "❌!", show_alert=True)
            fp = os.path.join(bd['file_path'], bd['entry_file']) if os.path.isdir(bd['file_path']) else os.path.join(user_folder(bd['user_id']), bd['bot_name'])
            if os.path.exists(fp):
                try:
                    with open(fp, 'rb') as f:
                        bot.send_document(uid, f, caption=f"📄 {bd['bot_name']}")
                except:
                    safe_send(uid, "❌ Could not send file.")
            else:
                safe_send(uid, "❌ File not found on server.")
            safe_answer(call.id, "📥 Sending...")

        # ─── REFERRAL ───
        elif data.startswith("ref_copy:"):
            rc = data.split(":", 1)[1]
            lnk = f"https://t.me/{BOT_USERNAME}?start={rc}"
            safe_answer(call.id)
            safe_send(uid, f"📋 <b>Your Referral Link:</b>\n\n<code>{lnk}</code>\n\n👆 Tap to copy!")

        elif data == "ref_list":
            refs = db.user_refs(uid)
            t = f"📋 <b>Your Referrals ({len(refs)})</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for r in refs[:20]:
                ru = db.get_user(r['referred_id'])
                name = ru.get('full_name', str(r['referred_id'])) if ru else str(r['referred_id'])
                t += f"  👤 {name} — +{r.get('commission', 0)} BDT\n    📅 {str(r.get('created_at', ''))[:10]}\n\n"
            if not refs:
                t += "No referrals yet!"
            safe_edit(t + "━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=back_btn("menu_ref", "🔙 Referral"))
            safe_answer(call.id)

        elif data == "ref_board":
            lb = db.ref_board(10)
            t = f"🏆 <b>Referral Leaderboard</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            medals = ['🥇', '🥈', '🥉']
            for i, l in enumerate(lb):
                icon = medals[i] if i < 3 else f"  #{i + 1}"
                t += f"{icon} {l.get('full_name', '?')} — {l.get('referral_count', 0)} refs ({l.get('referral_earnings', 0)} BDT)\n"
            if not lb:
                t += "No referrals yet!\n"
            safe_edit(t + "\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=back_btn("menu_ref", "🔙 Referral"))
            safe_answer(call.id)

        # ─── PLAN & PAYMENT ───
        elif data.startswith("plan_select:"):
            pk = data.split(":")[1]
            p = PLAN_LIMITS.get(pk)
            if not p:
                return safe_answer(call.id, "❌ Plan not found!", show_alert=True)
            slots = '♾️' if p['max_bots'] == -1 else str(p['max_bots'])
            safe_edit(
                f"{p['name']}\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🤖 Bot Slots: {slots}\n💾 RAM: {p['ram']}MB\n🔄 Auto Restart: {'✅' if p['auto_restart'] else '❌'}\n"
                f"💰 Price: <b>{p['price']} BDT/month</b>\n\nSelect payment method:\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=pay_method_kb(pk)
            )
            safe_answer(call.id)

        elif data.startswith("pay_method:"):
            parts = data.split(":")
            pk = parts[1]
            mk = parts[2]
            p = PLAN_LIMITS.get(pk)
            pm_info = PAYMENT_METHODS.get(mk)
            if not p or not pm_info:
                return safe_answer(call.id, "❌ Error!", show_alert=True)
            state.set_pay_state(uid, {'step': 'wait_trx', 'plan': pk, 'method': mk, 'amount': p['price']})
            safe_edit(
                f"{pm_info['icon']} <b>{pm_info['name']} Payment</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📱 Send to: <code>{pm_info['number']}</code>\n📝 Type: {pm_info['type']}\n"
                f"💰 Amount: <b>{p['price']} BDT</b>\n📦 Plan: {p['name']}\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📤 <b>Now send the Transaction ID below:</b>",
                chat_id, msg_id
            )
            safe_answer(call.id)

        elif data.startswith("pay_wallet:"):
            pk = data.split(":")[1]
            u = db.get_user(uid)
            p = PLAN_LIMITS.get(pk)
            if not u or not p:
                return safe_answer(call.id, "❌ Error!", show_alert=True)
            if u.get('wallet_balance', 0) < p['price']:
                return safe_answer(call.id, f"❌ Insufficient balance!\nNeed: {p['price']} BDT | Have: {u.get('wallet_balance', 0)} BDT", show_alert=True)
            db.wallet_tx(uid, p['price'], 'purchase', f"Plan: {pk}")
            db.set_sub(uid, pk, 0 if pk == 'lifetime' else 30)
            safe_answer(call.id, "✅ Plan activated!", show_alert=True)
            safe_edit(
                f"✅ <b>PLAN ACTIVATED!</b>\n\n📦 {p['name']}\n💰 {p['price']} BDT deducted\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=back_btn()
            )

        elif data.startswith("pay_approve:"):
            if not state.is_admin(uid):
                return
            pid = data.split(":")[1]  # ObjectId string — keep as str
            p = db.approve_pay(pid, uid)
            if p:
                safe_answer(call.id, "✅ Payment approved!")
                safe_send(p['user_id'],
                    f"✅ <b>PAYMENT APPROVED!</b>\n\n"
                    f"🆔 #{pid}\n📦 Plan: {PLAN_LIMITS.get(p['plan'], {}).get('name', p['plan'])}\n"
                    f"💰 {p['amount']} BDT\n{BRAND_FOOTER}"
                )
                db.admin_log(uid, 'approve_pay', p['user_id'], f"#{pid}")
            else:
                safe_answer(call.id, "❌ Payment not found!", show_alert=True)

        elif data.startswith("pay_reject:"):
            if not state.is_admin(uid):
                return
            pid = data.split(":")[1]  # ObjectId string — keep as str
            p_info = db.get_pay(pid)
            db.reject_pay(pid, uid)
            safe_answer(call.id, "❌ Payment rejected!")
            if p_info:
                safe_send(p_info['user_id'],
                    f"❌ <b>PAYMENT REJECTED!</b>\n\n🆔 #{pid}\n💰 {p_info['amount']} BDT\n\nContact: {YOUR_USERNAME}\n{BRAND_FOOTER}"
                )
            db.admin_log(uid, 'reject_pay', det=f"#{pid}")

        # ─── ADMIN CALLBACKS ───
        elif data == "adm_stats":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            s = db.stats()
            ss = sys_stats()
            safe_edit(
                f"📊 <b>ADMIN STATISTICS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👥 Total Users: {s['users']} (+{s['today']} today)\n"
                f"🤖 Total Bots: {s['bots']}\n💎 Active Subs: {s['active_subs']}\n"
                f"🚫 Banned: {s['banned']}\n💳 Pending: {s['pending']}\n"
                f"💰 Revenue: {s['revenue']} BDT\n\n"
                f"🖥 System:\n  CPU: {ss['cpu']}% | RAM: {ss['mem']}%\n  Disk: {ss['disk']}%\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=back_btn("menu_admin", "🔙 Admin")
            )

        elif data == "adm_payments":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            pays = db.pending_pay()
            t = f"💳 <b>Pending Payments ({len(pays)})</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            m = types.InlineKeyboardMarkup(row_width=2)
            for p in pays[:10]:
                pu = db.get_user(p['user_id'])
                t += f"  #{p['payment_id']} — {p['amount']} BDT | {p['plan']} | {p.get('method','?')}\n  👤 {pu.get('full_name','?') if pu else '?'} (<code>{p['user_id']}</code>)\n\n"
                m.add(
                    types.InlineKeyboardButton(f"✅ #{p['payment_id']}", callback_data=f"pay_approve:{p['payment_id']}"),
                    types.InlineKeyboardButton(f"❌ #{p['payment_id']}", callback_data=f"pay_reject:{p['payment_id']}")
                )
            if not pays:
                t += "No pending payments!"
            m.add(types.InlineKeyboardButton("🔙 Admin", callback_data="menu_admin"))
            safe_edit(t + "━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_broadcast":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'broadcast'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_admin"))
            safe_edit(f"📢 <b>BROADCAST</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Send your message now:\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_addsub":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'adm_addsub_uid'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_admin"))
            safe_edit(f"➕ <b>ADD SUBSCRIPTION</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Send the User ID:\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data.startswith("adm_setplan:"):
            if not state.is_admin(uid):
                return
            parts = data.split(":")
            plan = parts[1]
            target = int(parts[2])
            m = types.InlineKeyboardMarkup(row_width=3)
            m.add(
                types.InlineKeyboardButton("7 Days", callback_data=f"adm_quicksub:{plan}:{target}:7"),
                types.InlineKeyboardButton("30 Days", callback_data=f"adm_quicksub:{plan}:{target}:30"),
                types.InlineKeyboardButton("90 Days", callback_data=f"adm_quicksub:{plan}:{target}:90")
            )
            m.add(
                types.InlineKeyboardButton("180 Days", callback_data=f"adm_quicksub:{plan}:{target}:180"),
                types.InlineKeyboardButton("365 Days", callback_data=f"adm_quicksub:{plan}:{target}:365"),
                types.InlineKeyboardButton("♾ Lifetime", callback_data=f"adm_quicksub:lifetime:{target}:0")
            )
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_admin"))
            safe_edit(
                f"📅 <b>Select Duration</b>\n\n👤 User: <code>{target}</code>\n📦 Plan: {PLAN_LIMITS.get(plan, {}).get('name', plan)}\n\nChoose below:",
                chat_id, msg_id, reply_markup=m
            )
            safe_answer(call.id)

        elif data.startswith("adm_quicksub:"):
            if not state.is_admin(uid):
                return
            parts = data.split(":")
            plan = parts[1]
            target = int(parts[2])
            days = int(parts[3])
            if days == 0 or plan == 'lifetime':
                db.set_sub(target, 'lifetime')
                plan_name = "👑 Lifetime"
                dur_text = "Lifetime"
            else:
                db.set_sub(target, plan, days)
                plan_name = PLAN_LIMITS.get(plan, {}).get('name', plan)
                dur_text = f"{days} days"
            safe_answer(call.id, "✅ Done!")
            safe_edit(
                f"✅ <b>Subscription Added!</b>\n\n👤 User: <code>{target}</code>\n📦 Plan: {plan_name}\n📅 Duration: {dur_text}",
                chat_id, msg_id, reply_markup=back_btn("menu_admin", "🔙 Admin")
            )
            db.admin_log(uid, 'add_sub', target, f"{plan}/{dur_text}")
            safe_send(target, f"🎉 <b>Plan Upgraded!</b>\n📦 {plan_name}\n📅 {dur_text}\n{BRAND_FOOTER}")

        elif data == "adm_remsub":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'adm_remsub_uid'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_admin"))
            safe_edit(f"➖ <b>REMOVE SUBSCRIPTION</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Send the User ID:\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_ban":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'adm_ban_uid'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_admin"))
            safe_edit(f"🚫 <b>BAN USER</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Send: USER_ID [REASON]\nExample: 123456789 Spam\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_unban":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'adm_unban_uid'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_admin"))
            safe_edit(f"✅ <b>UNBAN USER</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Send the User ID:\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data.startswith("adm_ban_direct:"):
            if not state.is_admin(uid):
                return
            target = int(data.split(":")[1])
            db.ban(target, "Banned from admin panel")
            db.admin_log(uid, 'ban', target)
            for b in db.get_bots(target):
                sk = f"{target}_{b['bot_name']}"
                if sk in bot_scripts:
                    kill_tree(bot_scripts[sk])
                    cleanup_script(sk)
                db.update_bot(b['bot_id'], status='stopped')
            safe_answer(call.id, "🚫 Banned!")
            safe_send(target, f"🚫 <b>You have been banned!</b>\nContact {YOUR_USERNAME}")

        elif data.startswith("adm_unban_direct:"):
            if not state.is_admin(uid):
                return
            target = int(data.split(":")[1])
            db.unban(target)
            db.admin_log(uid, 'unban', target)
            safe_answer(call.id, "✅ Unbanned!")
            safe_send(target, "✅ You have been unbanned!")

        elif data == "adm_channels":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            channels = db.get_all_channels()
            t = f"📢 <b>Force Subscribe Channels</b>\nStatus: {'🟢 ON' if state.force_sub_enabled else '🔴 OFF'}\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for ch in channels:
                t += f"  {'🟢' if ch['is_active'] else '🔴'} @{ch['channel_username']} — {ch['channel_name']}\n"
            if not channels:
                t += "  No custom channels. Default: @developer_apon_07\n"
            t += "\n━━━━━━━━━━━━━━━━━━━━"
            safe_edit(t, chat_id, msg_id, reply_markup=channels_manage_kb())

        elif data.startswith("ch_toggle:"):
            if not state.is_admin(uid):
                return
            try:
                raw = data.split(":", 1)[1]
                # Try int (SQLite channel_id), else pass as string username (MongoDB)
                try:
                    cid_ch = int(raw)
                except ValueError:
                    cid_ch = raw
                ns = db.toggle_channel(cid_ch)
                if ns is not None:
                    safe_answer(call.id, f"{'🟢 Enabled' if ns else '🔴 Disabled'}!")
                else:
                    safe_answer(call.id, "❌ Channel not found!")
            except Exception as e:
                safe_answer(call.id, "❌ Error!")
            # Refresh channels panel directly
            channels_r = db.get_all_channels()
            t_ch = f"📢 <b>Force Subscribe Channels</b>\nStatus: {'🟢 ON' if state.force_sub_enabled else '🔴 OFF'}\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for ch in channels_r:
                t_ch += f"  {'🟢' if ch['is_active'] else '🔴'} @{ch['channel_username']} — {ch['channel_name']}\n"
            if not channels_r:
                t_ch += "  No custom channels. Default: @developer_apon_07\n"
            t_ch += "\n━━━━━━━━━━━━━━━━━━━━"
            safe_edit(t_ch, chat_id, msg_id, reply_markup=channels_manage_kb())

        elif data == "ch_add":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'ch_add'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="adm_channels"))
            safe_edit(f"➕ <b>Add Channel</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Send: @username [Channel Name]\nExample: @mychannel My Channel\n\n⚠️ Bot must be admin!\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "ch_remove":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'ch_remove'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="adm_channels"))
            safe_edit(f"🗑 <b>Remove Channel</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Send the channel username:\nExample: developer_apon_07\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_give":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'adm_give_balance'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_admin"))
            safe_edit(f"💰 <b>GIVE BALANCE</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Send: USER_ID AMOUNT\nExample: 123456789 100\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_userinfo":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'adm_userinfo_uid'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_admin"))
            safe_edit(f"🔍 <b>User Info</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Send the User ID:\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_notify":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'adm_notify_uid'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="menu_admin"))
            safe_edit(f"🔔 <b>Send Notification</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Send: USER_ID MESSAGE\nExample: 123456789 Your bot is ready!\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_fsub_toggle":
            if not state.is_admin(uid):
                return
            state.force_sub_enabled = not state.force_sub_enabled
            st = "🟢 ON" if state.force_sub_enabled else "🔴 OFF"
            safe_answer(call.id, f"Force Subscribe: {st}", show_alert=True)
            db.admin_log(uid, 'toggle_fsub', det=st)
            # Refresh admin panel directly
            s_adm = db.stats()
            rn_adm = len([k for k in bot_scripts if is_running(k)])
            tickets_adm = len(db.open_tickets())
            safe_edit(
                f"👑 <b>ADMIN PANEL</b>\n{BRAND_TAG}\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👥 Total Users: {s_adm['users']} (+{s_adm['today']} today)\n"
                f"🤖 Running Bots: {rn_adm}\n💎 Active Subs: {s_adm['active_subs']}\n"
                f"🚫 Banned: {s_adm['banned']}\n💳 Pending Payments: {s_adm['pending']}\n"
                f"🎫 Open Tickets: {tickets_adm}\n💰 Total Revenue: {s_adm['revenue']} BDT\n\n"
                f"🔐 Force Sub: {'🟢 ON' if state.force_sub_enabled else '🔴 OFF'}\n"
                f"🔒 Bot Lock: {'🔒 LOCKED' if state.bot_locked else '🔓 OPEN'}\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=admin_kb()
            )

        elif data == "adm_lock_toggle":
            if not state.is_admin(uid):
                return
            state.bot_locked = not state.bot_locked
            st = "🔒 LOCKED" if state.bot_locked else "🔓 OPEN"
            safe_answer(call.id, f"Bot: {st}", show_alert=True)
            db.admin_log(uid, 'toggle_lock', det=st)
            # Refresh admin panel directly
            s_adm = db.stats()
            rn_adm = len([k for k in bot_scripts if is_running(k)])
            tickets_adm = len(db.open_tickets())
            safe_edit(
                f"👑 <b>ADMIN PANEL</b>\n{BRAND_TAG}\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👥 Total Users: {s_adm['users']} (+{s_adm['today']} today)\n"
                f"🤖 Running Bots: {rn_adm}\n💎 Active Subs: {s_adm['active_subs']}\n"
                f"🚫 Banned: {s_adm['banned']}\n💳 Pending Payments: {s_adm['pending']}\n"
                f"🎫 Open Tickets: {tickets_adm}\n💰 Total Revenue: {s_adm['revenue']} BDT\n\n"
                f"🔐 Force Sub: {'🟢 ON' if state.force_sub_enabled else '🔴 OFF'}\n"
                f"🔒 Bot Lock: {'🔒 LOCKED' if state.bot_locked else '🔓 OPEN'}\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=admin_kb()
            )

        elif data == "adm_stopall":
            if not state.is_admin(uid):
                return
            stopped = 0
            for sk in list(bot_scripts.keys()):
                i = bot_scripts.get(sk)
                if i:
                    kill_tree(i)
                    bid_inner = i.get('bot_id')
                    if bid_inner:
                        db.update_bot(bid_inner, status='stopped')
                    cleanup_script(sk)
                    stopped += 1
            safe_answer(call.id, f"🛑 Stopped {stopped} bots!", show_alert=True)
            db.admin_log(uid, 'stop_all', det=f"stopped:{stopped}")
            # Refresh admin panel directly
            s_adm = db.stats()
            rn_adm = len([k for k in bot_scripts if is_running(k)])
            tickets_adm = len(db.open_tickets())
            safe_edit(
                f"👑 <b>ADMIN PANEL</b>\n{BRAND_TAG}\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👥 Total Users: {s_adm['users']} (+{s_adm['today']} today)\n"
                f"🤖 Running Bots: {rn_adm}\n💎 Active Subs: {s_adm['active_subs']}\n"
                f"🚫 Banned: {s_adm['banned']}\n💳 Pending Payments: {s_adm['pending']}\n"
                f"🎫 Open Tickets: {tickets_adm}\n💰 Total Revenue: {s_adm['revenue']} BDT\n\n"
                f"🔐 Force Sub: {'🟢 ON' if state.force_sub_enabled else '🔴 OFF'}\n"
                f"🔒 Bot Lock: {'🔒 LOCKED' if state.bot_locked else '🔓 OPEN'}\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=admin_kb()
            )

        elif data == "adm_system":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            ss = sys_stats()
            rn = len([k for k in bot_scripts if is_running(k)])
            safe_edit(
                f"🖥 <b>SYSTEM INFO</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"⚡ CPU: {ss['cpu']}%\n💾 RAM: {ss['mem']}% ({ss['mem_used']}/{ss['mem_total']})\n"
                f"💿 Disk: {ss['disk']}% ({ss['disk_used']}/{ss['disk_total']})\n"
                f"⏱️ Uptime: {ss['up']}\n🤖 Running Bots: {rn}\n━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=back_btn("menu_admin", "🔙 Admin")
            )

        elif data == "adm_backup":
            if not state.is_admin(uid):
                return
            try:
                from database import DB_PATH
                ts = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_path = os.path.join(BACKUP_DIR, f"bk_{ts}.db")
                if os.path.exists(DB_PATH):
                    shutil.copy2(DB_PATH, backup_path)
                    with open(backup_path, 'rb') as f:
                        bot.send_document(uid, f, caption=f"💾 Backup {ts}")
                    safe_answer(call.id, "✅ Backup created!")
                else:
                    safe_answer(call.id, "ℹ️ Using MongoDB, no local backup needed.", show_alert=True)
                db.admin_log(uid, 'backup')
            except Exception as e:
                safe_answer(call.id, f"❌ Backup error: {str(e)[:50]}", show_alert=True)

        elif data == "adm_logs":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            logs = db.get_admin_logs(15)
            t = f"📜 <b>Admin Logs</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for l in logs:
                t += f"  🔹 {l.get('action', '?')} by <code>{l.get('admin_id', '?')}</code>\n  📅 {str(l.get('created_at', ''))[:16]}\n\n"
            if not logs:
                t += "No logs."
            safe_edit(t + "━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=back_btn("menu_admin", "🔙 Admin"))

        elif data == "adm_promo":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            promos = db.all_promos()
            t = f"🎟 <b>Promo Codes ({len(promos)})</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for p in promos[:15]:
                st = "🟢" if p.get('is_active') else "🔴"
                t += f"  {st} <code>{p.get('code', '?')}</code> — {p.get('discount_pct', 0)}% off — {p.get('used_count', 0)}/{p.get('max_uses', 0)} used\n"
            if not promos:
                t += "  No promo codes yet.\n"
            m = types.InlineKeyboardMarkup(row_width=1)
            m.add(types.InlineKeyboardButton("➕ Create Promo", callback_data="adm_promo_create"))
            m.add(types.InlineKeyboardButton("🔙 Admin", callback_data="menu_admin"))
            safe_edit(t + "\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_promo_create":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            state.set_state(uid, {'action': 'adm_promo_create'})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="adm_promo"))
            safe_edit(f"🎟 <b>Create Promo Code</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Format: CODE DISCOUNT% MAX_USES\nExample: SAVE50 50 100\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_tickets":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            tickets = db.open_tickets()
            t = f"🎫 <b>Open Tickets ({len(tickets)})</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            m = types.InlineKeyboardMarkup(row_width=1)
            for ticket in tickets[:10]:
                tu = db.get_user(ticket['user_id'])
                t += f"  #{ticket['ticket_id']} — {ticket.get('subject', '?')}\n  👤 {tu.get('full_name', '?') if tu else '?'} | 📅 {str(ticket.get('created_at', ''))[:10]}\n\n"
                m.add(types.InlineKeyboardButton(f"💬 Reply #{ticket['ticket_id']}", callback_data=f"adm_ticket_reply:{ticket['ticket_id']}"))
            if not tickets:
                t += "No open tickets!"
            m.add(types.InlineKeyboardButton("🔙 Admin", callback_data="menu_admin"))
            safe_edit(t + "━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data.startswith("adm_ticket_reply:"):
            if not state.is_admin(uid):
                return
            tid = data.split(":")[1]  # ObjectId string — keep as str
            safe_answer(call.id)
            state.set_state(uid, {'action': 'ticket_reply', 'ticket_id': tid})
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("❌ Cancel", callback_data="adm_tickets"))
            safe_edit(f"💬 <b>Reply to Ticket #{tid}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📝 Type your reply now:\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=m)

        elif data == "adm_users":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            s = db.stats()
            users = db.get_all_users()
            t = (
                f"👥 <b>ALL USERS ({s['users']})</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💎 Active Subs: {s['active_subs']}\n🚫 Banned: {s['banned']}\n\n"
            )
            for u in users[:20]:
                icon = "🚫" if u.get('is_banned') else ("💎" if u.get('plan') != 'free' else "👤")
                t += f"  {icon} <code>{u['user_id']}</code> — {u.get('full_name', '?')[:15]} | {u.get('plan', 'free')}\n"
            if s['users'] > 20:
                t += f"\n  ... and {s['users'] - 20} more users"
            safe_edit(t + "\n━━━━━━━━━━━━━━━━━━━━", chat_id, msg_id, reply_markup=back_btn("menu_admin", "🔙 Admin"))

        # ─── CLEANUP ───
        elif data == "adm_cleanup":
            if not state.is_admin(uid):
                return
            safe_answer(call.id)
            m = types.InlineKeyboardMarkup(row_width=1)
            m.add(types.InlineKeyboardButton("🗑️ Delete Bot Files (disk)", callback_data="adm_cleanup_files"))
            m.add(types.InlineKeyboardButton("📜 Delete Log Files (disk)", callback_data="adm_cleanup_logs"))
            m.add(types.InlineKeyboardButton("💾 Delete Old Backups (keep 5)", callback_data="adm_cleanup_backups"))
            m.add(types.InlineKeyboardButton("❌ Delete Error Logs (DB)", callback_data="adm_cleanup_errlogs"))
            m.add(types.InlineKeyboardButton("🔔 Delete Old Notifications (30d)", callback_data="adm_cleanup_notifs"))
            m.add(types.InlineKeyboardButton("🔙 Admin Panel", callback_data="menu_admin"))
            safe_edit(
                f"🗑️ <b>CLEANUP STORAGE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"⚠️ Choose what to clean:\n\n"
                f"• <b>Bot Files</b> — deletes uploaded bot files from disk\n"
                f"• <b>Log Files</b> — clears all .log files\n"
                f"• <b>Old Backups</b> — keeps only last 5 backups\n"
                f"• <b>Error Logs</b> — clears error_logs from DB\n"
                f"• <b>Old Notifications</b> — deletes read notifs older than 30 days\n"
                f"━━━━━━━━━━━━━━━━━━━━",
                chat_id, msg_id, reply_markup=m
            )

        elif data == "adm_cleanup_files":
            if not state.is_admin(uid):
                return
            safe_answer(call.id, "🗑️ Deleting bot files...")
            try:
                n = db.cleanup_user_files()
                db.admin_log(uid, 'cleanup_files', det=f"deleted:{n}")
                safe_edit(
                    f"✅ <b>Bot Files Deleted!</b>\n\n🗑️ {n} items removed from disk.",
                    chat_id, msg_id, reply_markup=back_btn("adm_cleanup", "🔙 Cleanup")
                )
            except Exception as e:
                safe_edit(f"❌ Error: {str(e)[:200]}", chat_id, msg_id,
                          reply_markup=back_btn("adm_cleanup", "🔙 Cleanup"))

        elif data == "adm_cleanup_logs":
            if not state.is_admin(uid):
                return
            safe_answer(call.id, "📜 Clearing logs...")
            try:
                n = db.cleanup_old_logs()
                db.admin_log(uid, 'cleanup_logs', det=f"deleted:{n}")
                safe_edit(
                    f"✅ <b>Log Files Cleared!</b>\n\n🗑️ {n} log files deleted.",
                    chat_id, msg_id, reply_markup=back_btn("adm_cleanup", "🔙 Cleanup")
                )
            except Exception as e:
                safe_edit(f"❌ Error: {str(e)[:200]}", chat_id, msg_id,
                          reply_markup=back_btn("adm_cleanup", "🔙 Cleanup"))

        elif data == "adm_cleanup_backups":
            if not state.is_admin(uid):
                return
            safe_answer(call.id, "💾 Cleaning backups...")
            try:
                n = db.cleanup_old_backups(keep=5)
                db.admin_log(uid, 'cleanup_backups', det=f"deleted:{n}")
                safe_edit(
                    f"✅ <b>Old Backups Deleted!</b>\n\n🗑️ {n} old backups removed (kept last 5).",
                    chat_id, msg_id, reply_markup=back_btn("adm_cleanup", "🔙 Cleanup")
                )
            except Exception as e:
                safe_edit(f"❌ Error: {str(e)[:200]}", chat_id, msg_id,
                          reply_markup=back_btn("adm_cleanup", "🔙 Cleanup"))

        elif data == "adm_cleanup_errlogs":
            if not state.is_admin(uid):
                return
            safe_answer(call.id, "❌ Clearing error logs...")
            try:
                n = db.cleanup_error_logs_db()
                db.admin_log(uid, 'cleanup_errlogs', det=f"deleted:{n}")
                safe_edit(
                    f"✅ <b>Error Logs Cleared!</b>\n\n🗑️ {n} error records removed from DB.",
                    chat_id, msg_id, reply_markup=back_btn("adm_cleanup", "🔙 Cleanup")
                )
            except Exception as e:
                safe_edit(f"❌ Error: {str(e)[:200]}", chat_id, msg_id,
                          reply_markup=back_btn("adm_cleanup", "🔙 Cleanup"))

        elif data == "adm_cleanup_notifs":
            if not state.is_admin(uid):
                return
            safe_answer(call.id, "🔔 Cleaning notifications...")
            try:
                n = db.cleanup_old_notifications(days=30)
                db.admin_log(uid, 'cleanup_notifs', det=f"deleted:{n}")
                safe_edit(
                    f"✅ <b>Old Notifications Deleted!</b>\n\n🗑️ {n} old read notifications removed.",
                    chat_id, msg_id, reply_markup=back_btn("adm_cleanup", "🔙 Cleanup")
                )
            except Exception as e:
                safe_edit(f"❌ Error: {str(e)[:200]}", chat_id, msg_id,
                          reply_markup=back_btn("adm_cleanup", "🔙 Cleanup"))

        # ─── HELP CALLBACKS ───
        elif data.startswith("help_"):
            safe_answer(call.id)
            topic = data.replace("help_", "")
            help_texts = {
                "deploy": "📤 <b>HOW TO DEPLOY</b>\n\n1. Press 📤 Deploy Bot\n2. Send your .py, .js, or .zip file\n3. Bot auto-detects entry file\n4. Press ▶️ Start",
                "bots": "🤖 <b>MANAGING BOTS</b>\n\n• Start/Stop/Restart from My Bots\n• View real-time logs\n• Monitor RAM & CPU usage\n• Auto-restart on crash (paid plans)",
                "plans": "💎 <b>PLANS & PRICING</b>\n\n• 🆓 Free — 1 bot\n• 🟢 Starter — 2 bots — 99 BDT\n• ⭐ Basic — 5 bots — 199 BDT\n• 💎 Pro — 15 bots — 499 BDT\n• 🏢 Enterprise — 50 bots — 999 BDT\n• 👑 Lifetime — Unlimited — 1999 BDT",
                "payment": f"💳 <b>PAYMENT GUIDE</b>\n\n• bKash/Nagad/Rocket: 01775234802\n• Send Money → Get Transaction ID\n• Submit TRX in bot → Admin approves\n• Contact: {YOUR_USERNAME}",
                "referral": f"🎁 <b>REFERRAL SYSTEM</b>\n\n• Share your unique link\n• Earn +{REF_COMMISSION} BDT per referral\n• Get +{REF_BONUS_DAYS} days premium\n• Level up: Bronze → Diamond",
                "wallet": "💰 <b>WALLET GUIDE</b>\n\n• Earn from referrals automatically\n• Use wallet balance to pay for plans\n• Admin can add bonus balance\n• Check history in Wallet menu",
                "detect": "🔍 <b>AUTO DETECTION</b>\n\n• Supports main.py, app.py, bot.py\n• Reads package.json, Procfile\n• Falls back to scanning .py/.js files\n• Manual override via Re-detect",
                "files": "📦 <b>SUPPORTED FILES</b>\n\n• Python: .py, .zip with main.py\n• Node.js: .js, .zip with index.js\n• Config: .json, .env, .yml, .txt\n• Max size: 100MB",
                "faq": f"❓ <b>FAQ</b>\n\nQ: Free plan limit?\nA: 1 bot\n\nQ: Bot crashes?\nA: Check logs, ensure correct entry file\n\nQ: Payment not approved?\nA: Contact {YOUR_USERNAME}\n\nQ: Node.js supported?\nA: Yes! .js files supported",
                "trouble": "🛠 <b>TROUBLESHOOT</b>\n\n• Bot crashes: Check logs → Fix error\n• Entry not found: Use Re-detect\n• Module missing: Auto-installed on start\n• Still failing: Create support ticket",
                "commands": "/start — Main menu\n/help — Help center\n/id — Your user ID\n/ping — Bot status\n\nAdmin only:\n/admin — Admin panel\n/ban UID — Ban user\n/broadcast — Send to all\n/give UID AMOUNT — Add balance",
                "contact": f"📞 <b>CONTACT & SUPPORT</b>\n\n👨‍💻 Developer: {YOUR_USERNAME}\n📢 Channel: {UPDATE_CHANNEL}\n🎫 Or create a support ticket"
            }
            text = help_texts.get(topic, f"❓ Help topic: {topic}")
            safe_edit(text, chat_id, msg_id, reply_markup=back_help_btn())

        else:
            safe_answer(call.id, "⚠️ Unknown action!", show_alert=False)
            logger.warning(f"Unknown callback: {data} from {uid}")

    except Exception as e:
        logger.error(f"Callback error [{data}]: {e}", exc_info=True)
        forward_crash(f"callback:{data}", e, uid)
        safe_answer(call.id, "❌ An error occurred!", show_alert=True)
        try:
            safe_edit(f"❌ <b>Error occurred!</b>\n\nPlease try again.\n{BRAND_FOOTER}", chat_id, msg_id, reply_markup=back_btn())
        except:
            pass


# ═══════════════════════════════════════════════════
#  CLEANUP ON EXIT
# ═══════════════════════════════════════════════════
def cleanup():
    logger.info("🛑 Shutting down...")
    for sk in list(bot_scripts.keys()):
        i = bot_scripts.get(sk)
        if i:
            try:
                kill_tree(i)
            except:
                pass
            bid_inner = i.get('bot_id')
            if bid_inner:
                try:
                    db.update_bot(bid_inner, status='stopped')
                except:
                    pass
            cleanup_script(sk)
    logger.info("✅ Cleanup complete")


atexit.register(cleanup)


def signal_handler(sig, frame):
    cleanup()
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ═══════════════════════════════════════════════════
#  MAIN STARTUP
# ═══════════════════════════════════════════════════
def main():
    from config import MAIN_BOT_AUTO_RESTART_HOURS, FREE_BOT_MAX_HOURS
    logger.info("=" * 50)
    logger.info(f"  {BRAND_TAG}")
    logger.info("  Starting up...")
    logger.info("=" * 50)

    # ── Kill any existing polling instances (prevent 409 Conflict) ──
    try:
        import requests as _req
        _req.get(
            f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true",
            timeout=10
        )
        logger.info("✅ Webhook cleared")
        time.sleep(3)
    except Exception as e:
        logger.warning(f"Webhook clear failed: {e}")

    # Start Flask keep-alive
    keep_alive()
    logger.info("✅ Flask keep-alive started")

    # Start background threads
    threading.Thread(target=thread_monitor, daemon=True).start()
    logger.info("✅ Bot monitor started")

    threading.Thread(target=thread_backup, daemon=True).start()
    logger.info("✅ Auto-backup started")

    threading.Thread(target=thread_expiry, daemon=True).start()
    logger.info("✅ Expiry checker started")

    threading.Thread(target=thread_storage_monitor, daemon=True).start()
    logger.info("✅ Storage monitor started")

    threading.Thread(target=thread_daily_report, daemon=True).start()
    logger.info("✅ Daily report thread started")

    threading.Thread(target=thread_free_bot_limit, daemon=True).start()
    logger.info(f"✅ Free bot auto-stop started ({FREE_BOT_MAX_HOURS}h limit)")

    threading.Thread(target=thread_main_bot_restart, daemon=True).start()
    logger.info(f"✅ Main bot auto-restart started ({MAIN_BOT_AUTO_RESTART_HOURS}h)")

    # Auto-restart previously running bots
    try:
        from database import USE_MONGO, mongo_db
        if USE_MONGO and mongo_db is not None:
            all_bots = list(mongo_db['bots'].find({'status': 'running'}, {'_id': 0}))
        else:
            from database import DB_PATH
            import sqlite3 as _sqlite3
            _conn = _sqlite3.connect(DB_PATH)
            _conn.row_factory = _sqlite3.Row
            all_bots = [dict(r) for r in _conn.execute(
                "SELECT * FROM bots WHERE status='running'"
            ).fetchall()]
            _conn.close()

        logger.info(f"🔄 Found {len(all_bots)} bots to auto-restart")
        for b in all_bots:
            logger.info(f"🔄 Auto-restarting bot #{b['bot_id']}: {b['bot_name']}")
            db.update_bot(b['bot_id'], status='starting')
            threading.Thread(
                target=run_bot_script,
                args=(b['bot_id'], b['user_id']),
                daemon=True,
                name=f"autostart_{b['bot_id']}"
            ).start()
            time.sleep(1)
    except Exception as e:
        logger.error(f"Auto-restart error: {e}")
        forward_error("AUTO_RESTART", e)

    # Notify owner
    safe_send(OWNER_ID,
        f"🚀 <b>{BRAND_TAG} STARTED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ All systems online\n"
        f"📊 DB: {'MongoDB ✅' if __import__('database').USE_MONGO else 'SQLite ⚠️'}\n"
        f"🌐 Flask: OK\n"
        f"🔍 Monitor: OK\n"
        f"💾 Backup: OK\n"
        f"⏰ Auto-restart: every {MAIN_BOT_AUTO_RESTART_HOURS}h\n"
        f"🆓 Free bot limit: {FREE_BOT_MAX_HOURS}h\n"
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        reply_markup=main_menu_kb(OWNER_ID)
    )

    logger.info("🚀 Bot polling started!")

    # Start polling with auto-reconnect
    while True:
        try:
            bot.infinity_polling(
                timeout=60,
                long_polling_timeout=60,
                allowed_updates=['message', 'callback_query'],
                skip_pending=True
            )
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt — shutting down")
            break
        except Exception as e:
            logger.error(f"Polling error: {e}")
            forward_error("POLLING_CRASH", e)
            logger.info("🔄 Reconnecting in 10 seconds...")
            time.sleep(10)


if __name__ == '__main__':
    main()
