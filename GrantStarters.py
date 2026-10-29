"""Send starter funds to queued wallets, and move t-deposits into wallets' shielded
addresses. Run every few minutes by AgentWalletGrants.timer."""
import WalletCore as core

for line in core.grant_starters():
    print(core.iso(core.now()), line)
for line in core.sweep_t_deposits():
    print(core.iso(core.now()), line)
