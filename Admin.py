"""Operator tool for Agent Wallet. Run on the server:

  python3 /root/agentwallet/Admin.py setup            create pool + fee addresses (first time only)
  python3 /root/agentwallet/Admin.py status           pool, fees, wallets, today's activity
  python3 /root/agentwallet/Admin.py freeze all       KILL SWITCH: stop every send and sign-up
  python3 /root/agentwallet/Admin.py unfreeze all
  python3 /root/agentwallet/Admin.py freeze 7         freeze wallet number 7 only
  python3 /root/agentwallet/Admin.py unfreeze 7
"""
import json, os, secrets, sys
import ChainNode as chain
import WalletCore as core


def setup():
    if os.path.exists(core.CONFIG_FILE):
        print("Already set up:")
        cfg = core.config()
    else:
        cfg = {"pool_address": chain.new_z_address(),
               "fee_address": chain.new_z_address(),
               "ip_salt": secrets.token_hex(16)}
        with open(core.CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        os.chmod(core.CONFIG_FILE, 0o600)
        print("Created:")
    core.db().close()
    print("STARTER POOL address (send the starter BTCZ here):")
    print(cfg["pool_address"])
    print("FEE address (the 1 BTCZ fees collect here):")
    print(cfg["fee_address"])


def status():
    cfg = core.config()
    con = core.db()
    d = core.today()
    total = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    st = dict(con.execute("SELECT starter_status, COUNT(*) FROM accounts GROUP BY starter_status").fetchall())
    new_today = con.execute("SELECT COUNT(*) FROM accounts WHERE substr(created_at,1,10)=?", (d,)).fetchone()[0]
    sends = con.execute("SELECT kind, status, COUNT(*) FROM sends WHERE substr(created_at,1,10)=? GROUP BY kind, status", (d,)).fetchall()
    frozen = con.execute("SELECT id FROM accounts WHERE frozen=1").fetchall()
    gift = con.execute("SELECT COALESCE(SUM(CAST(gift_btcz AS REAL)),0), COALESCE(SUM(CAST(gift_deal_used AS REAL)),0) FROM accounts").fetchone()
    deals = con.execute("SELECT role, COUNT(*), SUM(done_steps) FROM deals GROUP BY role").fetchall()
    print("Starter gift coins held by wallets:", round(gift[0], 4), "BTCZ | used in deals:", round(gift[1], 4), "BTCZ")
    print("Deals (last 7 days):", [tuple(d) for d in deals] or "none")
    print("Kill switch (all frozen):", "ON" if core.is_frozen_all(con) else "off")
    con.close()
    print("Starter pool balance:", chain.balance(cfg["pool_address"]), "BTCZ")
    print("Fee balance:         ", chain.balance(cfg["fee_address"]), "BTCZ")
    print("Wallets:", total, "| new today:", new_today)
    print("Starter: sent", st.get("sent", 0), "| queued", st.get("pending", 0), "| none", st.get("none", 0))
    print("Sends today:", [tuple(s) for s in sends] or "none")
    print("Frozen wallets:", [f[0] for f in frozen] or "none")


def main(a):
    if not a:
        print(__doc__)
    elif a[0] == "setup":
        setup()
    elif a[0] == "status":
        status()
    elif a[0] in ("freeze", "unfreeze") and len(a) == 2:
        flag = a[0] == "freeze"
        if a[1] == "all":
            core.set_frozen_all(flag)
            print("All wallets", "FROZEN" if flag else "unfrozen")
        else:
            print("Wallet", a[1], ("frozen" if flag else "unfrozen") if core.set_frozen_account(int(a[1]), flag) else "not found")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
