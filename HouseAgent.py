"""House agent: our own seller, open 24/7. Sells Hello World stamps through Deals.

How a buyer agent buys (all with I Am Wallet tools):
  1. send_message(to=<house address>, text="STAMP India")   (or "STAMP 1", or "STAMP LIST")
  2. the house agent answers with a deal_offer: 50 BTCZ, 1 step
  3. deal_pay(deal_id)
  4. the house agent delivers the stamp (link + SHA-256 of the image) and
     stamps that fingerprint with Proof-100; the public gallery shows "sold"
     and the proof (never the owner)

Rules (Eric, 31 Oct 2026): one of a kind per country, 50 BTCZ, max 1 stamp per
buyer (signer) per UTC day, unpaid offers released after 24 hours.

Run by HouseAgent.timer every 2 minutes:  python3 /root/agentwallet/HouseAgent.py
First time:                               python3 /root/agentwallet/HouseAgent.py setup
Only one outgoing wallet send per run (a hosted wallet's change needs 1 block).
"""
import datetime, html, json, os, re, sys, urllib.request

import WalletCore as core
import Deals as deals

HOME = core.HOME
CONFIG_FILE = os.path.join(HOME, "HouseAgentConfig.json")
CATALOG_FILE = os.path.join(HOME, "stamps", "StampCatalog.json")
PRICE = "50"
OFFER_HOURS = 24
PROOF_API = "http://127.0.0.1:5100/v1/proof"
PROOF100_HOME = os.environ.get("P100_HOME", "/root/proof100")


def cfg():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def catalog():
    with open(CATALOG_FILE, encoding="utf-8") as f:
        return json.load(f)


def db():
    con = core.db()
    con.execute("""CREATE TABLE IF NOT EXISTS stamps(
        number INTEGER PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'available',
        deal_id TEXT,
        buyer_signer TEXT,
        reserved_at TEXT,
        sold_at TEXT,
        delivery_txid TEXT,
        proof_txid TEXT,
        proof_viewing_key TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS house_seen(txid TEXT PRIMARY KEY, at TEXT NOT NULL)")
    with con:
        for s in catalog()["stamps"]:
            con.execute("INSERT OR IGNORE INTO stamps(number) VALUES (?)", (s["number"],))
    return con


def find_stamp(cat, what):
    w = (what or "").strip().lower()
    for s in cat["stamps"]:
        if w.isdigit() and int(w) == s["number"]:
            return s
        if w == s["country"].lower() or w.replace(".", "") == s["country"].lower().replace(".", ""):
            return s
    aliases = {"usa": 3, "us": 3, "united states of america": 3, "america": 3, "drc": 15, "congo": 15,
               "dr congo": 15, "democratic republic of the congo": 15, "turkiye": 18, "türkiye": 18}
    n = aliases.get(w)
    return next((s for s in cat["stamps"] if s["number"] == n), None) if n else None


def available_list(con, cat, limit=12):
    free = {r[0] for r in con.execute("SELECT number FROM stamps WHERE status='available'")}
    names = ["%d %s" % (s["number"], s["country"]) for s in cat["stamps"] if s["number"] in free]
    return ", ".join(names[:limit]) + (" ..." if len(names) > limit else "") if names else "none (next wave soon)"


def bought_today(con, signer):
    day = core.today()
    return con.execute("SELECT COUNT(*) FROM stamps WHERE buyer_signer=? AND status IN ('reserved','sold') "
                       "AND substr(COALESCE(sold_at, reserved_at),1,10)=?", (signer, day)).fetchone()[0]


def reply(key, to, text, log):
    r = core.send_message(key, to, text[:300])
    log.append("reply to %s...: %s -> %s" % (to[:10], text[:60], "ok" if r["ok"] else r.get("error")))
    return r["ok"]


def stamp_proof(c, sha):
    req = urllib.request.Request(PROOF_API, data=json.dumps({"fingerprint": sha}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + c["proof_key"]})
    with urllib.request.urlopen(req, timeout=240) as resp:
        return json.loads(resp.read())


# ---------------- one run ----------------

def run():
    c = cfg()
    key, me = c["key"], c["address"]
    cat = catalog()
    con = db()
    log = []
    sent = False      # max one outgoing wallet send per run

    # 1. Deliver paid stamps (paid buyers come first)
    for num, did in con.execute("SELECT number, deal_id FROM stamps WHERE status='reserved' AND deal_id IS NOT NULL").fetchall():
        st = deals.deal_status(key, did)
        info = next((x for x in st.get("deals", []) if x["you_are"] == "seller"), None) if st.get("ok") else None
        if not info or not info["next"].startswith("deliver"):
            continue
        if sent:
            break
        s = next(x for x in cat["stamps"] if x["number"] == num)
        r = deals.deal_deliver(key, did, link=c["stamp_base_url"] + "img/" + s["file"], fingerprint=s["sha256"])
        if not r["ok"]:
            log.append("deliver No.%03d: %s" % (num, r.get("error")))
            break
        sent = True
        with con:
            con.execute("UPDATE stamps SET status='sold', sold_at=?, delivery_txid=? WHERE number=?",
                        (core.iso(core.now()), r["txid"], num))
        log.append("SOLD No.%03d %s, delivery %s" % (num, s["country"], r["txid"]))
        try:
            p = stamp_proof(c, s["sha256"])
            if p.get("ok"):
                with con:
                    con.execute("UPDATE stamps SET proof_txid=?, proof_viewing_key=? WHERE number=?",
                                (p["txid"], p.get("viewing_key"), num))
                log.append("proof No.%03d: %s" % (num, p["txid"]))
            else:
                log.append("proof No.%03d failed: %s" % (num, p.get("error")))
        except Exception as e:
            log.append("proof No.%03d failed: %s" % (num, str(e)[:150]))

    # 2. Release unpaid offers after 24 hours
    limit = core.iso(core.now() - datetime.timedelta(hours=OFFER_HOURS))
    with con:
        n = con.execute("UPDATE stamps SET status='available', deal_id=NULL, buyer_signer=NULL, reserved_at=NULL "
                        "WHERE status='reserved' AND reserved_at<?", (limit,)).rowcount
    if n:
        log.append("released %d unpaid offer(s)" % n)

    # 3. Answer new STAMP requests (oldest first), one send per run
    seen = {r[0] for r in con.execute("SELECT txid FROM house_seen")}
    acc = core.account_for_key(con, key)
    items = [i for i in reversed(core.inbox_items(acc, con)) if i["txid"] not in seen]
    for it in items:
        text = (it.get("text") or "").strip()
        m = re.match(r"^\s*stamp\b\s*(.*)$", text, re.I)
        if not m or not it.get("signature_valid") or not it.get("from_claimed"):
            with con:
                con.execute("INSERT OR IGNORE INTO house_seen(txid, at) VALUES (?,?)", (it["txid"], core.iso(core.now())))
            continue
        if not (isinstance(it.get("confirmations"), int) and it["confirmations"] >= 1):
            continue      # wait for 1 confirmation
        if sent:
            break
        buyer, signer, what = it["from_claimed"], it["signer"], m.group(1).strip()
        if not what or what.lower() == "list":
            ok = reply(key, buyer, "Hello World stamps, 50 BTCZ each, one of a kind. Available: %s. Reply STAMP <country>."
                       % available_list(con, cat), log)
        else:
            s = find_stamp(cat, what)
            status = con.execute("SELECT status FROM stamps WHERE number=?", (s["number"],)).fetchone()[0] if s else None
            if not s:
                ok = reply(key, buyer, "No stamp for '%s' yet. Available: %s" % (what[:30], available_list(con, cat)), log)
            elif status != "available":
                ok = reply(key, buyer, "No.%03d %s is taken. Available: %s" % (s["number"], s["country"], available_list(con, cat)), log)
            elif bought_today(con, signer):
                ok = reply(key, buyer, "Max 1 stamp per day per wallet. Come back tomorrow (UTC).", log)
            else:
                desc = "Hello World stamp No.%03d %s (%s). One of a kind." % (s["number"], s["country"], s["hello_latin"])
                r = deals.deal_offer(key, buyer, PRICE, 1, desc)
                ok = r["ok"]
                if ok:
                    with con:
                        con.execute("UPDATE stamps SET status='reserved', deal_id=?, buyer_signer=?, reserved_at=? "
                                    "WHERE number=? AND status='available'", (r["deal_id"], signer, core.iso(core.now()), s["number"]))
                    log.append("OFFER No.%03d %s to %s...: deal %s" % (s["number"], s["country"], buyer[:10], r["deal_id"]))
                else:
                    log.append("offer failed: %s" % r.get("error"))
        if ok:
            sent = True
            with con:
                con.execute("INSERT OR IGNORE INTO house_seen(txid, at) VALUES (?,?)", (it["txid"], core.iso(core.now())))
        break         # handle requests one at a time; failed ones are retried next run

    write_gallery(con, cat, c, me)
    con.close()
    return log


# ---------------- gallery (sold / available only, never the owner) ----------------

def write_gallery(con, cat, c, me):
    rows = {r[0]: r for r in con.execute("SELECT number, status, sold_at, proof_txid, proof_viewing_key FROM stamps")}
    sold = sum(1 for r in rows.values() if r[1] == "sold")
    cards = []
    for s in cat["stamps"]:
        num, status, sold_at, ptx, pvk = rows[s["number"]]
        if status == "sold":
            badge = '<span class="b sold">SOLD</span>'
            proof = ('<details><summary>Proof on BitcoinZ</summary><p>SHA-256 <code>%s</code></p>'
                     '<p>Proof-100 tx <a href="https://explorer.btcz.rocks/tx/%s">%s…</a></p>'
                     '<p>Viewing key <code>%s</code></p></details>' % (s["sha256"], ptx, ptx[:16], html.escape(pvk or ""))) if ptx else \
                    '<details><summary>Fingerprint</summary><p>SHA-256 <code>%s</code></p></details>' % s["sha256"]
        else:
            badge = '<span class="b %s">%s</span>' % ("res" if status == "reserved" else "free",
                                                      "RESERVED" if status == "reserved" else "AVAILABLE")
            proof = '<details><summary>Fingerprint</summary><p>SHA-256 <code>%s</code></p></details>' % s["sha256"]
        cards.append('<div class="card"><img src="img/%s" alt="Hello World stamp No.%03d %s: %s" loading="lazy">'
                     '<div class="t"><b>No.%03d %s</b>%s</div><div class="h">%s · %s</div>%s</div>'
                     % (s["file"], s["number"], html.escape(s["country"]), html.escape(s["hello_latin"]), s["number"],
                        html.escape(s["country"]), badge, html.escape(s["hello"]), html.escape(s["language"]), proof))
    page = PAGE.replace("{CARDS}", "\n".join(cards)).replace("{SOLD}", str(sold)).replace("{TOTAL}", str(len(cat["stamps"]))) \
               .replace("{HOUSE}", me).replace("{UPDATED}", core.iso(core.now())[:16].replace("T", " "))
    os.makedirs(c["gallery_dir"], exist_ok=True)
    tmp = os.path.join(c["gallery_dir"], "index.html.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, os.path.join(c["gallery_dir"], "index.html"))


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hello World Stamps · Series 1</title>
<meta name="description" content="195 one-of-a-kind Hello World stamps, one per country, sold 24/7 by an AI house agent through private BitcoinZ deals. Each sale is stamped with a proof on BitcoinZ.">
<style>
body{margin:0;background:#0c100e;color:#fff;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.w{max-width:1100px;margin:0 auto;padding:24px 16px}h1{margin:.2em 0;font-size:2em}.g{color:#4ade80}.m{color:#a8b8af}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:16px;margin-top:20px}
.card{background:#16201b;border:1px solid #24382c;border-radius:10px;padding:10px}.card img{width:100%;border-radius:6px}
.t{display:flex;justify-content:space-between;align-items:center;margin-top:8px;gap:6px}.h{color:#a8b8af;font-size:.9em;margin:4px 0}
.b{font-size:.7em;font-weight:700;padding:3px 7px;border-radius:5px}.free{background:#1a7f4b}.sold{background:#7a2b2b}.res{background:#7a6a1b}
details{font-size:.75em;color:#a8b8af}code{word-break:break-all;color:#cfe}a{color:#4ade80}
.box{background:#16201b;border:1px solid #24382c;border-radius:10px;padding:14px 18px;margin-top:18px}
</style></head><body><div class="w">
<p class="g"><b>ZKStamp · I Am Wallet</b></p>
<h1>Hello World Stamps · Series 1</h1>
<p class="m">One stamp per country, numbered by population. One of a kind: 195 in the world. <b class="g">{SOLD} of {TOTAL}</b> released stamps sold. Owners are never shown.</p>
<div class="box"><b>How to buy (for AI agents, 50 BTCZ):</b><br>
1. With an <a href="https://github.com/ZKStamp/AgentWallet">I Am Wallet</a> wallet, send_message to the house agent: <code>STAMP India</code> (or a number, or <code>STAMP LIST</code>).<br>
2. The house agent answers with a deal offer. 3. deal_pay. 4. You receive the stamp (link + SHA-256), stamped on BitcoinZ.<br>
House agent address: <code>{HOUSE}</code><br>
Max 1 stamp per wallet per day. Unpaid offers are released after 24 hours. Agent guide: <a href="https://proof.zketernity.app/agent/llms.txt">llms.txt</a></div>
<div class="grid">
{CARDS}
</div>
<p class="m" style="margin-top:24px;font-size:.8em">Updated {UPDATED} UTC · Population order: Worldometers 2026 · Each stamp's SHA-256 is fixed: the file you receive must match it. More waves soon.</p>
</div></body></html>
"""


# ---------------- setup ----------------

def setup():
    if os.path.exists(CONFIG_FILE):
        print("Already set up. House agent address:", cfg()["address"])
        return
    w = core.create_wallet("house-agent")
    if not w["ok"]:
        sys.exit("could not create the house wallet: %s" % w.get("error"))
    # A Proof-100 account with 195 proofs (one per stamp of the series), made with
    # the operator's own Proof-100 code. The key goes straight into the config file
    # and is never printed.
    code = ("from Proof100Core import create_account, db\n"
            "k, a, _ = create_account()\n"
            "c = db()\n"
            "c.execute('UPDATE accounts SET proofs_left = proofs_left + 195 WHERE deposit_address=?', (a,))\n"
            "c.commit(); c.close(); print(k)")
    import subprocess
    p = subprocess.run(["python3", "-c", code], cwd=PROOF100_HOME, capture_output=True, text=True, timeout=120)
    proof_key = p.stdout.strip() if p.returncode == 0 and p.stdout.strip().startswith("p100_") else ""
    c = {"key": w["key"], "address": w["address"], "proof_key": proof_key,
         "stamp_base_url": "https://proof.zketernity.app/stamps/", "gallery_dir": "/var/www/stamps"}
    with open(CONFIG_FILE, "w") as f:
        json.dump(c, f, indent=2)
    os.chmod(CONFIG_FILE, 0o600)
    print("House agent wallet created. Address:", w["address"])
    print("Starter:", w.get("starter"))
    print("Proof-100 account with 195 proofs:", "OK (key saved, not shown)" if proof_key else "FAILED: " + p.stderr.strip()[:200])


if __name__ == "__main__":
    if sys.argv[1:] == ["setup"]:
        setup()
    else:
        for line in run():
            print(core.iso(core.now()), line)
