"""Form 4 XML parser tests with a synthetic ownership document."""

from __future__ import annotations

from layers.layer4.form4_parser import parse_form4

FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <issuer>
    <issuerCik>0001234567</issuerCik>
    <issuerName>Acme Therapeutics Inc</issuerName>
    <issuerTradingSymbol>ACME</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Doe Jane</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>CEO</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-05-01</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>5.50</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <securityTitle><value>Common Stock</value></securityTitle>
      <transactionDate><value>2026-05-02</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>400</value></transactionShares>
        <transactionPricePerShare><value>6.00</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
"""


def test_parse_issuer_and_owner():
    res = parse_form4(FORM4_XML)
    assert res["issuer_symbol"] == "ACME"
    assert res["issuer_name"] == "Acme Therapeutics Inc"
    assert res["owners"][0]["name"] == "Doe Jane"
    assert "Director" in res["owners"][0]["role"]
    assert "CEO" in res["owners"][0]["role"]


def test_parse_transactions_purchase_and_sale():
    res = parse_form4(FORM4_XML)
    txns = res["transactions"]
    assert len(txns) == 2
    buy = next(t for t in txns if t["transaction_code"] == "P")
    assert buy["is_purchase"] is True
    assert buy["shares"] == 1000.0
    assert buy["price_per_share"] == 5.5
    assert buy["value_usd"] == 5500.0
    assert buy["insider_name"] == "Doe Jane"
    sale = next(t for t in txns if t["transaction_code"] == "S")
    assert sale["is_purchase"] is False
    assert sale["value_usd"] == 2400.0


def test_parse_garbage_returns_empty():
    res = parse_form4("not xml at all <<<")
    assert res["transactions"] == []
    assert res["issuer_symbol"] is None
