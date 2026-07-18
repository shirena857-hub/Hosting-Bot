"""
╔═══════════════════════════════════════════════════════════╗
║  DATABASE MODULE v6.0                                     ║
║  Primary MongoDB → Secondary MongoDB → SQLite             ║
║  Storage monitor + compact user data                      ║
╚═══════════════════════════════════════════════════════════╝
"""

import sqlite3, logging, threading, os, shutil
from datetime import datetime, timedelta
from config import (
    OWNER_ID, PLAN_LIMITS, DATA_DIR, MONGO_URL, MONGO_URL_BACKUP,
    DB_NAME, DB_STORAGE_WARN_MB, DB_STORAGE_LIMIT_MB
)

logger = logging.getLogger('APON.DB')

# ── DB mode tracking ──────────────────────────────
USE_MONGO        = False
MONGO_IS_BACKUP  = False   # True when running on secondary MongoDB
mongo_client     = None
mongo_db         = None

# ── Try PRIMARY MongoDB ───────────────────────────
def _try_mongo(url, label='PRIMARY'):
    global mongo_client, mongo_db, USE_MONGO
    if not url:
        return False
    try:
        from pymongo import MongoClient
        c = MongoClient(url, serverSelectionTimeoutMS=5000,
                        maxPoolSize=300, minPoolSize=5, maxIdleTimeMS=30000)
        c.admin.command('ping')
        mongo_client = c
        mongo_db     = c[DB_NAME]
        USE_MONGO    = True
        logger.info(f"✅ MongoDB {label} connected!")
        return True
    except Exception as e:
        logger.warning(f"⚠️ MongoDB {label} failed: {e}")
        return False

if not _try_mongo(MONGO_URL, 'PRIMARY'):
    if _try_mongo(MONGO_URL_BACKUP, 'BACKUP'):
        MONGO_IS_BACKUP = True
    else:
        logger.warning("⚠️ Both MongoDB URLs failed. Falling back to SQLite.")

DB_PATH = os.path.join(DATA_DIR, 'apon.db')


# ═══════════════════════════════════════════════════
#  CACHE HELPERS
# ═══════════════════════════════════════════════════
def _cache():
    try:
        from core.cache import cache
        return cache
    except Exception:
        return None

def _inv_user(uid):
    try:
        from core.cache import user_cache_del, plan_cache_del, stats_cache_invalidate
        user_cache_del(uid); plan_cache_del(uid); stats_cache_invalidate()
    except Exception:
        pass

def _inv_channels():
    try:
        from core.cache import channels_cache_invalidate
        channels_cache_invalidate()
    except Exception:
        pass


# ═══════════════════════════════════════════════════
#  STORAGE MONITOR
# ═══════════════════════════════════════════════════
class StorageMonitor:
    """
    Periodically checks DB storage. Alerts admin when near limit.
    If limit is hit, tries to switch to secondary MongoDB.
    Bot never stops — if no failover available, just warns admin.
    """
    _last_warn_level = 0   # 0=ok, 1=warn, 2=limit

    @staticmethod
    def get_mongo_size_mb():
        """Return MongoDB storage size in MB, or None if unavailable."""
        try:
            if not USE_MONGO or mongo_db is None:
                return None
            stats = mongo_db.command('dbStats')
            return round(stats.get('storageSize', 0) / (1024 * 1024), 1)
        except Exception:
            return None

    @staticmethod
    def get_sqlite_size_mb():
        if os.path.exists(DB_PATH):
            return round(os.path.getsize(DB_PATH) / (1024 * 1024), 2)
        return 0.0

    @classmethod
    def check(cls, send_fn, admin_ids):
        """Call from a background thread. send_fn(uid, text)."""
        try:
            if USE_MONGO:
                size_mb = cls.get_mongo_size_mb()
                db_type = f"MongoDB {'(Backup)' if MONGO_IS_BACKUP else '(Primary)'}"
            else:
                size_mb = cls.get_sqlite_size_mb()
                db_type = "SQLite"

            if size_mb is None:
                return

            msg = None

            if size_mb >= DB_STORAGE_LIMIT_MB and cls._last_warn_level < 2:
                cls._last_warn_level = 2
                # Try to failover to secondary MongoDB
                failover_ok = False
                if not MONGO_IS_BACKUP and MONGO_URL_BACKUP:
                    failover_ok = _try_mongo(MONGO_URL_BACKUP, 'BACKUP-FAILOVER')

                if failover_ok:
                    msg = (
                        f"🔴 <b>DATABASE STORAGE CRITICAL!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"📊 {db_type}: <b>{size_mb} MB</b> (limit: {DB_STORAGE_LIMIT_MB} MB)\n\n"
                        f"✅ <b>Auto-switched to Secondary MongoDB!</b>\n"
                        f"⚠️ Please free up space on Primary DB.\n"
                        f"🤖 Bot is running normally.\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                else:
                    msg = (
                        f"🔴 <b>DATABASE STORAGE CRITICAL!</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"📊 {db_type}: <b>{size_mb} MB</b> (limit: {DB_STORAGE_LIMIT_MB} MB)\n\n"
                        f"⚠️ No backup DB available.\n"
                        f"🔸 Bot is still running — new data may not be saved.\n"
                        f"🛠 Action required: free up space or add MONGO_URL_BACKUP.\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )

            elif size_mb >= DB_STORAGE_WARN_MB and cls._last_warn_level < 1:
                cls._last_warn_level = 1
                msg = (
                    f"🟡 <b>DATABASE STORAGE WARNING</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 {db_type}: <b>{size_mb} MB</b>\n"
                    f"⚠️ Getting close to limit ({DB_STORAGE_LIMIT_MB} MB).\n"
                    f"🤖 Bot is running normally.\n"
                    f"💡 Consider cleaning old logs or backups.\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
            elif size_mb < DB_STORAGE_WARN_MB * 0.9:
                cls._last_warn_level = 0   # reset after recovering

            if msg and send_fn:
                for aid in admin_ids:
                    try:
                        send_fn(aid, msg)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"StorageMonitor.check error: {e}")


storage_monitor = StorageMonitor()


# ═══════════════════════════════════════════════════
#  SQLITE BASE
# ═══════════════════════════════════════════════════
class SQLiteDB:
    _local = threading.local()

    def _conn(self):
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA cache_size=20000")
            self._local.conn.execute("PRAGMA temp_store=MEMORY")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn.execute("PRAGMA mmap_size=268435456")
        return self._local.conn

    def exe(self, q, p=(), fetch=False, one=False):
        try:
            conn = self._conn()
            cur  = conn.execute(q, p)
            conn.commit()
            if one:
                row = cur.fetchone()
                return dict(row) if row else None
            if fetch:
                rows = cur.fetchall()
                return [dict(r) for r in rows] if rows else []
            return cur.lastrowid
        except Exception as e:
            logger.error(f"SQLite error: {e} | Query: {q[:80]}")
            return None

    def init_tables(self):
        # ── Users (compact — only essential fields) ──
        self.exe("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            plan TEXT DEFAULT 'free',
            wallet_balance REAL DEFAULT 0,
            total_spent REAL DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            ban_reason TEXT DEFAULT '',
            is_lifetime INTEGER DEFAULT 0,
            subscription_end TEXT,
            referral_code TEXT DEFAULT '',
            referred_by INTEGER,
            referral_count INTEGER DEFAULT 0,
            referral_earnings REAL DEFAULT 0,
            referral_level TEXT DEFAULT 'bronze',
            last_active TEXT DEFAULT(datetime('now')),
            created_at TEXT DEFAULT(datetime('now'))
        )""")
        # ── Bots (only metadata — no file content) ──
        self.exe("""CREATE TABLE IF NOT EXISTS bots(
            bot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            bot_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            entry_file TEXT DEFAULT 'main.py',
            file_type TEXT DEFAULT 'py',
            bot_token TEXT DEFAULT '',
            status TEXT DEFAULT 'stopped',
            total_restarts INTEGER DEFAULT 0,
            file_size INTEGER DEFAULT 0,
            detection_confidence TEXT DEFAULT '',
            last_started TEXT,
            last_stopped TEXT,
            last_crash TEXT,
            created_at TEXT DEFAULT(datetime('now'))
        )""")
        self.exe("""CREATE TABLE IF NOT EXISTS payments(
            payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            transaction_id TEXT NOT NULL,
            plan TEXT NOT NULL,
            duration_days INTEGER DEFAULT 30,
            status TEXT DEFAULT 'pending',
            approved_by INTEGER,
            created_at TEXT DEFAULT(datetime('now')),
            processed_at TEXT
        )""")
        self.exe("""CREATE TABLE IF NOT EXISTS referrals(
            ref_id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL,
            bonus_days INTEGER DEFAULT 0,
            commission REAL DEFAULT 0,
            created_at TEXT DEFAULT(datetime('now'))
        )""")
        self.exe("""CREATE TABLE IF NOT EXISTS wallet_tx(
            tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            tx_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT(datetime('now'))
        )""")
        self.exe("""CREATE TABLE IF NOT EXISTS force_channels(
            channel_id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_username TEXT UNIQUE NOT NULL,
            channel_name TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            added_by INTEGER,
            created_at TEXT DEFAULT(datetime('now'))
        )""")
        self.exe("""CREATE TABLE IF NOT EXISTS tickets(
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            subject TEXT DEFAULT '',
            message TEXT DEFAULT '',
            admin_reply TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT(datetime('now'))
        )""")
        self.exe("""CREATE TABLE IF NOT EXISTS notifications(
            notif_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT DEFAULT '',
            message TEXT DEFAULT '',
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT(datetime('now'))
        )""")
        self.exe("""CREATE TABLE IF NOT EXISTS promo_codes(
            promo_id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            discount_pct INTEGER DEFAULT 0,
            max_uses INTEGER DEFAULT 1,
            used_count INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_by INTEGER,
            created_at TEXT DEFAULT(datetime('now'))
        )""")
        self.exe("""CREATE TABLE IF NOT EXISTS error_logs(
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            error_type TEXT DEFAULT '',
            error_message TEXT DEFAULT '',
            user_id INTEGER,
            traceback_info TEXT DEFAULT '',
            created_at TEXT DEFAULT(datetime('now'))
        )""")
        self.exe("""CREATE TABLE IF NOT EXISTS admin_logs(
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            target_user INTEGER,
            details TEXT DEFAULT '',
            created_at TEXT DEFAULT(datetime('now'))
        )""")
        # ── Indexes for fast lookups ──
        self.exe("CREATE INDEX IF NOT EXISTS idx_bots_user ON bots(user_id)")
        self.exe("CREATE INDEX IF NOT EXISTS idx_bots_status ON bots(status)")
        self.exe("CREATE INDEX IF NOT EXISTS idx_pay_status ON payments(status)")
        self.exe("CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id)")


# ═══════════════════════════════════════════════════
#  MONGODB
# ═══════════════════════════════════════════════════
class MongoDB:
    def __init__(self):
        self.db = mongo_db
        self._ensure_indexes()

    def _ensure_indexes(self):
        try:
            from pymongo import DESCENDING, ASCENDING
            self.db.users.create_index('user_id', unique=True)
            self.db.users.create_index('referral_code')
            # Drop ALL legacy unique indexes on bots that cause DuplicateKeyError
            for idx_name in ['bot_id_1', 'bot_id', 'user_id_1_bot_name_1']:
                try:
                    self.db.bots.drop_index(idx_name)
                    logger.info(f"Dropped legacy bots index: {idx_name}")
                except Exception:
                    pass
            # Recreate safe (non-unique) indexes
            self.db.bots.create_index([('user_id', ASCENDING), ('bot_name', ASCENDING)])
            self.db.bots.create_index('status')
            self.db.payments.create_index('status')
            self.db.notifications.create_index([('user_id', ASCENDING), ('is_read', ASCENDING)])
        except Exception as e:
            logger.warning(f"_ensure_indexes warning: {e}")

    def get_user(self, uid):
        c = _cache()
        if c:
            cached = c.get(f"user:{uid}")
            if cached is not None:
                return cached
        u = self.db.users.find_one({'user_id': uid}, {'_id': 0})
        if u and c:
            c.set(f"user:{uid}", u, ex=300)
        return u

    def get_user_by_ref_code(self, rc):
        return self.db.users.find_one({'referral_code': rc}, {'_id': 0})

    def create_user(self, uid, un='', fn='', rc='', rb=None):
        try:
            # Use upsert to avoid DuplicateKeyError on race conditions or re-starts
            self.db.users.update_one(
                {'user_id': uid},
                {'$setOnInsert': {
                    'user_id': uid, 'username': un, 'full_name': fn,
                    'plan': 'free', 'wallet_balance': 0, 'total_spent': 0,
                    'is_banned': 0, 'ban_reason': '', 'is_lifetime': 0,
                    'subscription_end': None, 'referral_code': rc,
                    'referred_by': rb, 'referral_count': 0,
                    'referral_earnings': 0, 'referral_level': 'bronze',
                    'last_active': datetime.now().isoformat(),
                    'created_at': datetime.now().isoformat()
                }},
                upsert=True
            )
        except Exception:
            pass
        _inv_user(uid)

    def update_user(self, uid, **kw):
        if not kw:
            return
        self.db.users.update_one({'user_id': uid}, {'$set': kw})
        _inv_user(uid)

    def get_all_users(self):
        # Return only compact profile fields — no heavy data
        return list(self.db.users.find(
            {},
            {'_id': 0, 'user_id': 1, 'username': 1, 'full_name': 1,
             'plan': 1, 'is_banned': 1, 'wallet_balance': 1,
             'subscription_end': 1, 'is_lifetime': 1, 'referral_count': 1,
             'referral_code': 1, 'created_at': 1}
        ))

    def ban(self, uid, r=''):
        self.update_user(uid, is_banned=1, ban_reason=r)

    def unban(self, uid):
        self.update_user(uid, is_banned=0, ban_reason='')

    def set_sub(self, uid, plan, days=30):
        if plan == 'lifetime':
            self.update_user(uid, plan=plan, is_lifetime=1, subscription_end=None)
        else:
            end = (datetime.now() + timedelta(days=days)).isoformat()
            self.update_user(uid, plan=plan, is_lifetime=0, subscription_end=end)

    def rem_sub(self, uid):
        self.update_user(uid, plan='free', is_lifetime=0, subscription_end=None)

    def is_active(self, uid):
        u = self.get_user(uid)
        if not u:
            return False
        if u.get('is_lifetime') or u.get('plan') == 'free':
            return True
        se = u.get('subscription_end')
        if se:
            try:
                return datetime.fromisoformat(se) > datetime.now()
            except Exception:
                return False
        return False

    def get_plan(self, uid):
        from core.state import state as st
        c = _cache()
        if c:
            cached = c.get(f"plan:{uid}")
            if cached is not None:
                return cached
        u = self.get_user(uid)
        if not u:
            return PLAN_LIMITS['free']
        pl = PLAN_LIMITS['lifetime'] if st.is_admin(uid) else PLAN_LIMITS.get(u.get('plan'), PLAN_LIMITS['free'])
        if c:
            c.set(f"plan:{uid}", pl, ex=300)
        return pl

    def add_bot(self, uid, name, path, entry='main.py', ft='py', tok='', sz=0, conf=''):
        # Only store metadata — file content stays on disk
        # Use insert_one safely; _ensure_indexes already drops any legacy unique index on bots
        try:
            r = self.db.bots.insert_one({
                'user_id': uid, 'bot_name': name, 'file_path': path,
                'entry_file': entry, 'file_type': ft, 'bot_token': tok,
                'status': 'stopped', 'total_restarts': 0, 'file_size': sz,
                'detection_confidence': conf, 'last_started': None,
                'last_stopped': None, 'last_crash': None,
                'created_at': datetime.now().isoformat()
            })
            return str(r.inserted_id)
        except Exception as e:
            logger.error(f"add_bot error: {e}")
            # Fallback: return existing bot id if somehow still duplicate
            try:
                existing = self.db.bots.find_one({'user_id': uid, 'bot_name': name}, {'_id': 1})
                if existing:
                    return str(existing['_id'])
            except Exception:
                pass
            return None

    def get_bots(self, uid):
        from pymongo import DESCENDING
        bots = list(self.db.bots.find({'user_id': uid}, {'_id': 0}).sort('created_at', DESCENDING))
        for i, b in enumerate(bots):
            if 'bot_id' not in b:
                b['bot_id'] = i + 1
        return bots

    def get_bot(self, bid):
        try:
            from bson import ObjectId
            b = self.db.bots.find_one({'_id': ObjectId(str(bid))}, {'_id': 0})
            if b and 'bot_id' not in b:
                b['bot_id'] = bid
            return b
        except Exception:
            return self.db.bots.find_one({'bot_id': bid}, {'_id': 0})

    def update_bot(self, bid, **kw):
        if not kw:
            return
        try:
            from bson import ObjectId
            self.db.bots.update_one({'_id': ObjectId(str(bid))}, {'$set': kw})
        except Exception:
            self.db.bots.update_one({'bot_id': bid}, {'$set': kw})

    def del_bot(self, bid):
        try:
            from bson import ObjectId
            self.db.bots.delete_one({'_id': ObjectId(str(bid))})
        except Exception:
            self.db.bots.delete_one({'bot_id': bid})

    def bot_count(self, uid):
        return self.db.bots.count_documents({'user_id': uid})

    def add_pay(self, uid, amt, method, trx, plan, days=30):
        r = self.db.payments.insert_one({
            'user_id': uid, 'amount': amt, 'method': method,
            'transaction_id': trx, 'plan': plan, 'duration_days': days,
            'status': 'pending', 'approved_by': None,
            'created_at': datetime.now().isoformat(), 'processed_at': None
        })
        return str(r.inserted_id)

    def pending_pay(self):
        from pymongo import DESCENDING
        pays = list(self.db.payments.find({'status': 'pending'}, {'_id': 0}).sort('created_at', DESCENDING))
        for i, p in enumerate(pays):
            if 'payment_id' not in p:
                p['payment_id'] = i + 1
        return pays

    def get_pay(self, pid):
        try:
            from bson import ObjectId
            p = self.db.payments.find_one({'_id': ObjectId(str(pid))}, {'_id': 0})
            if p and 'payment_id' not in p:
                p['payment_id'] = pid
            return p
        except Exception:
            return self.db.payments.find_one({'payment_id': pid}, {'_id': 0})

    def approve_pay(self, pid, aid):
        p = self.get_pay(pid)
        if not p:
            return None
        try:
            from bson import ObjectId
            self.db.payments.update_one(
                {'_id': ObjectId(str(pid))},
                {'$set': {'status': 'approved', 'approved_by': aid, 'processed_at': datetime.now().isoformat()}}
            )
        except Exception:
            self.db.payments.update_one({'payment_id': pid}, {'$set': {'status': 'approved', 'approved_by': aid}})
        self.set_sub(p['user_id'], p['plan'], p.get('duration_days', 30))
        u = self.get_user(p['user_id'])
        if u:
            self.update_user(p['user_id'], total_spent=u.get('total_spent', 0) + p['amount'])
        return p

    def reject_pay(self, pid, aid):
        try:
            from bson import ObjectId
            self.db.payments.update_one(
                {'_id': ObjectId(str(pid))},
                {'$set': {'status': 'rejected', 'approved_by': aid, 'processed_at': datetime.now().isoformat()}}
            )
        except Exception:
            self.db.payments.update_one({'payment_id': pid}, {'$set': {'status': 'rejected'}})

    def add_ref(self, rr, rd, days=3, comm=20):
        self.db.referrals.insert_one({
            'referrer_id': rr, 'referred_id': rd, 'bonus_days': days,
            'commission': comm, 'created_at': datetime.now().isoformat()
        })
        u = self.get_user(rr)
        if u:
            nc = u.get('referral_count', 0) + 1
            lv = 'diamond' if nc >= 100 else 'platinum' if nc >= 50 else 'gold' if nc >= 25 else 'silver' if nc >= 10 else 'bronze'
            # Note: wallet_balance is NOT updated here — wallet_tx() below handles it via $inc
            # Updating wallet_balance here AND in wallet_tx() would cause double credit
            self.update_user(rr, referral_count=nc,
                             referral_earnings=u.get('referral_earnings', 0) + comm,
                             referral_level=lv)
            self.wallet_tx(rr, comm, 'referral', f"Referral bonus: User {rd}")

    def ref_board(self, lim=10):
        from pymongo import DESCENDING
        return list(self.db.users.find({}, {'_id': 0}).sort('referral_count', DESCENDING).limit(lim))

    def user_refs(self, uid):
        from pymongo import DESCENDING
        return list(self.db.referrals.find({'referrer_id': uid}, {'_id': 0}).sort('created_at', DESCENDING))

    def wallet_tx(self, uid, amt, tt, desc=''):
        self.db.wallet_tx.insert_one({
            'user_id': uid, 'amount': amt, 'tx_type': tt,
            'description': desc, 'created_at': datetime.now().isoformat()
        })
        if tt in ('credit', 'referral', 'refund', 'bonus'):
            self.db.users.update_one({'user_id': uid}, {'$inc': {'wallet_balance': amt}})
        elif tt in ('debit', 'withdraw', 'purchase'):
            self.db.users.update_one({'user_id': uid}, {'$inc': {'wallet_balance': -amt}})
        _inv_user(uid)

    def wallet_hist(self, uid, lim=20):
        from pymongo import DESCENDING
        return list(self.db.wallet_tx.find({'user_id': uid}, {'_id': 0}).sort('created_at', DESCENDING).limit(lim))

    def add_channel(self, username, name='', added_by=None):
        username = username.strip().lstrip('@').lower()
        self.db.force_channels.update_one(
            {'channel_username': username},
            {'$set': {'channel_name': name or username, 'is_active': 1, 'added_by': added_by}},
            upsert=True
        )
        _inv_channels()

    def remove_channel(self, username):
        self.db.force_channels.update_one(
            {'channel_username': username.strip().lstrip('@').lower()},
            {'$set': {'is_active': 0}}
        )
        _inv_channels()

    def get_active_channels(self):
        c = _cache()
        if c:
            cached = c.get("channels:active")
            if cached is not None:
                return cached
        result = list(self.db.force_channels.find({'is_active': 1}, {'_id': 0}))
        if c:
            c.set("channels:active", result, ex=600)
        return result

    def get_all_channels(self):
        return list(self.db.force_channels.find({}, {'_id': 0}))

    def toggle_channel(self, cid):
        ch = self.db.force_channels.find_one({'channel_username': cid}, {'_id': 0}) or \
             self.db.force_channels.find_one({'_id': cid}, {'_id': 0})
        if ch:
            ns = 0 if ch.get('is_active') else 1
            self.db.force_channels.update_one(
                {'channel_username': ch['channel_username']}, {'$set': {'is_active': ns}}
            )
            _inv_channels()
            return ns
        return None

    def add_ticket(self, uid, subj, msg_text):
        r = self.db.tickets.insert_one({
            'user_id': uid, 'subject': subj, 'message': msg_text,
            'admin_reply': '', 'status': 'open',
            'created_at': datetime.now().isoformat()
        })
        return str(r.inserted_id)

    def open_tickets(self):
        from pymongo import DESCENDING
        tickets = list(self.db.tickets.find({'status': 'open'}, {'_id': 0}).sort('created_at', DESCENDING))
        for i, t in enumerate(tickets):
            if 'ticket_id' not in t:
                t['ticket_id'] = i + 1
        return tickets

    def get_ticket(self, tid):
        try:
            from bson import ObjectId
            t = self.db.tickets.find_one({'_id': ObjectId(str(tid))}, {'_id': 0})
            if t and 'ticket_id' not in t:
                t['ticket_id'] = tid
            return t
        except Exception:
            return self.db.tickets.find_one({'ticket_id': tid}, {'_id': 0})

    def reply_ticket(self, tid, reply):
        try:
            from bson import ObjectId
            self.db.tickets.update_one(
                {'_id': ObjectId(str(tid))},
                {'$set': {'admin_reply': reply, 'status': 'replied'}}
            )
        except Exception:
            self.db.tickets.update_one({'ticket_id': tid}, {'$set': {'admin_reply': reply, 'status': 'replied'}})

    def add_notif(self, uid, title, message):
        r = self.db.notifications.insert_one({
            'user_id': uid, 'title': title, 'message': message,
            'is_read': 0, 'created_at': datetime.now().isoformat()
        })
        return str(r.inserted_id)

    def get_notifs(self, uid, lim=10):
        from pymongo import DESCENDING
        return list(self.db.notifications.find({'user_id': uid}, {'_id': 0}).sort('created_at', DESCENDING).limit(lim))

    def unread_count(self, uid):
        return self.db.notifications.count_documents({'user_id': uid, 'is_read': 0})

    def mark_read(self, uid):
        self.db.notifications.update_many({'user_id': uid}, {'$set': {'is_read': 1}})

    def get_promo(self, code):
        return self.db.promo_codes.find_one({'code': code.upper(), 'is_active': 1}, {'_id': 0})

    def use_promo(self, code):
        self.db.promo_codes.update_one({'code': code.upper()}, {'$inc': {'used_count': 1}})

    def add_promo(self, code, discount, max_uses, created_by):
        try:
            self.db.promo_codes.insert_one({
                'code': code.upper(), 'discount_pct': discount,
                'max_uses': max_uses, 'used_count': 0,
                'created_by': created_by, 'is_active': 1,
                'created_at': datetime.now().isoformat()
            })
        except Exception:
            pass

    def all_promos(self):
        from pymongo import DESCENDING
        return list(self.db.promo_codes.find({}, {'_id': 0}).sort('created_at', DESCENDING))

    def log_error(self, error_type, error_msg, uid=None, tb=''):
        self.db.error_logs.insert_one({
            'error_type': error_type, 'error_message': str(error_msg)[:500],
            'user_id': uid, 'traceback_info': tb[:1000],
            'created_at': datetime.now().isoformat()
        })

    def admin_log(self, aid, act, tgt=None, det=''):
        self.db.admin_logs.insert_one({
            'admin_id': aid, 'action': act, 'target_user': tgt,
            'details': det, 'created_at': datetime.now().isoformat()
        })

    def get_admin_logs(self, limit=20):
        from pymongo import DESCENDING
        return list(self.db.admin_logs.find({}, {'_id': 0}).sort('created_at', DESCENDING).limit(limit))

    # ── Cleanup helpers (admin panel) ──
    def cleanup_user_files(self):
        """Delete bot files from disk, keep DB metadata."""
        import shutil
        from config import UPLOAD_DIR
        deleted = 0
        if os.path.isdir(UPLOAD_DIR):
            for entry in os.scandir(UPLOAD_DIR):
                try:
                    shutil.rmtree(entry.path) if entry.is_dir() else os.remove(entry.path)
                    deleted += 1
                except Exception:
                    pass
        return deleted

    def cleanup_old_logs(self):
        """Delete log files from disk."""
        from config import LOGS_DIR
        deleted = 0
        if os.path.isdir(LOGS_DIR):
            for f in os.listdir(LOGS_DIR):
                try:
                    fp = os.path.join(LOGS_DIR, f)
                    os.remove(fp)
                    deleted += 1
                except Exception:
                    pass
        return deleted

    def cleanup_old_backups(self, keep=5):
        """Keep only last N backups."""
        from config import BACKUP_DIR
        deleted = 0
        if os.path.isdir(BACKUP_DIR):
            bks = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('bk_')], reverse=True)
            for old in bks[keep:]:
                try:
                    os.remove(os.path.join(BACKUP_DIR, old))
                    deleted += 1
                except Exception:
                    pass
        return deleted

    def cleanup_error_logs_db(self):
        """Clear error_logs collection."""
        r = self.db.error_logs.delete_many({})
        return r.deleted_count

    def cleanup_old_notifications(self, days=30):
        """Delete read notifications older than N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        r = self.db.notifications.delete_many({'is_read': 1, 'created_at': {'$lt': cutoff}})
        return r.deleted_count

    def stats(self):
        c = _cache()
        if c:
            cached = c.get("admin:stats")
            if cached is not None:
                return cached
        now_str    = datetime.now().isoformat()
        today_start = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
        from pymongo import DESCENDING
        rev_agg = list(self.db.payments.aggregate([
            {'$match': {'status': 'approved'}},
            {'$group': {'_id': None, 's': {'$sum': '$amount'}}}
        ]))
        result = {
            'users':       self.db.users.count_documents({}),
            'bots':        self.db.bots.count_documents({}),
            'pending':     self.db.payments.count_documents({'status': 'pending'}),
            'revenue':     rev_agg[0].get('s', 0) if rev_agg else 0,
            'today':       self.db.users.count_documents({'created_at': {'$gte': today_start}}),
            'active_subs': self.db.users.count_documents({
                '$or': [
                    {'is_lifetime': 1},
                    {'subscription_end': {'$gt': now_str}, 'plan': {'$ne': 'free'}}
                ]
            }),
            'banned': self.db.users.count_documents({'is_banned': 1}),
        }
        if c:
            c.set("admin:stats", result, ex=120)
        return result

    def exe(self, q, p=(), fetch=False, one=False):
        logger.warning(f"exe() called on MongoDB: {q[:60]}")
        return None


# ═══════════════════════════════════════════════════
#  SQLITE WRAPPED
# ═══════════════════════════════════════════════════
class WrappedSQLite(SQLiteDB):

    def get_user(self, uid):
        c = _cache()
        if c:
            cached = c.get(f"user:{uid}")
            if cached is not None:
                return cached
        u = self.exe("SELECT * FROM users WHERE user_id=?", (uid,), one=True)
        if u and c:
            c.set(f"user:{uid}", u, ex=300)
        return u

    def get_user_by_ref_code(self, rc):
        return self.exe("SELECT * FROM users WHERE referral_code=?", (rc,), one=True)

    def create_user(self, uid, un='', fn='', rc='', rb=None):
        self.exe(
            "INSERT OR IGNORE INTO users(user_id,username,full_name,referral_code,referred_by) VALUES(?,?,?,?,?)",
            (uid, un, fn, rc, rb)
        )
        _inv_user(uid)

    def update_user(self, uid, **kw):
        if not kw:
            return
        cols = ','.join(f'{k}=?' for k in kw)
        vals = list(kw.values()) + [uid]
        self.exe(f"UPDATE users SET {cols} WHERE user_id=?", vals)
        _inv_user(uid)

    def get_all_users(self):
        # Compact projection — only profile essentials
        return self.exe(
            "SELECT user_id,username,full_name,plan,is_banned,wallet_balance,"
            "subscription_end,is_lifetime,referral_count,referral_code,created_at FROM users",
            fetch=True
        ) or []

    def ban(self, uid, r=''):
        self.update_user(uid, is_banned=1, ban_reason=r)

    def unban(self, uid):
        self.update_user(uid, is_banned=0, ban_reason='')

    def set_sub(self, uid, plan, days=30):
        if plan == 'lifetime':
            self.update_user(uid, plan=plan, is_lifetime=1, subscription_end=None)
        else:
            end = (datetime.now() + timedelta(days=days)).isoformat()
            self.update_user(uid, plan=plan, is_lifetime=0, subscription_end=end)

    def rem_sub(self, uid):
        self.update_user(uid, plan='free', is_lifetime=0, subscription_end=None)

    def is_active(self, uid):
        u = self.get_user(uid)
        if not u:
            return False
        if u['is_lifetime'] or u['plan'] == 'free':
            return True
        se = u.get('subscription_end')
        if se:
            try:
                return datetime.fromisoformat(se) > datetime.now()
            except Exception:
                return False
        return False

    def get_plan(self, uid):
        from core.state import state as st
        c = _cache()
        if c:
            cached = c.get(f"plan:{uid}")
            if cached is not None:
                return cached
        u = self.get_user(uid)
        if not u:
            return PLAN_LIMITS['free']
        pl = PLAN_LIMITS['lifetime'] if st.is_admin(uid) else PLAN_LIMITS.get(u['plan'], PLAN_LIMITS['free'])
        if c:
            c.set(f"plan:{uid}", pl, ex=300)
        return pl

    def add_bot(self, uid, name, path, entry='main.py', ft='py', tok='', sz=0, conf=''):
        return self.exe(
            "INSERT INTO bots(user_id,bot_name,file_path,entry_file,file_type,bot_token,file_size,detection_confidence) VALUES(?,?,?,?,?,?,?,?)",
            (uid, name, path, entry, ft, tok, sz, conf)
        )

    def get_bots(self, uid):
        return self.exe("SELECT * FROM bots WHERE user_id=?", (uid,), fetch=True) or []

    def get_bot(self, bid):
        return self.exe("SELECT * FROM bots WHERE bot_id=?", (bid,), one=True)

    def update_bot(self, bid, **kw):
        if not kw:
            return
        cols = ','.join(f'{k}=?' for k in kw)
        vals = list(kw.values()) + [bid]
        self.exe(f"UPDATE bots SET {cols} WHERE bot_id=?", vals)

    def del_bot(self, bid):
        self.exe("DELETE FROM bots WHERE bot_id=?", (bid,))

    def bot_count(self, uid):
        r = self.exe("SELECT COUNT(*) as c FROM bots WHERE user_id=?", (uid,), one=True)
        return r['c'] if r else 0

    def add_pay(self, uid, amt, method, trx, plan, days=30):
        pid = self.exe(
            "INSERT INTO payments(user_id,amount,method,transaction_id,plan,duration_days) VALUES(?,?,?,?,?,?)",
            (uid, amt, method, trx, plan, days)
        )
        try:
            from core.cache import stats_cache_invalidate
            stats_cache_invalidate()
        except Exception:
            pass
        return pid

    def pending_pay(self):
        return self.exe("SELECT * FROM payments WHERE status='pending' ORDER BY created_at DESC", fetch=True) or []

    def get_pay(self, pid):
        return self.exe("SELECT * FROM payments WHERE payment_id=?", (pid,), one=True)

    def user_payments(self, uid, limit=10):
        return self.exe(
            "SELECT * FROM payments WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (uid, limit), fetch=True
        ) or []

    def approve_pay(self, pid, aid):
        p = self.get_pay(pid)
        if not p:
            return None
        self.exe(
            "UPDATE payments SET status='approved',approved_by=?,processed_at=datetime('now') WHERE payment_id=?",
            (aid, pid)
        )
        self.set_sub(p['user_id'], p['plan'], p['duration_days'])
        u = self.get_user(p['user_id'])
        if u:
            self.update_user(p['user_id'], total_spent=u.get('total_spent', 0) + p['amount'])
        try:
            from core.cache import stats_cache_invalidate
            stats_cache_invalidate()
        except Exception:
            pass
        return p

    def reject_pay(self, pid, aid):
        self.exe(
            "UPDATE payments SET status='rejected',approved_by=?,processed_at=datetime('now') WHERE payment_id=?",
            (aid, pid)
        )

    def add_ref(self, rr, rd, days=3, comm=20):
        self.exe(
            "INSERT INTO referrals(referrer_id,referred_id,bonus_days,commission) VALUES(?,?,?,?)",
            (rr, rd, days, comm)
        )
        u = self.get_user(rr)
        if u:
            nc = u['referral_count'] + 1
            lv = 'diamond' if nc >= 100 else 'platinum' if nc >= 50 else 'gold' if nc >= 25 else 'silver' if nc >= 10 else 'bronze'
            self.update_user(rr, referral_count=nc,
                             referral_earnings=u['referral_earnings'] + comm,
                             wallet_balance=u['wallet_balance'] + comm,
                             referral_level=lv)
            self.wallet_tx(rr, comm, 'referral', f"Referral bonus: User {rd}")

    def ref_board(self, lim=10):
        return self.exe("SELECT * FROM users ORDER BY referral_count DESC LIMIT ?", (lim,), fetch=True) or []

    def user_refs(self, uid):
        return self.exe("SELECT * FROM referrals WHERE referrer_id=? ORDER BY created_at DESC", (uid,), fetch=True) or []

    def wallet_tx(self, uid, amt, tt, desc=''):
        self.exe("INSERT INTO wallet_tx(user_id,amount,tx_type,description) VALUES(?,?,?,?)", (uid, amt, tt, desc))
        if tt in ('credit', 'referral', 'refund', 'bonus'):
            self.exe("UPDATE users SET wallet_balance=wallet_balance+? WHERE user_id=?", (amt, uid))
        elif tt in ('debit', 'withdraw', 'purchase'):
            self.exe("UPDATE users SET wallet_balance=wallet_balance-? WHERE user_id=?", (amt, uid))
        _inv_user(uid)

    def wallet_hist(self, uid, lim=20):
        return self.exe("SELECT * FROM wallet_tx WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (uid, lim), fetch=True) or []

    def add_channel(self, username, name='', added_by=None):
        username = username.strip().lstrip('@').lower()
        ex = self.exe("SELECT * FROM force_channels WHERE channel_username=?", (username,), one=True)
        if ex:
            self.exe("UPDATE force_channels SET is_active=1,channel_name=? WHERE channel_username=?", (name or username, username))
        else:
            self.exe("INSERT INTO force_channels(channel_username,channel_name,added_by) VALUES(?,?,?)", (username, name or username, added_by))
        _inv_channels()

    def remove_channel(self, username):
        self.exe("UPDATE force_channels SET is_active=0 WHERE channel_username=?", (username.strip().lstrip('@').lower(),))
        _inv_channels()

    def get_active_channels(self):
        c = _cache()
        if c:
            cached = c.get("channels:active")
            if cached is not None:
                return cached
        result = self.exe("SELECT * FROM force_channels WHERE is_active=1", fetch=True) or []
        if c:
            c.set("channels:active", result, ex=600)
        return result

    def get_all_channels(self):
        return self.exe("SELECT * FROM force_channels ORDER BY is_active DESC", fetch=True) or []

    def toggle_channel(self, cid):
        ch = self.exe("SELECT * FROM force_channels WHERE channel_id=?", (cid,), one=True)
        if ch:
            ns = 0 if ch['is_active'] else 1
            self.exe("UPDATE force_channels SET is_active=? WHERE channel_id=?", (ns, cid))
            _inv_channels()
            return ns
        return None

    def add_ticket(self, uid, subj, msg_text):
        return self.exe("INSERT INTO tickets(user_id,subject,message) VALUES(?,?,?)", (uid, subj, msg_text))

    def open_tickets(self):
        return self.exe("SELECT * FROM tickets WHERE status='open' ORDER BY created_at DESC", fetch=True) or []

    def get_ticket(self, tid):
        return self.exe("SELECT * FROM tickets WHERE ticket_id=?", (tid,), one=True)

    def reply_ticket(self, tid, reply):
        self.exe("UPDATE tickets SET admin_reply=?,status='replied' WHERE ticket_id=?", (reply, tid))

    def add_notif(self, uid, title, message):
        return self.exe("INSERT INTO notifications(user_id,title,message) VALUES(?,?,?)", (uid, title, message))

    def get_notifs(self, uid, lim=10):
        return self.exe("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (uid, lim), fetch=True) or []

    def unread_count(self, uid):
        r = self.exe("SELECT COUNT(*) as c FROM notifications WHERE user_id=? AND is_read=0", (uid,), one=True)
        return r['c'] if r else 0

    def mark_read(self, uid):
        self.exe("UPDATE notifications SET is_read=1 WHERE user_id=?", (uid,))

    def get_promo(self, code):
        return self.exe("SELECT * FROM promo_codes WHERE code=? AND is_active=1", (code.upper(),), one=True)

    def use_promo(self, code):
        self.exe("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code.upper(),))

    def add_promo(self, code, discount, max_uses, created_by):
        return self.exe(
            "INSERT OR IGNORE INTO promo_codes(code,discount_pct,max_uses,created_by) VALUES(?,?,?,?)",
            (code.upper(), discount, max_uses, created_by)
        )

    def all_promos(self):
        return self.exe("SELECT * FROM promo_codes ORDER BY created_at DESC", fetch=True) or []

    def log_error(self, error_type, error_msg, uid=None, tb=''):
        self.exe(
            "INSERT INTO error_logs(error_type,error_message,user_id,traceback_info) VALUES(?,?,?,?)",
            (error_type, str(error_msg)[:500], uid, tb[:1000])
        )

    def admin_log(self, aid, act, tgt=None, det=''):
        self.exe("INSERT INTO admin_logs(admin_id,action,target_user,details) VALUES(?,?,?,?)", (aid, act, tgt, det))

    def get_admin_logs(self, limit=20):
        return self.exe("SELECT * FROM admin_logs ORDER BY created_at DESC LIMIT ?", (limit,), fetch=True) or []

    def cleanup_user_files(self):
        import shutil
        from config import UPLOAD_DIR
        deleted = 0
        if os.path.isdir(UPLOAD_DIR):
            for entry in os.scandir(UPLOAD_DIR):
                try:
                    shutil.rmtree(entry.path) if entry.is_dir() else os.remove(entry.path)
                    deleted += 1
                except Exception:
                    pass
        return deleted

    def cleanup_old_logs(self):
        from config import LOGS_DIR
        deleted = 0
        if os.path.isdir(LOGS_DIR):
            for f in os.listdir(LOGS_DIR):
                try:
                    os.remove(os.path.join(LOGS_DIR, f))
                    deleted += 1
                except Exception:
                    pass
        return deleted

    def cleanup_old_backups(self, keep=5):
        from config import BACKUP_DIR
        deleted = 0
        if os.path.isdir(BACKUP_DIR):
            bks = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('bk_')], reverse=True)
            for old in bks[keep:]:
                try:
                    os.remove(os.path.join(BACKUP_DIR, old))
                    deleted += 1
                except Exception:
                    pass
        return deleted

    def cleanup_error_logs_db(self):
        r = self.exe("DELETE FROM error_logs", ())
        return r or 0

    def cleanup_old_notifications(self, days=30):
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        r = self.exe(
            "DELETE FROM notifications WHERE is_read=1 AND created_at<?", (cutoff,)
        )
        return r or 0

    def stats(self):
        c = _cache()
        if c:
            cached = c.get("admin:stats")
            if cached is not None:
                return cached
        result = {
            'users':       (self.exe("SELECT COUNT(*) as c FROM users", one=True) or {}).get('c', 0),
            'bots':        (self.exe("SELECT COUNT(*) as c FROM bots", one=True) or {}).get('c', 0),
            'pending':     (self.exe("SELECT COUNT(*) as c FROM payments WHERE status='pending'", one=True) or {}).get('c', 0),
            'revenue':     (self.exe("SELECT COALESCE(SUM(amount),0) as s FROM payments WHERE status='approved'", one=True) or {}).get('s', 0),
            'today':       (self.exe("SELECT COUNT(*) as c FROM users WHERE date(created_at)=date('now')", one=True) or {}).get('c', 0),
            'active_subs': (self.exe("SELECT COUNT(*) as c FROM users WHERE plan!='free' AND(is_lifetime=1 OR subscription_end>datetime('now'))", one=True) or {}).get('c', 0),
            'banned':      (self.exe("SELECT COUNT(*) as c FROM users WHERE is_banned=1", one=True) or {}).get('c', 0),
        }
        if c:
            c.set("admin:stats", result, ex=120)
        return result


# ═══════════════════════════════════════════════════
#  INIT
# ═══════════════════════════════════════════════════
if USE_MONGO:
    db = MongoDB()
    mode = "MongoDB BACKUP" if MONGO_IS_BACKUP else "MongoDB PRIMARY"
    logger.info(f"🍃 Using {mode} (1000+ user optimized)")
else:
    _sq = WrappedSQLite()
    _sq.init_tables()
    db = _sq
    logger.info("🗄️ Using SQLite (local fallback)")
