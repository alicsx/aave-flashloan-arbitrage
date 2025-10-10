# scripts/price_utils.py
from brownie import interface
from decimal import Decimal

def token_decimals(token_address):
    token = interface.IERC20(token_address)
    return token.decimals()

def to_wei(amount_decimal, decimals=18):
    """
    تبدیل مقداری که به صورت Decimal یا float آمده به integer مطابق decimals توکن.
    """
    return int(Decimal(amount_decimal) * (10 ** decimals))

def from_wei(amount_int, decimals=18):
    return Decimal(amount_int) / (10 ** decimals)
