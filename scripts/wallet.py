"""Create and prepare the bot's Polymarket wallet. Run this ON THE VPS.

    python -m scripts.wallet new       # make a new key, save it to .env, print the address
    python -m scripts.wallet status    # show POL / USDC.e balances and approvals
    python -m scripts.wallet approve   # approve the Polymarket exchange contracts (costs a little POL)

The private key is written only to .env (mode 600). It is never printed.
"""
import os
import stat
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv
from eth_abi import encode
from eth_account import Account
from py_clob_client.config import get_contract_config

ENV = Path(".env")
CHAIN_ID = 137
MAX_UINT = 2**256 - 1

cfg = get_contract_config(CHAIN_ID)
neg_cfg = get_contract_config(CHAIN_ID, neg_risk=True)
USDC = cfg.collateral  # USDC.e (bridged), not native USDC
CTF = cfg.conditional_tokens
SPENDERS = {"CTF Exchange": cfg.exchange, "Neg Risk Exchange": neg_cfg.exchange}


def rpc(method, params):
    url = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
    r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(body["error"])
    return body["result"]


def call(to, selector, types, args) -> int:
    data = selector + encode(types, args).hex()
    return int(rpc("eth_call", [{"to": to, "data": data}, "latest"]), 16)


def account():
    load_dotenv(ENV)
    key = os.getenv("POLY_PRIVATE_KEY", "")
    if not key or key == "0x...":
        sys.exit("No POLY_PRIVATE_KEY in .env. Run: python -m scripts.wallet new")
    return Account.from_key(key)


def cmd_new():
    load_dotenv(ENV)
    if os.getenv("POLY_PRIVATE_KEY", "0x...") != "0x...":
        sys.exit("A key already exists in .env. Refusing to overwrite it.")
    acct = Account.create()
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    lines = [l for l in lines if not l.startswith(("POLY_PRIVATE_KEY=", "POLY_SIGNATURE_TYPE=", "POLY_FUNDER="))]
    lines += [f"POLY_PRIVATE_KEY={acct.key.hex()}", "POLY_SIGNATURE_TYPE=0"]
    ENV.write_text("\n".join(lines) + "\n")
    ENV.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"New wallet address: {acct.address}")
    print("The key is saved in .env only. Back up .env somewhere safe and offline.")
    print("Next: send POL (about 1, for gas) and USDC.e on Polygon to this address.")


def cmd_status():
    acct = account()
    pol = int(rpc("eth_getBalance", [acct.address, "latest"]), 16) / 1e18
    usdc = call(USDC, "0x70a08231", ["address"], [acct.address]) / 1e6
    print(f"Address : {acct.address}")
    print(f"POL     : {pol:.4f}")
    print(f"USDC.e  : {usdc:.2f}")
    for name, spender in SPENDERS.items():
        allowance = call(USDC, "0xdd62ed3e", ["address", "address"], [acct.address, spender])
        approved = call(CTF, "0xe985e9c5", ["address", "address"], [acct.address, spender])
        print(f"{name:18}: USDC.e allowance {'OK' if allowance > 10**30 else 'MISSING'}, "
              f"CTF approval {'OK' if approved else 'MISSING'}")


def send(acct, to, data):
    tx = {
        "chainId": CHAIN_ID,
        "from": acct.address,
        "to": to,
        "data": data,
        "value": 0,
        "nonce": int(rpc("eth_getTransactionCount", [acct.address, "pending"]), 16),
        "gasPrice": int(int(rpc("eth_gasPrice", []), 16) * 1.25),
    }
    tx["gas"] = int(int(rpc("eth_estimateGas", [{k: (hex(v) if isinstance(v, int) else v)
                                                  for k, v in tx.items() if k != "chainId"}]), 16) * 1.3)
    signed = Account.sign_transaction(tx, acct.key)
    tx_hash = rpc("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex().removeprefix("0x")])
    print(f"  sent {tx_hash}")
    for _ in range(60):
        receipt = rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt:
            if int(receipt["status"], 16) != 1:
                sys.exit(f"  transaction {tx_hash} failed")
            return
        __import__("time").sleep(3)
    sys.exit(f"  no receipt for {tx_hash} after 3 minutes. Run 'status' to check.")


def cmd_approve():
    acct = account()
    for name, spender in SPENDERS.items():
        if call(USDC, "0xdd62ed3e", ["address", "address"], [acct.address, spender]) < 10**30:
            print(f"Approving USDC.e for {name}")
            send(acct, USDC, "0x095ea7b3" + encode(["address", "uint256"], [spender, MAX_UINT]).hex())
        if not call(CTF, "0xe985e9c5", ["address", "address"], [acct.address, spender]):
            print(f"Approving outcome tokens for {name}")
            send(acct, CTF, "0xa22cb465" + encode(["address", "bool"], [spender, True]).hex())
    print("Approvals done.")
    cmd_status()


if __name__ == "__main__":
    commands = {"new": cmd_new, "status": cmd_status, "approve": cmd_approve}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()
