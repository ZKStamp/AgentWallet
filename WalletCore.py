"""Layer 2 - Wallet: accounts, fee, starter funds, safety limits.

Shared by both paths. Talks to the chain only through ChainNode, and to
message formats only through MessageCodecs.
"""
import datetime, hashlib, json, os, secrets, sqlite3
from decimal import Decimal

import AgentSign
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
GIFT_DEAL_MAX = Decimal("100")      # starter (gift) coins usable for deal payments, per wallet, in total
GLOBAL_SENDS_PER_MINUTE = 30        # safety cap across ALL wallets (29 Oct)
POOL_MAX_PER_DAY = STARTER_AMOUNT * STARTER_PER_DAY
SIGNUPS_PER_IP_MINUTE = 1
SIGNUPS_PER_IP_DAY = 5
FUNDING_TIP = ("Coming from an exchange, or from a wallet that only sends to t-addresses (like Coinomi)? Send to your t_deposit_address: "
               "the coins move into your shielded wallet by themselves. For a fully private deposit, send your BTCZ to the free Z-TEXT wallet "
               "first (https://z-text.com/downloads): it shields them automatically. Then pay from Z-TEXT to your zs address.")
T_PRIVACY_NOTE = ("Funding from an exchange or Coinomi? Use the t_deposit_address. Your deposit will be visible on the blockchain, but everything "
                  "after it (payments, messages, deals) stays fully shielded. For a fully private deposit, use your zs address. "
                  "Moving a deposit into your shielded wallet costs the 0.0001 BTCZ network fee.")
SWEEP_MIN = Decimal("0.001")       # t-deposits below this are not moved
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
    con.execute("CREATE TABLE IF NOT EXISTS daily_stats(day TEXT, kind TEXT, status TEXT, n INTEGER, PRIMARY KEY(day, kind, status))")
    for sql in ("ALTER TABLE sends ADD COLUMN txid_hash TEXT",
                "ALTER TABLE sends ADD COLUMN gift_used TEXT",
                "ALTER TABLE accounts ADD COLUMN gift_btcz TEXT",
                "ALTER TABLE accounts ADD COLUMN gift_deal_used TEXT NOT NULL DEFAULT '0'",
                "ALTER TABLE accounts ADD COLUMN t_address TEXT"):
        try:
            con.execute(sql)
        except sqlite3.OperationalError:
            pass
    con.execute("""CREATE TABLE IF NOT EXISTS deals(
        id INTEGER PRIMARY KEY,
        account_id INTEGER NOT NULL,
        deal_id TEXT NOT NULL,
        role TEXT NOT NULL,
        counterparty TEXT NOT NULL,
        price TEXT NOT NULL,
        steps INTEGER NOT NULL,
        description TEXT,
        done_steps INTEGER NOT NULL DEFAULT 0,
        counter_signer TEXT,
        log TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(account_id, deal_id, role))""")
    return con


def h(x):
    return hashlib.sha256((x or "").encode()).hexdigest()


def forget(con):
    """Bee Memory: keep only what the limits need.
    - send details (receiver, txid) are wiped right after each send (see _execute)
    - yesterday's send rows become anonymous daily counters, then are deleted
    - the network fingerprint used for sign-up limits is wiped after 24 hours
    - a deal's record is deleted 7 days after its last step"""
    d = today()
    with con:
        con.execute("DELETE FROM deals WHERE updated_at<?", (iso(now() - datetime.timedelta(days=7)),))
        for day, kind, status, n in con.execute(
                "SELECT substr(created_at,1,10), kind, status, COUNT(*) FROM sends WHERE substr(created_at,1,10)<? GROUP BY 1,2,3", (d,)).fetchall():
            con.execute("INSERT INTO daily_stats(day, kind, status, n) VALUES (?,?,?,?) "
                        "ON CONFLICT(day, kind, status) DO UPDATE SET n=n+excluded.n", (day, kind, status, n))
        con.execute("DELETE FROM sends WHERE substr(created_at,1,10)<?", (d,))
        for sid, tx in con.execute("SELECT id, txid FROM sends WHERE txid IS NOT NULL").fetchall():
            con.execute("UPDATE sends SET txid=NULL, txid_hash=? WHERE id=?", (h(tx), sid))
        con.execute("UPDATE sends SET to_address='' WHERE to_address!='' AND status!='pending'")
        con.execute("UPDATE accounts SET ip_hash=NULL WHERE ip_hash IS NOT NULL AND created_at<?",
                    (iso(now() - datetime.timedelta(days=1)),))
        for aid, tx in con.execute("SELECT id, starter_txid FROM accounts WHERE starter_txid IS NOT NULL AND length(starter_txid)=64 AND starter_txid NOT LIKE 'h:%'").fetchall():
            con.execute("UPDATE accounts SET starter_txid=? WHERE id=?", ("h:" + h(tx)[:62], aid))


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def rules():
    return {
        "service_fee_btcz": str(FEE),
        "service_fee_note": "Every payment and every message costs 1 BTCZ, paid to the operator (Z-TEXT LLC). It buys the operator a coffee: probably the cheapest private coffee in the galaxy, tens of thousands of times cheaper than a €2 coffee in a European café.",
        "privacy": "All sends are shielded (zk-SNARK) transactions: on the BitcoinZ blockchain, sender, receiver, amount and message are hidden. The operator's server hosts the wallets, so it can see your wallet's activity. Bee Memory: the service forgets each send's receiver and transaction id right after sending, keeps only today's counters for the limits (deleted the next day), and forgets your network fingerprint after 24 hours. Open deals are kept until 7 days after their last step, then deleted. The node's own wallet still records transactions, as any hosted wallet does. For zero trust, run your own copy (open source).",
        "signatures": "Every message and deal step is signed with your wallet's Ed25519 signing key. Your public identity (signer id, starts with awid_) stays the same across deals; receivers can check that a message really comes from you. Hosted wallets: the server derives the signing key from your wallet key and does not store it.",
        "key_recovery": "There is no recovery: if you lose your wallet key, the wallet and its coins are lost. create_wallet makes a NEW wallet on every call.",
        "network_fee_btcz": str(chain.NET_FEE),
        "message_amount_btcz": str(DUST),
        "max_per_payment_btcz": str(MAX_PER_SEND),
        "max_out_per_day_btcz": str(MAX_PER_DAY),
        "max_sends_per_day": MAX_SENDS_PER_DAY,
        "starter": ("The first %d wallets get %s free BTCZ (max %d per day). Starter coins are a gift for trying the service: they pay messages, fees, "
                    "and up to %s BTCZ of deal payments to other I Am Wallet wallets. They can never be withdrawn; coins received from a starter-paid "
                    "deal stay starter coins for the receiver." % (STARTER_TOTAL, STARTER_AMOUNT, STARTER_PER_DAY, GIFT_DEAL_MAX)),
        "deals": "Deals: trade with another agent in small steps (offer, pay a step, deliver a step with a SHA-256 fingerprint, check, repeat). No escrow: pay one small step at a time. Max %d steps per deal; an offer expires after about 24 hours (576 blocks). For a proof anyone can check, also stamp the fingerprint with ZKStamp Proof-100 (https://proof.zketernity.app/llms.txt)." % 10,
        "global_cap": "Safety cap: max %d sends per minute across the whole service; above that, try again in a minute." % GLOBAL_SENDS_PER_MINUTE,
        "timing": "A send takes about 10 to 60 seconds. After a send, wait about 1 block (2.5 minutes) before the next one.",
        "custody": "Wallets are hosted on the operator's BitcoinZ node. Keep balances small.",
        "t_deposit": T_PRIVACY_NOTE,
        "funding_tip": FUNDING_TIP,
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
    forget(con)
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
    out["t_deposit_address"] = t_address_for(address)
    out["t_deposit_note"] = T_PRIVACY_NOTE
    out["funding_tip"] = FUNDING_TIP
    return out


def account_for_key(con, key):
    if not key:
        return None
    return con.execute("SELECT id, address, starter_status, starter_at, frozen FROM accounts WHERE key_hash=?",
                       (hash_key(key),)).fetchone()


def _gift(con, acc_id, starter_status, total):
    """Starter (gift) coins of a wallet, capped by what it holds (incl. unconfirmed).
    Wallets from before v0.2 get their label set here on first use."""
    row = con.execute("SELECT gift_btcz, gift_deal_used FROM accounts WHERE id=?", (acc_id,)).fetchone()
    g, used = row
    if g is None:
        g = min(STARTER_AMOUNT, total) if starter_status == "sent" else Decimal("0")
        con.execute("UPDATE accounts SET gift_btcz=? WHERE id=?", (str(g), acc_id))
    return min(Decimal(g), total), Decimal(used or "0")


def signer_for_key(key):
    return AgentSign.signer_id(AgentSign.public_key(AgentSign.seed_for_key(key)))


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
    total = chain.balance(address, 0)
    with con:
        gift, deal_used = _gift(con, acc_id, s_status, total)
    sends, spent = _usage_today(con, acc_id)
    con.close()
    t_addr = t_address_for(address)
    t_wait = chain.balance(t_addr, 0) if t_addr else Decimal("0")
    return {"ok": True, "address": address, "signer": signer_for_key(key),
            "t_deposit_address": t_addr, "t_deposit_waiting_btcz": str(t_wait),
            "balance_btcz": str(confirmed), "incoming_unconfirmed_btcz": str(total - confirmed),
            "starter_gift_btcz": str(gift),
            "starter_gift_for_deals_left_btcz": str(max(min(GIFT_DEAL_MAX - deal_used, gift), Decimal("0"))),
            "sendable_out_btcz": str(max(confirmed - gift, Decimal("0"))),
            "starter_status": s_status,
            "today": {"sends": sends, "sends_left": max(MAX_SENDS_PER_DAY - sends, 0),
                      "out_btcz": str(spent), "out_left_btcz": str(max(MAX_PER_DAY - spent, Decimal("0")))},
            "frozen": bool(frozen), "rules": rules()}


def t_address_for(z_address):
    """The wallet's t-deposit address; wallets from before 29 Oct get one on first use."""
    con = db()
    row = con.execute("SELECT t_address FROM accounts WHERE address=?", (z_address,)).fetchone()
    if not row:
        con.close()
        return None
    if row[0]:
        con.close()
        return row[0]
    t = chain.new_t_address()
    with con:
        con.execute("UPDATE accounts SET t_address=? WHERE address=? AND t_address IS NULL", (t, z_address))
    t = con.execute("SELECT t_address FROM accounts WHERE address=?", (z_address,)).fetchone()[0]
    con.close()
    return t


def sweep_t_deposits():
    """Move confirmed t-deposits into each wallet's own shielded address.
    One transaction per t-address (never combined, so they stay unlinked).
    The 0.0001 network fee comes out of the deposit. Run by GrantStarters.py."""
    con = db()
    if is_frozen_all(con):
        con.close()
        return ["frozen: no t-deposit moves"]
    rows = con.execute("SELECT id, address, t_address FROM accounts WHERE t_address IS NOT NULL AND frozen=0").fetchall()
    con.close()
    log = []
    for acc_id, z, t in rows:
        try:
            bal = chain.balance(t)
            if bal < SWEEP_MIN:
                continue
            txid = chain.send(t, [{"address": z, "amount": bal - chain.NET_FEE}])
            log.append("t-deposit of wallet %d moved to its shielded address in %s" % (acc_id, txid))
        except chain.ChainError as e:
            log.append("t-deposit move of wallet %d failed, will retry: %s" % (acc_id, str(e)[:150]))
    return log


# ---------------- sending ----------------

def _reserve(con, acc, kind, to_address, amount, deal=False):
    """Check every rule and reserve today's quota atomically.
    Returns (send_id, plan, None) or (None, None, error).
    plan: gift coins used, and gift coins that move to a hosted receiver (deals)."""
    acc_id, address, s_status, s_at, frozen = acc
    if is_frozen_all(con):
        return None, None, "service paused, try later"
    if frozen:
        return None, None, "this wallet is frozen"
    minute_ago = iso(now() - datetime.timedelta(minutes=1))
    if con.execute("SELECT COUNT(*) FROM sends WHERE created_at>=? AND status IN ('pending','ok')",
                   (minute_ago,)).fetchone()[0] >= GLOBAL_SENDS_PER_MINUTE:
        return None, None, "service busy (max %d sends per minute for everyone), try again in a minute" % GLOBAL_SENDS_PER_MINUTE
    sends, spent = _usage_today(con, acc_id)
    if sends >= MAX_SENDS_PER_DAY:
        return None, None, "daily limit reached: max %d sends per day" % MAX_SENDS_PER_DAY
    if spent + amount + FEE > MAX_PER_DAY:
        return None, None, "daily limit reached: max %s BTCZ out per day" % MAX_PER_DAY
    confirmed = chain.balance(address)
    total = chain.balance(address, 0)
    gift, deal_used = _gift(con, acc_id, s_status, total)
    real = max(confirmed - gift, Decimal("0"))
    fees = FEE + chain.NET_FEE
    gift_amt = Decimal("0")
    receiver = None
    if amount + fees > confirmed:
        return None, None, "not enough confirmed BTCZ: need %s, have %s" % (amount + fees, confirmed) + WAIT_HINT
    if kind == "message":
        gift_used = min(gift, amount + fees)
    else:
        gift_fee = min(gift, fees)
        if deal:
            row = con.execute("SELECT id FROM accounts WHERE address=?", (to_address,)).fetchone()
            if row:
                receiver = row[0]
                gift_amt = max(min(gift - gift_fee, amount, GIFT_DEAL_MAX - deal_used), Decimal("0"))
        need_real = amount - gift_amt + fees - gift_fee
        if need_real > real:
            if gift > 0:
                return None, None, ("not enough withdrawable BTCZ: this payment needs %s of your own coins, you have %s. Starter coins never leave "
                                    "I Am Wallet; they pay messages, fees and up to %s BTCZ of deal payments to other I Am Wallet wallets."
                                    % (need_real, real, GIFT_DEAL_MAX))
            return None, None, "not enough confirmed BTCZ: need %s, have %s" % (amount + fees, confirmed) + WAIT_HINT
        gift_used = gift_fee + gift_amt
    con.execute("UPDATE accounts SET gift_btcz=?, gift_deal_used=? WHERE id=?",
                (str(gift - gift_used), str(deal_used + gift_amt), acc_id))
    cur = con.execute("INSERT INTO sends(account_id, created_at, kind, to_address, amount, fee, status, gift_used) VALUES (?,?,?,?,?,?, 'pending', ?)",
                      (acc_id, iso(now()), kind, to_address, str(amount), str(FEE), str(gift_used)))
    return cur.lastrowid, {"gift_used": gift_used, "gift_amt": gift_amt, "receiver": receiver}, None


def _execute(con, send_id, acc_id, from_addr, outputs, plan):
    try:
        txid = chain.send(from_addr, outputs)
    except chain.ChainError as e:
        with con:
            con.execute("UPDATE sends SET status='failed', to_address='', error='send failed' WHERE id=?", (send_id,))
            # give the starter label back: nothing was spent
            g, u = con.execute("SELECT gift_btcz, gift_deal_used FROM accounts WHERE id=?", (acc_id,)).fetchone()
            con.execute("UPDATE accounts SET gift_btcz=?, gift_deal_used=? WHERE id=?",
                        (str(Decimal(g or "0") + plan["gift_used"]), str(Decimal(u or "0") - plan["gift_amt"]), acc_id))
        return {"ok": False, "error": "send failed, nothing was spent: " + str(e)[:200]}
    with con:
        # Bee Memory: forget the receiver and the transaction id right away
        con.execute("UPDATE sends SET status='ok', to_address='', txid=NULL, txid_hash=? WHERE id=?", (h(txid), send_id))
        if plan["receiver"] and plan["gift_amt"] > 0:
            # starter coins stay starter coins for the receiver
            row = con.execute("SELECT gift_btcz FROM accounts WHERE id=?", (plan["receiver"],)).fetchone()
            con.execute("UPDATE accounts SET gift_btcz=? WHERE id=?",
                        (str(Decimal(row[0] or "0") + plan["gift_amt"]), plan["receiver"]))
    return {"ok": True, "txid": txid, "explorer": "https://explorer.btcz.rocks/tx/" + txid}


def _signed_send(key, kind, to, amount, text=None, deal=False):
    """One shielded send: a payment (optional signed note) or a signed message.
    kind: 'payment' or 'message'. deal=True lets starter coins pay (deals only)."""
    kind_addr = chain.is_valid_address(to)
    if not kind_addr:
        return {"ok": False, "error": "invalid BitcoinZ address"}
    if kind == "message" and kind_addr != "z":
        return {"ok": False, "error": "messages need a shielded BitcoinZ address (starts with zs)"}
    con = db()
    acc = account_for_key(con, key)
    if not acc:
        con.close()
        return {"ok": False, "error": "invalid key"}
    if to == acc[1]:
        con.close()
        return {"ok": False, "error": "cannot send to yourself"}
    memo = None
    if text is not None:
        if kind_addr != "z":
            con.close()
            return {"ok": False, "error": "a note can only be attached when sending to a shielded (zs) address"}
        try:
            memo = codecs.SIGNED.encode_signed(AgentSign.seed_for_key(key), acc[1], to, str(text))
        except ValueError as e:
            con.close()
            return {"ok": False, "error": str(e)}
    forget(con)
    con.isolation_level = None
    con.execute("BEGIN IMMEDIATE")
    try:
        send_id, plan, err = _reserve(con, acc, kind, to, amount, deal)
    except Exception:
        con.execute("ROLLBACK")
        con.close()
        raise
    if err:
        con.execute("ROLLBACK")
        con.close()
        return {"ok": False, "error": err}
    con.execute("COMMIT")
    con.isolation_level = ""
    out = [{"address": to, "amount": amount}, {"address": config()["fee_address"], "amount": FEE}]
    if memo:
        out[0]["memo"] = memo
    r = _execute(con, send_id, acc[0], acc[1], out, plan)
    con.close()
    if r["ok"]:
        r.update({"service_fee_btcz": str(FEE), "network_fee_btcz": str(chain.NET_FEE),
                  "starter_gift_used_btcz": str(plan["gift_used"])})
        if memo:
            r.update({"format": codecs.SIGNED.name, "signer": signer_for_key(key)})
    return r


def send_btcz(key, to, amount, note=None, deal=False):
    try:
        amount = Decimal(str(amount)).quantize(Decimal("0.00000001"))
    except Exception:
        return {"ok": False, "error": "invalid amount"}
    if amount <= 0:
        return {"ok": False, "error": "amount must be positive"}
    if amount > MAX_PER_SEND:
        return {"ok": False, "error": "max %s BTCZ per payment" % MAX_PER_SEND}
    r = _signed_send(key, "payment", to, amount, note if note else None, deal)
    if r["ok"]:
        r["sent_btcz"] = str(amount)
    return r


def send_message(key, to, text):
    r = _signed_send(key, "message", to, DUST, str(text))
    if r["ok"]:
        r["message_amount_btcz"] = str(DUST)
    return r


# ---------------- reading ----------------

def inbox_items(acc, con):
    """All incoming items of a wallet, decoded, newest first (no limit)."""
    own = {r[0] for r in con.execute("SELECT txid_hash FROM sends WHERE account_id=? AND txid_hash IS NOT NULL", (acc[0],))}
    items = []
    conf_cache = {}
    for n in chain.received_notes(acc[1], 0):
        if n.get("change") or h(n.get("txid")) in own:
            continue                        # our own change, not an incoming item
        d = codecs.decode_any(n.get("memo", ""), acc[1])
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
        if "signer" in d:
            item["signer"] = d["signer"]
            item["signature_valid"] = bool(d.get("signature_valid"))
        elif "from_claimed" in d:
            item["signature_valid"] = False
        items.append(item)
    items.sort(key=lambda i: (i["confirmations"] if isinstance(i["confirmations"], int) else 0))
    for i in items:
        i["status"] = "confirmed" if isinstance(i["confirmations"], int) and i["confirmations"] > 0 else (
            "unconfirmed" if i["confirmations"] == 0 else "unknown")
    return items


def inbox(key, limit=20):
    con = db()
    acc = account_for_key(con, key)
    if not acc:
        con.close()
        return {"ok": False, "error": "invalid key"}
    forget(con)
    try:
        limit = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit = 20
    items = inbox_items(acc, con)
    con.close()
    return {"ok": True, "address": acc[1], "count": len(items), "items": items[:limit],
            "note": "Shielded payments do not reveal the sender. 'signer' is the sender's public identity: if 'signature_valid' is true, "
                    "the message (including 'from_claimed', the reply address) was really written by that signer. Unsigned items cannot be verified."}


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
    memo = codecs.ACTIVE.encode(pool, "Welcome to I Am Wallet. %s BTCZ starter coins: a gift for messages, fees and small deals (up to %s BTCZ) "
                                      "inside I Am Wallet. They cannot be withdrawn. Rules: call balance." % (STARTER_AMOUNT, GIFT_DEAL_MAX))
    try:
        txid = chain.send(pool, [{"address": a, "amount": STARTER_AMOUNT, "memo": memo} for _, a in queue])
    except chain.ChainError as e:
        con.close()
        return ["grant batch failed: %s" % e]
    with con:
        for acc_id, _ in queue:
            con.execute("UPDATE accounts SET starter_status='sent', starter_txid=?, starter_at=?, "
                        "gift_btcz=CAST(COALESCE(gift_btcz,'0') AS REAL)+? WHERE id=?",
                        ("h:" + h(txid)[:62], iso(now()), float(STARTER_AMOUNT), acc_id))
    log.append("starter sent to accounts %s in %s" % ([i for i, _ in queue], txid))
    con.close()
    return log
