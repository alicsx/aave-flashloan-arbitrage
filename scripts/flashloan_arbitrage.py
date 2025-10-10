import os
import brownie
from brownie import config, network, FlashLoanArbitrage, Contract, interface
from scripts.helper_scripts import get_account, toWei, fromWei, approve_erc20, FORKED_BLOCHCHAINS
from scripts.get_weth import get_weth
from scripts.preflight_check import run_preflight  # new import
from decimal import Decimal

ETHERSCAN_TX_URL = "https://kovan.etherscan.io/tx/{}"

weth_token = config["networks"][network.show_active()]["weth-token"]
dai_token = config["networks"][network.show_active()]["dai-token"]

uni_router_address = config["networks"][network.show_active()]["uniswap-router"]
sushi_router_address = config["networks"][network.show_active()]["sushiswap-router"]
aave_address_provider = config["networks"][network.show_active()]["provider"]


def deploy():

    account = get_account()

    if network.show_active() in FORKED_BLOCHCHAINS:
        get_weth(account, 10)

    arbitrage = FlashLoanArbitrage.deploy(
        aave_address_provider,
        uni_router_address,
        sushi_router_address,
        weth_token,
        dai_token,
        {"from": account}
    )

    # Put here the amount of ETH you want to deposit (in wei-like units for WETH)
    amount = toWei(5)

    approve_erc20(weth_token, arbitrage.address, amount, account)

    deposit_tx = arbitrage.deposit(amount, {"from": account})
    deposit_tx.wait(1)

    weth_balance = arbitrage.getERC20Balance(weth_token)
    print("amount deposited: ", fromWei(weth_balance))

    # ----------------- PRE-FLIGHT CHECK BEFORE SENDING FLASHLOAN -----------------
    # We will estimate profit by simulating:
    # 1) swap WETH -> DAI on Uniswap (router_uni)
    # 2) swap DAI -> WETH on Sushiswap (router_sushi)
    # then compute profit_in_eth - gas_cost_eth and decide.
    borrow_amount = toWei(20)  # amount to flashloan (same as previous code)

    # paths used by contract (WETH -> DAI) and (DAI -> WETH)
    path_swap1 = [weth_token, dai_token]
    path_swap2 = [dai_token, weth_token]

    # run preflight: returns dict with proceed True/False and reason + estimates
    pre = run_preflight(
        account=account,
        flashloan_contract=arbitrage,
        borrow_token_address=weth_token,
        amount_to_borrow_wei=borrow_amount,
        router_swap1=uni_router_address,
        path_swap1=path_swap1,
        router_swap2=sushi_router_address,
        path_swap2=path_swap2,
        weth_address=weth_token  # pass weth token from config (works on fork/mainnet)
    )

    print("PRE-FLIGHT:", pre)
    if not pre.get("proceed", False):
        print("Aborting execution: preflight check failed or not profitable ->", pre.get("reason"))
        return

    # If preflight ok -> send the flashloan transaction (use same signature you had)
    try:
        # Your original call used this syntax (overloaded function):
        flash_tx = arbitrage.flashloan['address,uint'](weth_token, borrow_amount, {"from": account})
        flash_tx.wait(1)
        print("Flashloan tx sent, tx:", flash_tx.txid if hasattr(flash_tx, "txid") else flash_tx)
    except Exception as e:
        print("Transaction execution failed:", e)
        return

    if network.show_active() == "kovan":
        print("View your flashloan tx here: " + ETHERSCAN_TX_URL.format(flash_tx.txid))


def main():
    deploy()
