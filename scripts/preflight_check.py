# scripts/preflight_check.py
"""
Pre-flight check for aave-flashloan-arbitrage repo (Brownie).
محاسبه‌ی تخمینی getAmountsOut، برآورد گس، محاسبه‌ی هزینه‌ی گس به ETH،
محاسبه‌ی سود خالص و تصمیم‌گیری برای ادامه یا عدم ادامه اجرای معامله.
"""

import os
from decimal import Decimal, getcontext

from brownie import interface, network
from brownie.network.web3 import web3

# برای دقت محاسبات اعشاری
getcontext().prec = 36

# پارامترهای پیش‌فرض (قابل override از .env)
MIN_PROFIT_ETH = Decimal(os.getenv("MIN_PROFIT_ETH", "0.001"))        # حداقل سود خالص (ETH)
SLIPPAGE_TOLERANCE = Decimal(os.getenv("SLIPPAGE_TOLERANCE", "0.005"))  # 0.5% پیش‌فرض
MAX_GAS_LIMIT = int(os.getenv("MAX_GAS_LIMIT", "1000000"))           # حداکثر گس قابل قبول (fallback)

# آدرس‌های پیش‌فرض که در mainnet به‌کار می‌رود؛ در صورت تست‌نت یا fork آدرس‌ها را تنظیم کن.
# اگر از mainnet-fork استفاده می‌کنی، این آدرس‌ها باید همان آدرس‌های اصلی mainnet باشند.
WETH_ADDRESS_MAINNET = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
UNISWAP_V2_ROUTER_MAINNET = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"  # UniswapV2 router
SUSHISWAP_ROUTER_MAINNET = "0xd9e1CE17f2641f24aE83637ab66a2cca9C378B9F"   # Sushiswap (old mainnet)

def wei_to_decimal(wei_amount):
    return Decimal(wei_amount) / Decimal(10 ** 18)

def decimal_to_wei(dec_amount):
    return int((dec_amount * Decimal(10**18)).to_integral_value())

def get_gas_price_wei():
    # web3.eth.gas_price returns wei
    try:
        return web3.eth.gas_price
    except Exception:
        # fallback کوچک
        return int(20 * 10**9)  # 20 gwei fallback

def estimate_tx_gas(tx_dict):
    try:
        g = web3.eth.estimate_gas(tx_dict)
        # محدودیت به MAX_GAS_LIMIT
        return min(g, MAX_GAS_LIMIT)
    except Exception as e:
        # در حالت failure، None برگردان یا مقدار محافظه‌کارانه
        return None

def get_amounts_out(router_address, amount_in, path):
    """
    wrapper برای router.getAmountsOut
    amount_in باید به فرم integer (wei-like) باشد برای توکن موردنظر (توجه به decimals).
    path : list از آدرس توکن‌ها (addresses)
    """
    router = interface.IUniswapV2Router02(router_address)
    return router.getAmountsOut(amount_in, path)

def conservative_amount_with_slippage(amount_out_int):
    """
    اعمال slippage tolerance: خروجی تئوریک رو به صورت محافظه‌کارانه کاهش میدیم
    amount_out_int: integer (wei-like)
    """
    out_dec = Decimal(amount_out_int)
    out_after_slippage = out_dec * (Decimal(1) - SLIPPAGE_TOLERANCE)
    return int(out_after_slippage.to_integral_value())

def run_preflight(account, flashloan_contract, borrow_token_address, amount_to_borrow_wei,
                  path_swap1, router_swap1, path_swap2, router_swap2,
                  weth_address=None):
    """
    ورودی‌ها:
    - account: Brownie account object (که از آن ارسال می‌شه)
    - flashloan_contract: شئِ قرارداد deploy شده (brownie ContractContainer/Contract)
    - borrow_token_address: آدرس توکنی که flashloan از آون گرفته می‌شه
    - amount_to_borrow_wei: مقدار قرض به صورت integer (wei-like مطابق decimals توکن)
    - path_swap1, router_swap1: مسیر و router برای swap اول (مثلاً Uniswap)
    - path_swap2, router_swap2: مسیر و router برای swap دوم (مثلاً Sushiswap)
    - weth_address: آدرس WETH برای تبدیل خروجی نهایی به ETH (اگر None از mainnet constant استفاده می‌کنیم)
    خروجی: dict شامل expected_profit_eth, estimated_gas, gas_cost_eth, net_profit_eth, proceed, reason
    """
    if weth_address is None:
        weth_address = WETH_ADDRESS_MAINNET

    result = {
        "proceed": False,
        "reason": "unknown",
    }

    # 1) تخمین خروجی swap اول (getAmountsOut)
    try:
        # خروجی swap1: amounts_out_a[-1] مقدار توکن‌ای است که بعد از swap اول حاصل می‌شود
        amounts_out_a = get_amounts_out(router_swap1, amount_to_borrow_wei, path_swap1)
        out_after_a = amounts_out_a[-1]
    except Exception as e:
        result.update({"proceed": False, "reason": f"getAmountsOut swap1 failed: {e}"})
        return result

    # 2) تخمین خروجی swap دوم: از توکن خروجی swap1 به borrow_token یا به WETH برای محاسبه ETH
    # اگر هدف محاسبه سود به ETH است، ابتدا مقدار خروجی را به WETH تبدیل می‌کنیم
    try:
        # تبدیل out_after_a -> WETH
        amounts_to_weth = get_amounts_out(router_swap2, out_after_a, [path_swap1[-1], weth_address])
        weth_amount_out = amounts_to_weth[-1]
        profit_in_eth_est = wei_to_decimal(weth_amount_out) - wei_to_decimal(amount_to_borrow_wei)
    except Exception as e:
        result.update({"proceed": False, "reason": f"getAmountsOut to WETH failed: {e}"})
        return result

    # 3) ساخت calldata برای فراخوانی تابع استراتژی در قرارداد فلش‌لون (نام تابع ممکن است در رپو فرق کند)
    # اینجا ما تلاش می‌کنیم calldata را encode کنیم. اگر اسم فانکشن در قرارداد متفاوت است، آن را جایگزین کن.
    try:
        # نام تابع نمونه: "startArbitrage(uint256,address[],address[])"
        # اگر قرارداد تو اسم متفاوتی دارد (مثلاً executeOperation یا startFlashloan) آنجا را تغییر بده.
        # توجه: encode_input روش Brownie Contract object است.
        calldata = flashloan_contract.startArbitrage.encode_input(amount_to_borrow_wei, path_swap1, path_swap2)
        tx_stub = {
            "to": flashloan_contract.address,
            "from": account.address,
            "data": calldata,
            "value": 0
        }
        estimated_gas = estimate_tx_gas(tx_stub)
        if estimated_gas is None:
            # fallback: اگر estimate نشد، مقدار محافظه‌کارانه تعیین کن
            estimated_gas = MAX_GAS_LIMIT
    except Exception as e:
        result.update({"proceed": False, "reason": f"calldata/estimate preparation failed: {e}"})
        return result

    gas_price = get_gas_price_wei()
    gas_cost_eth = wei_to_decimal(estimated_gas * gas_price)

    # 4) محاسبه سود خالص (سود تئوریک - هزینه گس)
    net_profit = profit_in_eth_est - gas_cost_eth

    # 5) تصمیم‌گیری با آستانه MIN_PROFIT_ETH
    if net_profit <= 0:
        result.update({
            "expected_profit_eth": float(profit_in_eth_est),
            "estimated_gas": int(estimated_gas),
            "gas_cost_eth": float(gas_cost_eth),
            "net_profit_eth": float(net_profit),
            "proceed": False,
            "reason": f"net loss: {net_profit} ETH (profit {profit_in_eth_est} - gas {gas_cost_eth})"
        })
    elif net_profit < MIN_PROFIT_ETH:
        result.update({
            "expected_profit_eth": float(profit_in_eth_est),
            "estimated_gas": int(estimated_gas),
            "gas_cost_eth": float(gas_cost_eth),
            "net_profit_eth": float(net_profit),
            "proceed": False,
            "reason": f"net profit {net_profit} ETH below MIN_PROFIT_ETH {MIN_PROFIT_ETH}"
        })
    else:
        result.update({
            "expected_profit_eth": float(profit_in_eth_est),
            "estimated_gas": int(estimated_gas),
            "gas_cost_eth": float(gas_cost_eth),
            "net_profit_eth": float(net_profit),
            "proceed": True,
            "reason": f"net profit {net_profit} ETH OK"
        })

    return result
