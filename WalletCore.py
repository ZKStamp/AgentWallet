"""Layer 2 - Wallet: accounts, fee, starter funds, safety limits.

Shared by both paths. Talks to the chain only through ChainNode, and to
message formats only through MessageCodecs.
"""
import datetime, hashlib, json, os, secrets, sqlite3
from decimal import Decimal

import ChainNode as chain
import MessageCodecs as codecs

HOME = os.environ.get("AW_HOME", "/root/agentwallet")
DB = os.path.join(HOME, "AgentWallet.db")
CONFIG_FILE = os.path.join(HOME, "AgentWalletConfig.json")

# ---- Rules (decided by Eric, 27-28 Oct 2026) ----
FEE = Decimal("1")                  # service fee per send (payment or message), to Eric
DUST = Decimal("0.0001")            # amount carried by a message
MAX_PER_SEND = Decimal("500")       # max amount of one payment
MAX_PER_DAY = Decimal("2000")       # max BTCZ out per wallet per UTC day (amounts + fees)
MAX_SENDS_PER_DAY = 100             # max payments + messages per wallet per UTC day
STARTER_AMOUNT = Decimal("1000")    # free starter funds
STARTER_TOTAL = 100                 # first 100 wallets only
STARTER_PER_DAY = 10                # max starter grants per UTC day
STARTER_LOCK_DAYS = 7               # starter coins: messages/fees only for 7 days
POOL_MAX_PER_DAY = STARTER_AMOUNT * STARTER_PER_DAY
SIGNUPS_PER_IP_MINUTE = 1
SIGNUPS_PER_IP_DAY = 5
WAIT_HINT = " (after a send, your change needs 1 block, about 2.5 minutes, before it can be spent again)"


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(t):
    return t.isoformat(timespec="seconds")


def today():
    return iso(now())[:10]


def config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def db():
    con = sqlite3.connect(DB, timeout=30)
    con.execute("""CREATE TABLE IF NOT EXISTS accounts(
        id INTEGER PRIMARY KEY,
        key_hash TEXT UNIQUE NOT NULL,
        address TEXT UNIQUE NOT NULL,
        created_at TEXT NOT NULL,
        ip_hash TEXT,
        starter_status TEXT NOT NULL DEFAULT 'none',
        starter_txid TEXT,
        starter_at TEXT,
        frozen INTEGER NOT NULL DEFAULT 0)""")
    con.execute("""CREATE TABLE IF NOT EXISTS sends(
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        kind TEXT NOT NULL,
        to_address TEXT NOT NULL,
        amount TEXT NOT NULL,
        fee TEXT NOT NULL,
        status TEXT NOT NULL,
        txid TEXT,
        error TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)")
    return con


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def rules():
    return {
        "service_fee_btcz": str(FEE),
        "service_fee_note": "Every payment and every message costs 1 BTCZ, paid to the operator (Z-TEXT LLC). It buys the operator a coffee: probably the cheapest private coffee on chain.",
        "privacy": "All sends are shielded (zk-SNARK) transactions: on the BitcoinZ blockchain, sender, receiver, amount and message are hidden. The operator's server, which hosts the wallets, does see your wallet's activity (it records sends to enforce the limits). For privacy from the operator too, run your own copy (open source).",
        "network_fee_btcz": str(chain.NET_FEE),
        "message_amount_btcz": str(DUST),
        "max_per_payment_btcz": str(MAX_PER_SEND),
        "max_out_per_day_btcz": str(MAX_PER_DAY),
        "max_sends_per_day": MAX_SENDS_PER_DAY,
        "starter": "The first %d wallets get %s free BTCZ (max %d per day). For %d days, starter coins can only pay for messages and fees, not be sent out."
                   % (STARTER_TOTAL, STARTER_AMOUNT, STARTER_PER_DAY, STARTER_LOCK_DAYS),
        "timing": "A send takes about 10 to 60 seconds. After a send, wait about 1 block (2.5 minutes) before the next one.",
        "custody": "Wallets are hosted on the operator's BitcoinZ node. Keep balances small.",
    }


# ---------------- global switches ----------------

def is_frozen_all(con):
    row = con.execute("SELECT v FROM settings WHERE k='frozen_all'").fetchone()
    return bool(row and row[0] == "1")


def set_frozen_all(flag):
    con = db()
    with con:
        con.execute("INSERT OR REPLACE INTO settings(k, v) VALUES ('frozen_all', ?)", ("1" if flag else "0",))
    con.close()


def set_frozen_account(account_id, flag):
    con = db()
    with con:
        n = con.execute("UPDATE accounts SET frozen=? WHERE id=?", (1 if flag else 0, account_id)).rowcount
    con.close()
    return n == 1


# ---------------- accounts ----------------

def ip_hash(ip):
    salt = config().get("ip_salt", "")
    return hashlib.sha256((salt + "|" + (ip or "")).encode()).hexdigest()


def create_wallet(ip):
    con = db()
    if is_frozen_all(con):
        con.close()
        return {"ok": False, "error": "service paused, try later"}
    ih = ip_hash(ip)
    t = now()
    minute_ago = iso(t - datetime.timedelta(minutes=1))
    day_ago = iso(t - datetime.timedelta(days=1))
    recent = con.execute("SELECT COUNT(*) FROM accounts WHERE ip_hash=? AND created_at>=?", (ih, minute_ago)).fetchone()[0]
    daily = con.execute("SELECT COUNT(*) FROM accounts WHERE ip_hash=? AND created_at>=?", (ih, day_ago)).fetchone()[0]
    if recent >= SIGNUPS_PER_IP_MINUTE or daily >= SIGNUPS_PER_IP_DAY:
        con.close()
        return {"ok": False, "error": "too many new wallets from your network, try later"}
    key = "aw_" + secrets.token_urlsafe(32)
    address = chain.new_z_address()
    with con:
        given = con.execute("SELECT COUNT(*) FROM accounts WHERE starter_status IN ('pending','sent')").fetchone()[0]
        starter = "pending" if given < STARTER_TOTAL else "none"
        con.execute("INSERT INTO accounts(key_hash, address, created_at, ip_hash, starter_status) VALUES (?,?,?,?,?)",
                    (hash_key(key), address, iso(t), ih, starter))
    queue = None
    if starter == "pending":
        queue = con.execute("SELECT COUNT(*) FROM accounts WHERE starter_status='pending'").fetchone()[0]
    con.close()
    out = {"ok": True, "key": key, "address": address,
           "note": "Save your key now: it is shown only once. It controls this wallet.",
           "rules": rules()}
    if starter == "pending":
        out["starter"] = ("You are in the starter queue (position %d). %s BTCZ will arrive automatically, usually within minutes; "
                          "max %d starter wallets per day." % (queue, STARTER_AMOUNT, STARTER_PER_DAY))
    else:
        out["starter"] = "No free starter funds left. Receive BTCZ at your address to start."
    return out


def account_for_key(con, key):
    if not key:
        return None
    return con.execute("SELECT id, address, starter_status, starter_at, frozen FROM accounts WHERE key_hash=?",
                       (hash_key(key),)).fetchone()


def _locked(starter_status, starter_at):
    if starter_status != "sent" or not starter_at:
        return Decimal("0")
    unlock = datetime.datetime.fromisoformat(starter_at) + datetime.timedelta(days=STARTER_LOCK_DAYS)
    return STARTER_AMOUNT if now() < unlock else Decimal("0")


def _usage_today(con, account_id):
    rows = con.execute("SELECT amount, fee FROM sends WHERE account_id=? AND status IN ('pending','ok') AND substr(created_at,1,10)=?",
                       (account_id, today())).fetchall()
    total = sum((Decimal(a) + Decimal(f) for a, f in rows), Decimal("0"))
    return len(rows), total


def wallet_info(key):
    con = db()
    acc = account_for_key(con, key)
    if not acc:
        con.close()
        return {"ok": False, "error": "invalid key"}
    acc_id, address, s_status, s_at, frozen = acc
    confirmed = chain.balance(address)
    pending = chain.balance(address, 0) - confirmed
    locked = min(_locked(s_status, s_at), confirmed)
    sends, spent = _usage_today(con, acc_id)
    con.close()
    return {"ok": True, "address": address,
            "balance_btcz": str(confirmed), "incoming_unconfirmed_btcz": str(pending),
            "starter_locked_btcz": str(locked),
            "sendable_out_btcz": str(max(confirmed - locked, Decimal("0"))),
            "starter_status": s_status,
            "today": {"sends": sends, "sends_left": max(MAX_SENDS_PER_DAY - sends, 0),
                      "out_btcz": str(spent), "out_left_btcz": str(max(MAX_PER_DAY - spent, Decimal("0")))},
            "frozen": bool(frozen), "rules": rules()}


# ---------------- sending ----------------

def _reserve(con, acc, kind, to_address, amount):
    """Check every rule and reserve today's quota atomically. Returns (send_id, None) or (None, error)."""
    acc_id, address, s_status, s_at, frozen = acc
    if is_frozen_all(con):
        return None, "service paused, try later"
    if frozen:
        return None, "this wallet is frozen"
    sends, spent = _usage_today(con, acc_id)
    if sends >= MAX_SENDS_PER_DAY:
        return None, "daily limit reached: max %d sends per day" % MAX_SENDS_PER_DAY
    if spent + amount + FEE > MAX_PER_DAY:
        return None, "daily limit reached: max %s BTCZ out per day" % MAX_PER_DAY
    confirmed = chain.balance(address)
    cost = amount + FEE + chain.NET_FEE
    if kind == "payment":
        locked = min(_locked(s_status, s_at), confirmed)
        if cost > confirmed - locked:
            return None, ("not enough sendable BTCZ: need %s, sendable %s (starter coins are locked for messages for %d days)"
                          % (cost, confirmed - locked, STARTER_LOCK_DAYS)) if locked else \
                         "not enough confirmed BTCZ: need %s, have %s" % (cost, confirmed) + WAIT_HINT
    elif cost > confirmed:
        return None, "not enough confirmed BTCZ: need %s, have %s" % (cost, confirmed) + WAIT_HINT
    cur = con.execute("INSERT INTO sends(account_id, created_at, kind, to_address, amount, fee, status) VALUES (?,?,?,?,?,?, 'pending')",
                      (acc_id, iso(now()), kind, to_address, str(amount), str(FEE)))
    return cur.lastrowid, None


def _execute(con, send_id, from_addr, outputs):
    try:
        txid = chain.send(from_addr, outputs)
    except chain.ChainError as e:
        with con:
            con.execute("UPDATE sends SET status='failed', error=? WHERE id=?", (str(e)[:300], send_id))
        return {"ok": False, "error": "send failed, nothing was spent: " + str(e)[:200]}
    with con:
        con.execute("UPDATE sends SET status='ok', txid=? WHERE id=?", (txid, send_id))
    return {"ok": True, "txid": txid, "explorer": "https://explorer.btcz.rocks/tx/" + txid}


def send_btcz(key, to, amount, note=None):
    try:
        amount = Decimal(str(amount)).quantize(Decimal("0.00000001"))
    except Exception:
        return {"ok": False, "error": "invalid amount"}
    if amount <= 0:
        return {"ok": False, "error": "amount must be positive"}
    if amount > MAX_PER_SEND:
        return {"ok": False, "error": "max %s BTCZ per payment" % MAX_PER_SEND}
    kind_addr = chain.is_valid_address(to)
    if not kind_addr:
        return {"ok": False, "error": "invalid BitcoinZ address"}
    con = db()
    acc = account_for_key(con, key)
    if not acc:
        con.close()
        return {"ok": False, "error": "invalid key"}
    if to == acc[1]:
        con.close()
        return {"ok": False, "error": "cannot send to yourself"}
    memo = None
    if note:
        if kind_addr != "z":
            con.close()
            return {"ok": False, "error": "a note can only be attached when sending to a shielded (zs) address"}
        try:
            memo = codecs.ACTIVE.encode(acc[1], str(note))
        except ValueError as e:
            con.close()
            return {"ok": False, "error": str(e)}
    con.isolation_level = None
    con.execute("BEGIN IMMEDIATE")
    send_id, err = _reserve(con, acc, "payment", to, amount)
    if err:
        con.execute("ROLLBACK")
        con.close()
        return {"ok": False, "error": err}
    con.execute("COMMIT")
    con.isolation_level = ""
    out = [{"address": to, "amount": amount}, {"address": config()["fee_address"], "amount": FEE}]
    if memo:
        out[0]["memo"] = memo
    r = _execute(con, send_id, acc[1], out)
    con.close()
    if r["ok"]:
        r.update({"sent_btcz": str(amount), "service_fee_btcz": str(FEE), "network_fee_btcz": str(chain.NET_FEE)})
    return r


def send_message(key, to, text):
    if chain.is_valid_address(to) != "z":
        return {"ok": False, "error": "messages need a shielded BitcoinZ address (starts with zs)"}
    con = db()
    acc = account_for_key(con, key)
    if not acc:
        con.close()
        return {"ok": False, "error": "invalid key"}
    if to == acc[1]:
        con.close()
        return {"ok": False, "error": "cannot message yourself"}
    try:
        memo = codecs.ACTIVE.encode(acc[1], str(text))
    except ValueError as e:
        con.close()
        return {"ok": False, "error": str(e)}
    con.isolation_level = None
    con.execute("BEGIN IMMEDIATE")
    send_id, err = _reserve(con, acc, "message", to, DUST)
    if err:
        con.execute("ROLLBACK")
        con.close()
        return {"ok": False, "error": err}
    con.execute("COMMIT")
    con.isolation_level = ""
    out = [{"address": to, "amount": DUST, "memo": memo},
           {"address": config()["fee_address"], "amount": FEE}]
    r = _execute(con, send_id, acc[1], out)
    con.close()
    if r["ok"]:
        r.update({"format": codecs.ACTIVE.name, "service_fee_btcz": str(FEE), "network_fee_btcz": str(chain.NET_FEE),
                  "message_amount_btcz": str(DUST)})
    return r


# ---------------- reading ----------------

def inbox(key, limit=20):
    con = db()
    acc = account_for_key(con, key)
    if not acc:
        con.close()
        return {"ok": False, "error": "invalid key"}
    own = {r[0] for r in con.execute("SELECT txid FROM sends WHERE account_id=? AND txid IS NOT NULL", (acc[0],))}
    con.close()
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 20
    items = []
    conf_cache = {}
    for n in chain.received_notes(acc[1], 0):
        if n.get("change") or n.get("txid") in own:
            continue                        # our own change, not an incoming item
        d = codecs.decode_any(n.get("memo", ""))
        item = {"txid": n.get("txid"), "amount_btcz": str(n.get("amount")),
                "confirmations": None, "format": d.get("format")}
        conf = n.get("confirmations")
        if not isinstance(conf, int):
            tx = n.get("txid")
            if tx not in conf_cache:
                conf_cache[tx] = chain.confirmations(tx)
            conf = conf_cache[tx]
        item["confirmations"] = conf
        if "text" in d:
            item["text"] = d["text"]
        if "from_claimed" in d:
            item["from_claimed"] = d["from_claimed"]
            item["from_verified"] = False
        items.append(item)
    items.sort(key=lambda i: (i["confirmations"] if isinstance(i["confirmations"], int) else 0))
    for i in items:
        i["status"] = "confirmed" if isinstance(i["confirmations"], int) and i["confirmations"] > 0 else (
            "unconfirmed" if i["confirmations"] == 0 else "unknown")
    return {"ok": True, "address": acc[1], "count": len(items), "items": items[:limit],
            "note": "Shielded payments do not reveal the sender. 'from_claimed' is written by the sender and is not verified."}


# ---------------- starter grants (run by GrantStarters.py) ----------------

def grant_starters(max_now=STARTER_PER_DAY):
    """Send starter funds to queued wallets, within the daily caps. Returns a list of log lines."""
    log = []
    con = db()
    if is_frozen_all(con):
        con.close()
        return ["frozen: no grants"]
    done_today = con.execute("SELECT COUNT(*) FROM accounts WHERE starter_status='sent' AND substr(starter_at,1,10)=?",
                             (today(),)).fetchone()[0]
    room = min(STARTER_PER_DAY - done_today, max_now)
    if room <= 0:
        con.close()
        return ["daily starter cap reached"]
    if (done_today + room) * STARTER_AMOUNT > POOL_MAX_PER_DAY:
        room = int(POOL_MAX_PER_DAY / STARTER_AMOUNT) - done_today
    queue = con.execute("SELECT id, address FROM accounts WHERE starter_status='pending' AND frozen=0 ORDER BY id LIMIT ?",
                        (max(room, 0),)).fetchall()
    if not queue:
        con.close()
        return ["queue empty"]
    pool = config()["pool_address"]
    # Pay the whole batch in ONE transaction: the pool's change is unconfirmed
    # after a send, so a second send in the same block would fail.
    affordable = int((chain.balance(pool) - chain.NET_FEE) / STARTER_AMOUNT)
    queue = queue[:max(affordable, 0)]
    if not queue:
        con.close()
        return ["pool too low (or its last change is not confirmed yet)"]
    memo = codecs.ACTIVE.encode(pool, "Welcome to Agent Wallet. %s BTCZ starter funds: messages and fees only for %d days. Rules: call balance."
                                % (STARTER_AMOUNT, STARTER_LOCK_DAYS))
    try:
        txid = chain.send(pool, [{"address": a, "amount": STARTER_AMOUNT, "memo": memo} for _, a in queue])
    except chain.ChainError as e:
        con.close()
        return ["grant batch failed: %s" % e]
    with con:
        for acc_id, _ in queue:
            con.execute("UPDATE accounts SET starter_status='sent', starter_txid=?, starter_at=? WHERE id=?",
                        (txid, iso(now()), acc_id))
    log.append("starter sent to accounts %s in %s" % ([i for i, _ in queue], txid))
    con.close()
    return log
