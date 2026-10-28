"""Send starter funds to queued wallets. Run every minute by AgentWalletGrants.timer."""
import WalletCore as core

for line in core.grant_starters():
    print(core.iso(core.now()), line)
