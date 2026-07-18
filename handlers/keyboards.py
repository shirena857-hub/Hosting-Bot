"""
KEYBOARDS MODULE — All inline keyboard builders
"""

from telebot import types
from config import PLAN_LIMITS, PAYMENT_METHODS
from core.state import state

# db is set after init
_db = None

def init_keyboards(db_instance):
    global _db
    _db = db_instance


def main_menu_kb(uid):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("🤖 My Bots", callback_data="menu_mybots"),
        types.InlineKeyboardButton("📤 Deploy Bot", callback_data="menu_deploy")
    )
    m.add(
        types.InlineKeyboardButton("💎 Subscription", callback_data="menu_sub"),
        types.InlineKeyboardButton("💰 Wallet", callback_data="menu_wallet")
    )
    m.add(
        types.InlineKeyboardButton("🎁 Referral", callback_data="menu_ref"),
        types.InlineKeyboardButton("📊 Statistics", callback_data="menu_stats")
    )
    m.add(
        types.InlineKeyboardButton("🟢 Running Bots", callback_data="menu_running"),
        types.InlineKeyboardButton("⚡ Speed Test", callback_data="menu_speed")
    )
    m.add(
        types.InlineKeyboardButton("📚 Help", callback_data="menu_help"),
        types.InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings")
    )
    notif_count = _db.unread_count(uid) if _db else 0
    notif_label = f"🔔 Notifications ({notif_count})" if notif_count > 0 else "🔔 Notifications"
    m.add(
        types.InlineKeyboardButton(notif_label, callback_data="menu_notif"),
        types.InlineKeyboardButton("🎫 Support", callback_data="menu_support")
    )
    if state.is_admin(uid):
        m.add(types.InlineKeyboardButton("👑 Admin Panel", callback_data="menu_admin"))
    m.add(types.InlineKeyboardButton("📞 Contact Developer", url="https://t.me/shihab23"))
    return m


def help_menu_kb():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("📤 How to Deploy", callback_data="help_deploy"),
        types.InlineKeyboardButton("🤖 Managing Bots", callback_data="help_bots")
    )
    m.add(
        types.InlineKeyboardButton("💎 Plans & Pricing", callback_data="help_plans"),
        types.InlineKeyboardButton("💳 Payment Guide", callback_data="help_payment")
    )
    m.add(
        types.InlineKeyboardButton("🎁 Referral System", callback_data="help_referral"),
        types.InlineKeyboardButton("💰 Wallet Guide", callback_data="help_wallet")
    )
    m.add(
        types.InlineKeyboardButton("🔍 Auto Detection", callback_data="help_detect"),
        types.InlineKeyboardButton("📦 Supported Files", callback_data="help_files")
    )
    m.add(
        types.InlineKeyboardButton("❓ FAQ", callback_data="help_faq"),
        types.InlineKeyboardButton("🛠 Troubleshoot", callback_data="help_trouble")
    )
    m.add(
        types.InlineKeyboardButton("📋 All Commands", callback_data="help_commands"),
        types.InlineKeyboardButton("📞 Contact Support", callback_data="help_contact")
    )
    m.add(types.InlineKeyboardButton("🏠 Back to Main Menu", callback_data="go_home"))
    return m


def back_btn(cb="go_home", text="🏠 Main Menu"):
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton(text, callback_data=cb))
    return m


def back_help_btn():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("📚 Back to Help", callback_data="menu_help"),
        types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home")
    )
    return m


def bot_action_kb(bid, is_live):
    m = types.InlineKeyboardMarkup(row_width=2)
    if is_live:
        m.add(
            types.InlineKeyboardButton("🛑 Stop", callback_data=f"bot_stop:{bid}"),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f"bot_restart:{bid}")
        )
        m.add(
            types.InlineKeyboardButton("📋 Logs", callback_data=f"bot_logs:{bid}"),
            types.InlineKeyboardButton("📊 Resources", callback_data=f"bot_res:{bid}")
        )
    else:
        m.add(
            types.InlineKeyboardButton("▶️ Start", callback_data=f"bot_start:{bid}"),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f"bot_del:{bid}")
        )
        m.add(
            types.InlineKeyboardButton("📋 Logs", callback_data=f"bot_logs:{bid}"),
            types.InlineKeyboardButton("📥 Download", callback_data=f"bot_dl:{bid}")
        )
        m.add(types.InlineKeyboardButton("🔍 Re-detect Entry", callback_data=f"bot_redetect:{bid}"))
    m.add(types.InlineKeyboardButton("🔙 Back to My Bots", callback_data="menu_mybots"))
    return m


def plan_kb():
    m = types.InlineKeyboardMarkup(row_width=1)
    for k, p in PLAN_LIMITS.items():
        if k == 'free':
            continue
        slots = '♾️' if p['max_bots'] == -1 else str(p['max_bots'])
        m.add(types.InlineKeyboardButton(
            f"{p['name']} — {slots} bots — {p['price']} BDT",
            callback_data=f"plan_select:{k}"
        ))
    m.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
    return m


def pay_method_kb(pk):
    m = types.InlineKeyboardMarkup(row_width=2)
    for k, v in PAYMENT_METHODS.items():
        m.add(types.InlineKeyboardButton(
            f"{v['icon']} {v['name']}",
            callback_data=f"pay_method:{pk}:{k}"
        ))
    m.add(types.InlineKeyboardButton("💰 Pay from Wallet", callback_data=f"pay_wallet:{pk}"))
    m.add(
        types.InlineKeyboardButton("🔙 Back to Plans", callback_data="menu_sub"),
        types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home")
    )
    return m


def admin_kb():
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("👥 All Users", callback_data="adm_users"),
        types.InlineKeyboardButton("📊 Statistics", callback_data="adm_stats")
    )
    m.add(
        types.InlineKeyboardButton("💳 Pending Payments", callback_data="adm_payments"),
        types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")
    )
    m.add(
        types.InlineKeyboardButton("➕ Add Subscription", callback_data="adm_addsub"),
        types.InlineKeyboardButton("➖ Remove Subscription", callback_data="adm_remsub")
    )
    m.add(
        types.InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban"),
        types.InlineKeyboardButton("✅ Unban User", callback_data="adm_unban")
    )
    m.add(
        types.InlineKeyboardButton("📢 Force Sub Channels", callback_data="adm_channels"),
        types.InlineKeyboardButton("🎟 Promo Codes", callback_data="adm_promo")
    )
    m.add(
        types.InlineKeyboardButton("🎫 Support Tickets", callback_data="adm_tickets"),
        types.InlineKeyboardButton("🖥 System Info", callback_data="adm_system")
    )
    m.add(
        types.InlineKeyboardButton("🛑 Stop All Bots", callback_data="adm_stopall"),
        types.InlineKeyboardButton("💾 Backup DB", callback_data="adm_backup")
    )
    m.add(
        types.InlineKeyboardButton("📜 Admin Logs", callback_data="adm_logs"),
        types.InlineKeyboardButton("💰 Give Balance", callback_data="adm_give")
    )
    m.add(
        types.InlineKeyboardButton("🔍 User Info", callback_data="adm_userinfo"),
        types.InlineKeyboardButton("🔔 Send Notification", callback_data="adm_notify")
    )
    m.add(types.InlineKeyboardButton("🗑️ Cleanup Storage", callback_data="adm_cleanup"))
    fsub_icon = "🟢" if state.force_sub_enabled else "🔴"
    lock_icon = "🔒" if state.bot_locked else "🔓"
    m.add(
        types.InlineKeyboardButton(f"{fsub_icon} Force Subscribe", callback_data="adm_fsub_toggle"),
        types.InlineKeyboardButton(f"{lock_icon} Bot Lock", callback_data="adm_lock_toggle")
    )
    m.add(types.InlineKeyboardButton("🏠 Main Menu", callback_data="go_home"))
    return m


def pay_approve_kb(pid):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.add(
        types.InlineKeyboardButton("✅ Approve", callback_data=f"pay_approve:{pid}"),
        types.InlineKeyboardButton("❌ Reject", callback_data=f"pay_reject:{pid}")
    )
    return m


def channels_manage_kb():
    channels = _db.get_all_channels() if _db else []
    m = types.InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        icon = "🟢" if ch['is_active'] else "🔴"
        # Use channel_id for SQLite, channel_username for MongoDB (no channel_id field)
        toggle_key = str(ch.get('channel_id') or ch.get('channel_username', ''))
        m.add(types.InlineKeyboardButton(
            f"{icon} @{ch['channel_username']} — {ch['channel_name']}",
            callback_data=f"ch_toggle:{toggle_key}"
        ))
    m.add(types.InlineKeyboardButton("➕ Add Channel", callback_data="ch_add"))
    m.add(types.InlineKeyboardButton("🗑 Remove Channel", callback_data="ch_remove"))
    m.add(types.InlineKeyboardButton("🔙 Back to Admin", callback_data="menu_admin"))
    return m
