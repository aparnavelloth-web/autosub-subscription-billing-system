"""
Invoice PDF Generation Service for AutoSub Billing System.
Generates professional, itemized PDF statements using ReportLab with full multi-currency Unicode font support.
"""
from decimal import Decimal
import io
import os
from typing import List, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.models.invoice import Invoice


# -----------------------------------------------------------------------------
# Multi-Currency Configuration & Formatting Helpers
# -----------------------------------------------------------------------------
CURRENCY_SYMBOLS = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CAD": "CA$",
    "AUD": "AU$",
}

FONT_FAMILY = "AutoSub-Sans"
FONT_NORMAL = "AutoSub-Sans"
FONT_BOLD = "AutoSub-Sans-Bold"

_FONTS_INITIALIZED = False
_ACTIVE_NORMAL_FONT = "Helvetica"
_ACTIVE_BOLD_FONT = "Helvetica-Bold"


def _ensure_unicode_fonts() -> Tuple[str, str]:
    """
    Registers a TrueType Unicode font family capable of rendering international
    currency glyphs (including ₹ for INR, € for EUR, £ for GBP) with cross-platform fallbacks.
    """
    global _FONTS_INITIALIZED, _ACTIVE_NORMAL_FONT, _ACTIVE_BOLD_FONT
    if _FONTS_INITIALIZED:
        return _ACTIVE_NORMAL_FONT, _ACTIVE_BOLD_FONT

    candidates = [
        # Windows standard system fonts
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
        ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
        # Linux standard system fonts
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
    ]

    for norm_path, bold_path in candidates:
        if os.path.isfile(norm_path) and os.path.isfile(bold_path):
            try:
                pdfmetrics.registerFont(TTFont(FONT_NORMAL, norm_path))
                pdfmetrics.registerFont(TTFont(FONT_BOLD, bold_path))
                pdfmetrics.registerFontFamily(
                    FONT_FAMILY,
                    normal=FONT_NORMAL,
                    bold=FONT_BOLD,
                    italic=FONT_NORMAL,
                    boldItalic=FONT_BOLD,
                )
                _ACTIVE_NORMAL_FONT = FONT_NORMAL
                _ACTIVE_BOLD_FONT = FONT_BOLD
                _FONTS_INITIALIZED = True
                return _ACTIVE_NORMAL_FONT, _ACTIVE_BOLD_FONT
            except Exception:
                continue

    # Graceful fallback to standard Helvetica if no TTF font is accessible
    _ACTIVE_NORMAL_FONT = "Helvetica"
    _ACTIVE_BOLD_FONT = "Helvetica-Bold"
    _FONTS_INITIALIZED = True
    return _ACTIVE_NORMAL_FONT, _ACTIVE_BOLD_FONT


def get_currency_symbol(currency: str | None) -> str:
    """
    Returns the visual currency symbol for a currency code.
    Defaults to the ISO code if symbol is not mapped.
    """
    if not currency:
        return "₹"
    curr_clean = str(currency).strip().upper()
    return CURRENCY_SYMBOLS.get(curr_clean, curr_clean)


def format_currency_amount(amount: Decimal | float | int | None, currency: str | None = "INR") -> str:
    """
    Reusable currency formatting helper that formats monetary values with two-decimal precision
    and the appropriate currency symbol based on the invoice's stored currency.
    """
    if amount is None:
        dec_amount = Decimal("0.00")
    elif isinstance(amount, Decimal):
        dec_amount = amount
    else:
        dec_amount = Decimal(str(amount))

    symbol = get_currency_symbol(currency)
    is_negative = dec_amount < Decimal("0.00")
    abs_amount = abs(dec_amount)

    if is_negative:
        return f"-{symbol} {abs_amount:.2f}"
    return f"{symbol} {abs_amount:.2f}"


class InvoicePDFService:
    """
    Dedicated domain service responsible for generating downloadable,
    presentation-grade PDF statements for Invoices.
    """

    CURRENCY_SYMBOLS = CURRENCY_SYMBOLS
    format_currency = staticmethod(format_currency_amount)
    get_symbol = staticmethod(get_currency_symbol)

    @staticmethod
    def generate_invoice_pdf(invoice: Invoice) -> bytes:
        """
        Renders an Invoice and its itemized line charges into binary PDF bytes.
        """
        normal_font, bold_font = _ensure_unicode_fonts()

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            pageCompression=0,
            leftMargin=0.5 * inch,
            rightMargin=0.5 * inch,
            topMargin=0.5 * inch,
            bottomMargin=0.5 * inch,
        )

        styles = getSampleStyleSheet()

        # Custom typography styles utilizing registered Unicode font family
        brand_title = ParagraphStyle(
            "BrandTitle",
            parent=styles["Normal"],
            fontName=bold_font,
            fontSize=22,
            leading=26,
            textColor=colors.HexColor("#1E3A8A"),  # Deep Navy
        )
        brand_subtitle = ParagraphStyle(
            "BrandSubtitle",
            parent=styles["Normal"],
            fontName=normal_font,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#64748B"),  # Slate gray
        )
        invoice_heading = ParagraphStyle(
            "InvoiceHeading",
            parent=styles["Normal"],
            fontName=bold_font,
            fontSize=24,
            leading=28,
            alignment=2,  # Right-aligned
            textColor=colors.HexColor("#0F172A"),
        )
        invoice_number_style = ParagraphStyle(
            "InvoiceNumber",
            parent=styles["Normal"],
            fontName=bold_font,
            fontSize=11,
            leading=14,
            alignment=2,
            textColor=colors.HexColor("#2563EB"),  # Royal Blue
        )
        section_label = ParagraphStyle(
            "SectionLabel",
            parent=styles["Normal"],
            fontName=bold_font,
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#475569"),
        )
        body_text = ParagraphStyle(
            "BodyText",
            parent=styles["Normal"],
            fontName=normal_font,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#1E293B"),
        )
        body_bold = ParagraphStyle(
            "BodyBold",
            parent=styles["Normal"],
            fontName=bold_font,
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#0F172A"),
        )
        cell_header = ParagraphStyle(
            "CellHeader",
            parent=styles["Normal"],
            fontName=bold_font,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#1E293B"),
        )
        cell_text = ParagraphStyle(
            "CellText",
            parent=styles["Normal"],
            fontName=normal_font,
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#334155"),
        )
        cell_right = ParagraphStyle(
            "CellRight",
            parent=styles["Normal"],
            fontName=normal_font,
            fontSize=9,
            leading=12,
            alignment=2,
            textColor=colors.HexColor("#0F172A"),
        )
        cell_bold_right = ParagraphStyle(
            "CellBoldRight",
            parent=styles["Normal"],
            fontName=bold_font,
            fontSize=9,
            leading=12,
            alignment=2,
            textColor=colors.HexColor("#0F172A"),
        )
        footer_text = ParagraphStyle(
            "FooterText",
            parent=styles["Normal"],
            fontName=normal_font,
            fontSize=8,
            leading=11,
            alignment=1,  # Center
            textColor=colors.HexColor("#94A3B8"),
        )

        elements = []

        # -------------------------------------------------------------------------
        # 1. Header (Brand vs Invoice Title & Status)
        # -------------------------------------------------------------------------
        status_str = invoice.status.value.upper() if invoice.status else "OPEN"
        status_color = "#16A34A" if status_str == "PAID" else ("#D97706" if status_str == "OPEN" else "#DC2626")

        header_data = [
            [
                Paragraph("<b>AutoSub</b>", brand_title),
                Paragraph("<b>INVOICE</b>", invoice_heading),
            ],
            [
                Paragraph("Recurring Revenue & Billing Automation", brand_subtitle),
                Paragraph(f"<b>#{invoice.invoice_number}</b>", invoice_number_style),
            ],
            [
                Paragraph("support@autosub.io | billing.autosub.io", brand_subtitle),
                Paragraph(
                    f'<font color="{status_color}"><b>STATUS: {status_str}</b></font>',
                    ParagraphStyle("StatusStyle", parent=invoice_number_style, fontSize=10, fontName=bold_font),
                ),
            ],
        ]

        header_table = Table(header_data, colWidths=[3.75 * inch, 3.75 * inch])
        header_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
            ])
        )
        elements.append(header_table)
        elements.append(Spacer(1, 14))

        # Thin divider
        elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0"), spaceAfter=14))

        # -------------------------------------------------------------------------
        # 2. Customer & Invoice Meta Details (2 Columns)
        # -------------------------------------------------------------------------
        customer_name = invoice.customer_name or (invoice.customer.full_name if invoice.customer else "Valued Customer")
        customer_email = invoice.customer_email or (invoice.customer.email if invoice.customer else "N/A")
        customer_code = invoice.customer.customer_code if invoice.customer else f"CUST-#{invoice.customer_id}"

        sub_code = (
            invoice.subscription.subscription_code
            if invoice.subscription
            else (f"SUB-#{invoice.subscription_id}" if invoice.subscription_id else "Standalone Charge")
        )
        plan_name = invoice.plan_name or (
            invoice.subscription.plan.name
            if (invoice.subscription and invoice.subscription.plan)
            else "Standard Subscription"
        )
        plan_code = invoice.plan_code or (
            invoice.subscription.plan.code
            if (invoice.subscription and invoice.subscription.plan)
            else "N/A"
        )

        issue_date_str = invoice.issue_date.strftime("%B %d, %Y") if invoice.issue_date else "N/A"
        due_date_str = invoice.due_date.strftime("%B %d, %Y") if invoice.due_date else "N/A"
        curr_symbol = get_currency_symbol(invoice.currency)

        bill_to_paragraphs = [
            Paragraph("<b>BILLED TO</b>", section_label),
            Spacer(1, 4),
            Paragraph(f"<b>{customer_name}</b>", body_bold),
            Paragraph(f"Email: {customer_email}", body_text),
            Paragraph(f"Account Code: <code>{customer_code}</code>", body_text),
        ]

        invoice_meta_paragraphs = [
            Paragraph("<b>INVOICE DETAILS</b>", section_label),
            Spacer(1, 4),
            Paragraph(f"<b>Issue Date:</b> {issue_date_str}", body_text),
            Paragraph(f"<b>Due Date:</b> {due_date_str}", body_text),
            Paragraph(f"<b>Plan:</b> {plan_name} ({plan_code})", body_text),
            Paragraph(f"<b>Subscription Code:</b> <code>{sub_code}</code>", body_text),
            Paragraph(f"<b>Currency:</b> {invoice.currency} ({curr_symbol})", body_text),
        ]

        meta_table = Table(
            [[bill_to_paragraphs, invoice_meta_paragraphs]],
            colWidths=[3.75 * inch, 3.75 * inch],
        )
        meta_table.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ])
        )
        elements.append(meta_table)
        elements.append(Spacer(1, 16))

        # -------------------------------------------------------------------------
        # 3. Itemized Charges Table
        # -------------------------------------------------------------------------
        item_table_data: List[List[Paragraph]] = [
            [
                Paragraph("<b>Item & Description</b>", cell_header),
                Paragraph("<b>Type</b>", cell_header),
                Paragraph("<b>Qty</b>", cell_header),
                Paragraph("<b>Unit Price</b>", ParagraphStyle("RightHdr", parent=cell_header, alignment=2, fontName=bold_font)),
                Paragraph("<b>Amount</b>", ParagraphStyle("RightHdr2", parent=cell_header, alignment=2, fontName=bold_font)),
            ]
        ]

        items = invoice.items or []
        if not items:
            # Fallback row if no child line items exist
            item_table_data.append([
                Paragraph(f"{plan_name} Recurring Charge", cell_text),
                Paragraph("base_subscription", cell_text),
                Paragraph("1", cell_text),
                Paragraph(format_currency_amount(invoice.subtotal, invoice.currency), cell_right),
                Paragraph(format_currency_amount(invoice.subtotal, invoice.currency), cell_bold_right),
            ])
        else:
            for itm in items:
                type_label = itm.item_type.value if hasattr(itm.item_type, "value") else str(itm.item_type)
                item_table_data.append([
                    Paragraph(itm.description, cell_text),
                    Paragraph(f"<code>{type_label}</code>", cell_text),
                    Paragraph(str(itm.quantity), cell_text),
                    Paragraph(format_currency_amount(itm.unit_price, invoice.currency), cell_right),
                    Paragraph(format_currency_amount(itm.amount, invoice.currency), cell_bold_right),
                ])

        items_table = Table(
            item_table_data,
            colWidths=[3.3 * inch, 1.4 * inch, 0.5 * inch, 1.1 * inch, 1.2 * inch],
        )
        items_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.HexColor("#CBD5E1")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ])
        )
        elements.append(items_table)
        elements.append(Spacer(1, 14))

        # -------------------------------------------------------------------------
        # 4. Financial Summary Breakdown (Right Aligned)
        # -------------------------------------------------------------------------
        subtotal_val = invoice.subtotal
        tax_val = invoice.tax_amount
        discount_val = invoice.discount_amount
        total_val = invoice.total_amount
        paid_val = invoice.amount_paid
        balance_val = max(Decimal("0.00"), total_val - paid_val)

        totals_data = [
            [
                Paragraph("<b>Subtotal:</b>", cell_header),
                Paragraph(format_currency_amount(subtotal_val, invoice.currency), cell_bold_right),
            ],
        ]
        if discount_val > Decimal("0.00"):
            totals_data.append([
                Paragraph("<b>Discount / Credits:</b>", cell_header),
                Paragraph(format_currency_amount(-discount_val, invoice.currency), cell_bold_right),
            ])
        has_gst_item = any("GST" in (itm.description or "") for itm in items)
        if tax_val > Decimal("0.00") or has_gst_item:
            tax_label = "<b>GST (18%):</b>"
        else:
            tax_label = "<b>Tax:</b>"

        totals_data.extend([
            [
                Paragraph(tax_label, cell_header),
                Paragraph(format_currency_amount(tax_val, invoice.currency), cell_bold_right),
            ],
            [
                Paragraph("<b>Total Amount:</b>", ParagraphStyle("BigTot", parent=cell_header, fontSize=10, fontName=bold_font)),
                Paragraph(
                    f"<b>{format_currency_amount(total_val, invoice.currency)}</b>",
                    ParagraphStyle("BigTotVal", parent=cell_bold_right, fontSize=10, fontName=bold_font, textColor=colors.HexColor("#2563EB")),
                ),
            ],
            [
                Paragraph("<b>Amount Paid:</b>", cell_header),
                Paragraph(format_currency_amount(paid_val, invoice.currency), cell_bold_right),
            ],
            [
                Paragraph("<b>Outstanding Balance:</b>", ParagraphStyle("BalTot", parent=cell_header, fontSize=10, fontName=bold_font)),
                Paragraph(
                    f"<b>{format_currency_amount(balance_val, invoice.currency)}</b>",
                    ParagraphStyle(
                        "BalTotVal",
                        parent=cell_bold_right,
                        fontSize=10,
                        fontName=bold_font,
                        textColor=colors.HexColor("#16A34A" if balance_val == Decimal("0.00") else "#DC2626"),
                    ),
                ),
            ],
        ])

        totals_table = Table(totals_data, colWidths=[2.0 * inch, 1.6 * inch])
        totals_table.setStyle(
            TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEBELOW", (0, 2), (-1, 2), 0.5, colors.HexColor("#E2E8F0")),
                ("BACKGROUND", (0, 3), (-1, 3), colors.HexColor("#F8FAFC")),
                ("LINEBELOW", (0, -1), (-1, -1), 1, colors.HexColor("#CBD5E1")),
            ])
        )

        summary_container = Table(
            [[Paragraph("", body_text), totals_table]],
            colWidths=[3.9 * inch, 3.6 * inch],
        )
        summary_container.setStyle(
            TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ])
        )
        elements.append(KeepTogether(summary_container))
        elements.append(Spacer(1, 20))

        # -------------------------------------------------------------------------
        # 5. Notes & Terms
        # -------------------------------------------------------------------------
        if invoice.notes:
            notes_data = [
                [Paragraph("<b>Notes & Payment Instructions:</b>", section_label)],
                [Paragraph(invoice.notes, body_text)],
            ]
            notes_table = Table(notes_data, colWidths=[7.5 * inch])
            notes_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ])
            )
            elements.append(notes_table)
            elements.append(Spacer(1, 16))

        # -------------------------------------------------------------------------
        # 6. Professional Footer
        # -------------------------------------------------------------------------
        elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E2E8F0"), spaceAfter=8))
        elements.append(
            Paragraph(
                "Thank you for choosing AutoSub. This is a computer-generated tax invoice. "
                "For inquiries, please contact billing@autosub.io.",
                footer_text,
            )
        )
        elements.append(
            Paragraph(
                "AutoSub Systems Inc. • 100 Revenue Way, Suite 400 • San Francisco, CA 94105",
                footer_text,
            )
        )

        doc.build(elements)
        buffer.seek(0)
        return buffer.getvalue()
