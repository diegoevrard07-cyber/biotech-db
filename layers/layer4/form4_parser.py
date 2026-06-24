"""
SEC Form 4 (insider transaction) XML parser.

Pure function: takes the ownership-document XML text and returns issuer +
a flat list of transactions. Form 4 ownership XML carries no namespace, so we
use stdlib ElementTree. Robust to missing nodes (insiders file messy XML).

Transaction codes of interest:
  P = open-market purchase (bullish signal)   S = open-market sale
  A = grant/award   M = option exercise   F = tax withholding   G = gift
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

OPEN_MARKET_PURCHASE = "P"


def _text(node, path: str) -> str | None:
    if node is None:
        return None
    el = node.find(path)
    if el is None:
        return None
    val = (el.text or "").strip()
    return val or None


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _value_text(node, path: str) -> str | None:
    """Form 4 wraps many fields as <field><value>X</value></field>."""
    if node is None:
        return None
    el = node.find(path)
    if el is None:
        return None
    inner = el.find("value")
    if inner is not None:
        return (inner.text or "").strip() or None
    return (el.text or "").strip() or None


def _owner_role(owner) -> str | None:
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    if rel is None:
        return None
    roles: list[str] = []
    if _text(rel, "isDirector") in ("1", "true"):
        roles.append("Director")
    if _text(rel, "isOfficer") in ("1", "true"):
        title = _text(rel, "officerTitle")
        roles.append(f"Officer ({title})" if title else "Officer")
    if _text(rel, "isTenPercentOwner") in ("1", "true"):
        roles.append("10% Owner")
    if _text(rel, "isOther") in ("1", "true"):
        roles.append("Other")
    return ", ".join(roles) if roles else None


def _parse_transactions(table, *, is_derivative: bool) -> list[dict]:
    if table is None:
        return []
    tag = "derivativeTransaction" if is_derivative else "nonDerivativeTransaction"
    out: list[dict] = []
    for txn in table.findall(tag):
        coding = txn.find("transactionCoding")
        code = _text(coding, "transactionCode") if coding is not None else None
        amounts = txn.find("transactionAmounts")
        shares = _float(_value_text(amounts, "transactionShares")) if amounts is not None else None
        price = _float(_value_text(amounts, "transactionPricePerShare")) if amounts is not None else None
        ad = _value_text(amounts, "transactionAcquiredDisposedCode") if amounts is not None else None
        txn_date = _value_text(txn, "transactionDate")
        security = _text(txn, "securityTitle/value") or _value_text(txn, "securityTitle")
        value_usd = round(shares * price, 2) if (shares is not None and price is not None) else None
        out.append(
            {
                "transaction_date": txn_date,
                "transaction_code": code,
                "shares": shares,
                "price_per_share": price,
                "value_usd": value_usd,
                "acquired_disposed": ad,
                "is_purchase": code == OPEN_MARKET_PURCHASE,
                "security_title": security,
                "is_derivative": is_derivative,
            }
        )
    return out


def parse_form4(xml_text: str) -> dict[str, Any]:
    """Parse Form 4 XML into {issuer, owners, transactions}. Never raises on content."""
    result: dict[str, Any] = {
        "issuer_cik": None,
        "issuer_name": None,
        "issuer_symbol": None,
        "owners": [],
        "transactions": [],
    }
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return result

    issuer = root.find("issuer")
    if issuer is not None:
        result["issuer_cik"] = _text(issuer, "issuerCik")
        result["issuer_name"] = _text(issuer, "issuerName")
        result["issuer_symbol"] = _text(issuer, "issuerTradingSymbol")

    owners = []
    for owner in root.findall("reportingOwner"):
        oid = owner.find("reportingOwnerId")
        name = _text(oid, "rptOwnerName") if oid is not None else None
        owners.append({"name": name, "role": _owner_role(owner)})
    result["owners"] = owners

    primary_name = owners[0]["name"] if owners else None
    primary_role = owners[0]["role"] if owners else None

    txns = _parse_transactions(root.find("nonDerivativeTable"), is_derivative=False)
    txns += _parse_transactions(root.find("derivativeTable"), is_derivative=True)
    for t in txns:
        t["insider_name"] = primary_name
        t["insider_role"] = primary_role
    result["transactions"] = txns
    return result
