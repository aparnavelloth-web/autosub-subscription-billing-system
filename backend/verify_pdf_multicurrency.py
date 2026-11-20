"""
Multi-Currency Invoice PDF Verification Script.
Tests font registration, symbol mapping, and PDF generation across INR, USD, EUR, GBP, CAD, AUD.
"""
from datetime import datetime, timezone
from decimal import Decimal
import os
import re
from unittest.mock import MagicMock
import zlib

from reportlab.pdfbase import pdfutils

from app.models.invoice import Invoice, InvoiceItem, InvoiceItemType, InvoiceStatus
from app.services.pdf_service import (
    CURRENCY_SYMBOLS,
    InvoicePDFService,
    format_currency_amount,
    get_currency_symbol,
)


def extract_content_stream_text(pdf_data: bytes) -> str:
    """Extracts and decompresses the page content stream from a ReportLab PDF."""
    idx = pdf_data.find(b"/Filter [ /ASCII85Decode /FlateDecode ]")
    if idx == -1:
        return ""
    chunk = pdf_data[idx:]
    s_idx = chunk.find(b"stream") + 6
    while chunk[s_idx:s_idx+1] in (b"\r", b"\n"):
        s_idx += 1
    e_idx = chunk.find(b"endstream")
    stream_bytes = chunk[s_idx:e_idx].strip()
    decompressed = zlib.decompress(pdfutils.asciiBase85Decode(stream_bytes))
    return decompressed.decode("latin1", errors="ignore")


def run_verification():
    print("=" * 60)
    print("STEP 1: Verify Currency Mapping & Helper Functions")
    print("=" * 60)

    expected_symbols = {
        "INR": "₹",
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "CAD": "CA$",
        "AUD": "AU$",
    }

    for curr, sym in expected_symbols.items():
        assert get_currency_symbol(curr) == sym, f"Symbol mismatch for {curr}: got {get_currency_symbol(curr)}"
        fmt = format_currency_amount(Decimal("1234.50"), curr)
        assert fmt == f"{sym} 1234.50", f"Formatting mismatch: {fmt} != {sym} 1234.50"
        neg_fmt = format_currency_amount(Decimal("-50.00"), curr)
        assert neg_fmt == f"-{sym} 50.00", f"Negative formatting mismatch: {neg_fmt}"
        print(f" [PASS] {curr} -> Symbol '{sym}' -> Sample: '{fmt}', Negative: '{neg_fmt}'")

    print("\n" + "=" * 60)
    print("STEP 2: Generate Actual PDFs for INR, USD, EUR, GBP, CAD, AUD")
    print("=" * 60)

    currencies_to_test = [
        ("INR", Decimal("4999.00"), Decimal("0.18"), Decimal("500.00")),
        ("USD", Decimal("49.00"), Decimal("0.08"), Decimal("0.00")),
        ("EUR", Decimal("79.50"), Decimal("0.20"), Decimal("10.00")),
        ("GBP", Decimal("89.00"), Decimal("0.20"), Decimal("0.00")),
        ("CAD", Decimal("120.00"), Decimal("0.13"), Decimal("20.00")),
        ("AUD", Decimal("150.00"), Decimal("0.10"), Decimal("0.00")),
    ]

    for curr, unit_price, tax_rate, discount in currencies_to_test:
        subtotal = unit_price * 2
        tax_amt = ((subtotal - discount) * tax_rate).quantize(Decimal("0.01"))
        total = (subtotal - discount + tax_amt).quantize(Decimal("0.01"))

        invoice = Invoice(
            id=1001,
            invoice_number=f"INV-TEST-{curr}-001",
            customer_id=1,
            subscription_id=1,
            status=InvoiceStatus.OPEN,
            currency=curr,
            subtotal=subtotal,
            tax_amount=tax_amt,
            discount_amount=discount,
            total_amount=total,
            amount_paid=Decimal("0.00"),
            issue_date=datetime.now(timezone.utc),
            due_date=datetime.now(timezone.utc),
            notes=f"AutoSub test invoice for {curr} billing verification.",
        )

        mock_customer = MagicMock()
        mock_customer.full_name = f"Test Customer ({curr})"
        mock_customer.email = f"billing_{curr.lower()}@example.com"
        mock_customer.customer_code = f"CUST-{curr}-001"
        invoice.customer = mock_customer

        mock_plan = MagicMock()
        mock_plan.name = f"Enterprise Plan ({curr})"
        mock_plan.code = f"ENT-{curr}"

        mock_sub = MagicMock()
        mock_sub.subscription_code = f"SUB-{curr}-001"
        mock_sub.plan = mock_plan
        invoice.subscription = mock_sub

        item1 = InvoiceItem(
            id=1,
            invoice_id=1001,
            item_type=InvoiceItemType.BASE_SUBSCRIPTION,
            description=f"Enterprise Base Subscription ({curr})",
            quantity=2,
            unit_price=unit_price,
            amount=subtotal,
        )
        invoice.items = [item1]

        pdf_bytes = InvoicePDFService.generate_invoice_pdf(invoice)
        assert len(pdf_bytes) > 1000, f"Generated PDF for {curr} too small: {len(pdf_bytes)} bytes"
        assert pdf_bytes.startswith(b"%PDF-"), f"Generated data for {curr} is not valid PDF"

        filename = f"sample_{curr.lower()}_invoice.pdf"
        with open(filename, "wb") as f:
            f.write(pdf_bytes)

        print(f" [PASS] Generated {filename} ({len(pdf_bytes)} bytes) with currency {curr} ({expected_symbols[curr]})")

    print("\n" + "=" * 60)
    print("STEP 3: Inspect Font Streams & ToUnicode CMap for Currency Symbols")
    print("=" * 60)

    for curr, fn, hex_code in [
        ("INR", "sample_inr_invoice.pdf", "20B9"),
        ("EUR", "sample_eur_invoice.pdf", "20AC"),
        ("GBP", "sample_gbp_invoice.pdf", "00A3"),
    ]:
        with open(fn, "rb") as f:
            data = f.read()
        pattern = re.compile(rb"/Length\s+(\d+).*?stream[\r\n]+", re.DOTALL)
        found = False
        for m in pattern.finditer(data):
            stream_start = m.end()
            length = int(m.group(1))
            stream_bytes = data[stream_start:stream_start + length]
            try:
                dec = zlib.decompress(stream_bytes)
                dec_str = dec.decode("latin1", errors="ignore")
                if "beginbfchar" in dec_str and hex_code in dec_str.upper():
                    found = True
                    print(f" [PASS] {curr} ({fn}): CMap verified! Unicode U+{hex_code} ({expected_symbols[curr]}) is mapped into font.")
                    break
            except Exception:
                pass
        assert found, f"CMap for {curr} must contain U+{hex_code}"

    # Verify content stream amounts for USD, CAD, AUD
    with open("sample_usd_invoice.pdf", "rb") as f:
        usd_text = extract_content_stream_text(f.read())
    assert "$ 49.00" in usd_text and "$ 98.00" in usd_text and "$ 105.84" in usd_text
    print(" [PASS] USD (sample_usd_invoice.pdf): Content stream verified with symbol '$' and two-decimal precision.")

    with open("sample_cad_invoice.pdf", "rb") as f:
        cad_text = extract_content_stream_text(f.read())
    assert "CA$ 120.00" in cad_text and "CA$ 240.00" in cad_text
    print(" [PASS] CAD (sample_cad_invoice.pdf): Content stream verified with symbol 'CA$' and two-decimal precision.")

    with open("sample_aud_invoice.pdf", "rb") as f:
        aud_text = extract_content_stream_text(f.read())
    assert "AU$ 150.00" in aud_text and "AU$ 300.00" in aud_text
    print(" [PASS] AUD (sample_aud_invoice.pdf): Content stream verified with symbol 'AU$' and two-decimal precision.")

    print("\n" + "=" * 60)
    print("ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    run_verification()
