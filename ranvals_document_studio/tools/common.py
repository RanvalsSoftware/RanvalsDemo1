import re
import unicodedata
from datetime import date, datetime

from odoo.tools import html2plaintext
from odoo.tools.misc import format_date


def check_record_access(records, operation="read"):
    """Use the unified Odoo 18/19 ACL API with an Odoo 17 fallback."""
    unified_check = getattr(records, "check_access", None)
    if unified_check:
        return unified_check(operation)
    records.check_access_rights(operation)
    records.check_access_rule(operation)
    return True


LABELS = {
    "tr": {
        "quote": "TEKLİF",
        "order": "SİPARİŞ",
        "proforma": "PROFORMA FATURA",
        "invoice": "FATURA",
        "refund": "İADE FATURASI",
        "purchase_quote": "SATIN ALMA TEKLİFİ",
        "purchase_order": "SATIN ALMA SİPARİŞİ",
        "company_info": "FİRMA BİLGİLERİ",
        "customer_info": "MÜŞTERİ BİLGİLERİ",
        "vendor_info": "TEDARİKÇİ BİLGİLERİ",
        "document_info": "DOKÜMAN BİLGİLERİ",
        "document_no": "BELGE NO",
        "date": "TARİH",
        "due_date": "VADE TARİHİ",
        "validity": "GEÇERLİLİK",
        "salesperson": "SATIŞ TEMSİLCİSİ",
        "buyer": "SATIN ALMA SORUMLUSU",
        "reference": "REFERANS",
        "delivery_date": "TESLİM TARİHİ",
        "service_period": "HİZMET DÖNEMİ",
        "payment_terms": "ÖDEME KOŞULU",
        "description": "AÇIKLAMA",
        "code": "KOD",
        "quantity": "MİKTAR",
        "unit_price": "BİRİM FİYAT",
        "discount": "İNDİRİM",
        "tax": "VERGİ",
        "amount": "TUTAR",
        "subtotal": "ARA TOPLAM",
        "tax_total": "KDV / VERGİ",
        "grand_total": "GENEL TOPLAM",
        "notes": "NOTLAR / ŞARTLAR",
        "bank_info": "BANKA BİLGİLERİ",
        "bank": "Banka",
        "branch": "Şube",
        "account_name": "Hesap Adı",
        "iban": "IBAN",
        "swift": "SWIFT / BIC",
        "phone": "Telefon",
        "email": "E-posta",
        "website": "Web Sitesi",
        "tax_no": "Vergi No",
        "thank_you": "Bizi tercih ettiğiniz için teşekkür ederiz.",
    },
    "en": {
        "quote": "QUOTATION",
        "order": "SALES ORDER",
        "proforma": "PRO-FORMA INVOICE",
        "invoice": "INVOICE",
        "refund": "CREDIT NOTE",
        "purchase_quote": "REQUEST FOR QUOTATION",
        "purchase_order": "PURCHASE ORDER",
        "company_info": "COMPANY INFORMATION",
        "customer_info": "CUSTOMER INFORMATION",
        "vendor_info": "VENDOR INFORMATION",
        "document_info": "DOCUMENT INFORMATION",
        "document_no": "DOCUMENT NO",
        "date": "DATE",
        "due_date": "DUE DATE",
        "validity": "VALIDITY",
        "salesperson": "SALESPERSON",
        "buyer": "BUYER",
        "reference": "REFERENCE",
        "delivery_date": "DELIVERY DATE",
        "service_period": "SERVICE PERIOD",
        "payment_terms": "PAYMENT TERMS",
        "description": "DESCRIPTION",
        "code": "CODE",
        "quantity": "QUANTITY",
        "unit_price": "UNIT PRICE",
        "discount": "DISCOUNT",
        "tax": "TAX",
        "amount": "AMOUNT",
        "subtotal": "SUBTOTAL",
        "tax_total": "TAX",
        "grand_total": "GRAND TOTAL",
        "notes": "NOTES / TERMS",
        "bank_info": "BANK INFORMATION",
        "bank": "Bank",
        "branch": "Branch",
        "account_name": "Account Name",
        "iban": "IBAN",
        "swift": "SWIFT / BIC",
        "phone": "Phone",
        "email": "Email",
        "website": "Website",
        "tax_no": "Tax ID",
        "thank_you": "Thank you for choosing us.",
    },
    "de": {
        "quote": "ANGEBOT", "order": "AUFTRAG", "proforma": "PRO-FORMA-RECHNUNG", "invoice": "RECHNUNG", "refund": "GUTSCHRIFT",
        "purchase_quote": "PREISANFRAGE", "purchase_order": "BESTELLUNG",
        "company_info": "FIRMENINFORMATIONEN", "customer_info": "KUNDENINFORMATIONEN",
        "vendor_info": "LIEFERANTENINFORMATIONEN", "document_info": "DOKUMENTINFORMATIONEN",
        "document_no": "DOKUMENT-NR.", "date": "DATUM", "due_date": "FÄLLIGKEITSDATUM",
        "validity": "GÜLTIGKEIT", "salesperson": "VERTRIEB", "buyer": "EINKÄUFER",
        "reference": "REFERENZ", "delivery_date": "LIEFERDATUM", "service_period": "LEISTUNGSZEITRAUM",
        "payment_terms": "ZAHLUNGSBEDINGUNGEN", "description": "BESCHREIBUNG", "code": "CODE",
        "quantity": "MENGE", "unit_price": "EINZELPREIS", "discount": "RABATT", "tax": "STEUER",
        "amount": "BETRAG", "subtotal": "ZWISCHENSUMME", "tax_total": "STEUER",
        "grand_total": "GESAMTSUMME", "notes": "HINWEISE / BEDINGUNGEN", "bank_info": "BANKVERBINDUNG",
        "bank": "Bank", "branch": "Filiale", "account_name": "Kontoinhaber", "iban": "IBAN",
        "swift": "SWIFT / BIC", "phone": "Telefon", "email": "E-Mail", "website": "Webseite",
        "tax_no": "Steuernummer", "thank_you": "Vielen Dank für Ihr Vertrauen.",
    },
    "fr": {
        "quote": "DEVIS", "order": "COMMANDE", "proforma": "FACTURE PRO FORMA", "invoice": "FACTURE", "refund": "AVOIR",
        "purchase_quote": "DEMANDE DE PRIX", "purchase_order": "BON DE COMMANDE",
        "company_info": "INFORMATIONS SOCIÉTÉ", "customer_info": "INFORMATIONS CLIENT",
        "vendor_info": "INFORMATIONS FOURNISSEUR", "document_info": "INFORMATIONS DOCUMENT",
        "document_no": "N° DOCUMENT", "date": "DATE", "due_date": "DATE D'ÉCHÉANCE",
        "validity": "VALIDITÉ", "salesperson": "COMMERCIAL", "buyer": "ACHETEUR",
        "reference": "RÉFÉRENCE", "delivery_date": "DATE DE LIVRAISON", "service_period": "PÉRIODE DE SERVICE",
        "payment_terms": "CONDITIONS DE PAIEMENT", "description": "DESCRIPTION", "code": "CODE",
        "quantity": "QUANTITÉ", "unit_price": "PRIX UNITAIRE", "discount": "REMISE", "tax": "TAXE",
        "amount": "MONTANT", "subtotal": "SOUS-TOTAL", "tax_total": "TAXES",
        "grand_total": "TOTAL GÉNÉRAL", "notes": "NOTES / CONDITIONS", "bank_info": "INFORMATIONS BANCAIRES",
        "bank": "Banque", "branch": "Agence", "account_name": "Titulaire du compte", "iban": "IBAN",
        "swift": "SWIFT / BIC", "phone": "Téléphone", "email": "E-mail", "website": "Site web",
        "tax_no": "N° fiscal", "thank_you": "Merci de votre confiance.",
    },
    "es": {
        "quote": "OFERTA", "order": "PEDIDO", "proforma": "FACTURA PROFORMA", "invoice": "FACTURA", "refund": "NOTA DE CRÉDITO",
        "purchase_quote": "SOLICITUD DE COTIZACIÓN", "purchase_order": "ORDEN DE COMPRA",
        "company_info": "INFORMACIÓN DE LA EMPRESA", "customer_info": "INFORMACIÓN DEL CLIENTE",
        "vendor_info": "INFORMACIÓN DEL PROVEEDOR", "document_info": "INFORMACIÓN DEL DOCUMENTO",
        "document_no": "N.º DOCUMENTO", "date": "FECHA", "due_date": "FECHA DE VENCIMIENTO",
        "validity": "VALIDEZ", "salesperson": "VENDEDOR", "buyer": "COMPRADOR",
        "reference": "REFERENCIA", "delivery_date": "FECHA DE ENTREGA", "service_period": "PERÍODO DE SERVICIO",
        "payment_terms": "CONDICIONES DE PAGO", "description": "DESCRIPCIÓN", "code": "CÓDIGO",
        "quantity": "CANTIDAD", "unit_price": "PRECIO UNITARIO", "discount": "DESCUENTO", "tax": "IMPUESTO",
        "amount": "IMPORTE", "subtotal": "SUBTOTAL", "tax_total": "IMPUESTOS",
        "grand_total": "TOTAL GENERAL", "notes": "NOTAS / CONDICIONES", "bank_info": "INFORMACIÓN BANCARIA",
        "bank": "Banco", "branch": "Sucursal", "account_name": "Titular", "iban": "IBAN",
        "swift": "SWIFT / BIC", "phone": "Teléfono", "email": "Correo", "website": "Sitio web",
        "tax_no": "NIF", "thank_you": "Gracias por su confianza.",
    },
    "it": {
        "quote": "OFFERTA", "order": "ORDINE", "proforma": "FATTURA PROFORMA", "invoice": "FATTURA", "refund": "NOTA DI CREDITO",
        "purchase_quote": "RICHIESTA DI OFFERTA", "purchase_order": "ORDINE DI ACQUISTO",
        "company_info": "INFORMAZIONI AZIENDA", "customer_info": "INFORMAZIONI CLIENTE",
        "vendor_info": "INFORMAZIONI FORNITORE", "document_info": "INFORMAZIONI DOCUMENTO",
        "document_no": "N. DOCUMENTO", "date": "DATA", "due_date": "SCADENZA",
        "validity": "VALIDITÀ", "salesperson": "COMMERCIALE", "buyer": "ACQUIRENTE",
        "reference": "RIFERIMENTO", "delivery_date": "DATA DI CONSEGNA", "service_period": "PERIODO DI SERVIZIO",
        "payment_terms": "CONDIZIONI DI PAGAMENTO", "description": "DESCRIZIONE", "code": "CODICE",
        "quantity": "QUANTITÀ", "unit_price": "PREZZO UNITARIO", "discount": "SCONTO", "tax": "IMPOSTA",
        "amount": "IMPORTO", "subtotal": "SUBTOTALE", "tax_total": "IMPOSTE",
        "grand_total": "TOTALE GENERALE", "notes": "NOTE / CONDIZIONI", "bank_info": "DATI BANCARI",
        "bank": "Banca", "branch": "Filiale", "account_name": "Intestatario", "iban": "IBAN",
        "swift": "SWIFT / BIC", "phone": "Telefono", "email": "E-mail", "website": "Sito web",
        "tax_no": "Partita IVA", "thank_you": "Grazie per la fiducia.",
    },
    "pt": {
        "quote": "PROPOSTA", "order": "PEDIDO", "proforma": "FATURA PROFORMA", "invoice": "FATURA", "refund": "NOTA DE CRÉDITO",
        "purchase_quote": "PEDIDO DE COTAÇÃO", "purchase_order": "ORDEM DE COMPRA",
        "company_info": "INFORMAÇÕES DA EMPRESA", "customer_info": "INFORMAÇÕES DO CLIENTE",
        "vendor_info": "INFORMAÇÕES DO FORNECEDOR", "document_info": "INFORMAÇÕES DO DOCUMENTO",
        "document_no": "N.º DOCUMENTO", "date": "DATA", "due_date": "VENCIMENTO",
        "validity": "VALIDADE", "salesperson": "VENDEDOR", "buyer": "COMPRADOR",
        "reference": "REFERÊNCIA", "delivery_date": "DATA DE ENTREGA", "service_period": "PERÍODO DE SERVIÇO",
        "payment_terms": "CONDIÇÕES DE PAGAMENTO", "description": "DESCRIÇÃO", "code": "CÓDIGO",
        "quantity": "QUANTIDADE", "unit_price": "PREÇO UNITÁRIO", "discount": "DESCONTO", "tax": "IMPOSTO",
        "amount": "VALOR", "subtotal": "SUBTOTAL", "tax_total": "IMPOSTOS",
        "grand_total": "TOTAL GERAL", "notes": "NOTAS / CONDIÇÕES", "bank_info": "INFORMAÇÕES BANCÁRIAS",
        "bank": "Banco", "branch": "Agência", "account_name": "Titular", "iban": "IBAN",
        "swift": "SWIFT / BIC", "phone": "Telefone", "email": "E-mail", "website": "Site",
        "tax_no": "NIF", "thank_you": "Obrigado pela confiança.",
    },
    "ru": {
        "quote": "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ", "order": "ЗАКАЗ", "proforma": "СЧЕТ-ПРОФОРМА", "invoice": "СЧЕТ", "refund": "КРЕДИТ-НОТА",
        "purchase_quote": "ЗАПРОС ЦЕНЫ", "purchase_order": "ЗАКАЗ НА ПОКУПКУ",
        "company_info": "ИНФОРМАЦИЯ О КОМПАНИИ", "customer_info": "ИНФОРМАЦИЯ О КЛИЕНТЕ",
        "vendor_info": "ИНФОРМАЦИЯ О ПОСТАВЩИКЕ", "document_info": "ИНФОРМАЦИЯ О ДОКУМЕНТЕ",
        "document_no": "НОМЕР ДОКУМЕНТА", "date": "ДАТА", "due_date": "СРОК ОПЛАТЫ",
        "validity": "СРОК ДЕЙСТВИЯ", "salesperson": "МЕНЕДЖЕР", "buyer": "ЗАКУПЩИК",
        "reference": "ССЫЛКА", "delivery_date": "ДАТА ПОСТАВКИ", "service_period": "ПЕРИОД УСЛУГ",
        "payment_terms": "УСЛОВИЯ ОПЛАТЫ", "description": "ОПИСАНИЕ", "code": "КОД",
        "quantity": "КОЛИЧЕСТВО", "unit_price": "ЦЕНА", "discount": "СКИДКА", "tax": "НАЛОГ",
        "amount": "СУММА", "subtotal": "ПОДЫТОГ", "tax_total": "НАЛОГИ",
        "grand_total": "ИТОГО", "notes": "ПРИМЕЧАНИЯ / УСЛОВИЯ", "bank_info": "БАНКОВСКИЕ РЕКВИЗИТЫ",
        "bank": "Банк", "branch": "Филиал", "account_name": "Получатель", "iban": "IBAN",
        "swift": "SWIFT / BIC", "phone": "Телефон", "email": "Эл. почта", "website": "Веб-сайт",
        "tax_no": "ИНН", "thank_you": "Благодарим за доверие.",
    },
    "ar": {
        "quote": "عرض سعر", "order": "طلب", "proforma": "فاتورة أولية", "invoice": "فاتورة", "refund": "إشعار دائن",
        "purchase_quote": "طلب عرض سعر", "purchase_order": "أمر شراء",
        "company_info": "معلومات الشركة", "customer_info": "معلومات العميل",
        "vendor_info": "معلومات المورد", "document_info": "معلومات المستند",
        "document_no": "رقم المستند", "date": "التاريخ", "due_date": "تاريخ الاستحقاق",
        "validity": "الصلاحية", "salesperson": "مندوب المبيعات", "buyer": "مسؤول المشتريات",
        "reference": "المرجع", "delivery_date": "تاريخ التسليم", "service_period": "فترة الخدمة",
        "payment_terms": "شروط الدفع", "description": "الوصف", "code": "الرمز",
        "quantity": "الكمية", "unit_price": "سعر الوحدة", "discount": "الخصم", "tax": "الضريبة",
        "amount": "المبلغ", "subtotal": "المجموع الفرعي", "tax_total": "الضرائب",
        "grand_total": "الإجمالي", "notes": "الملاحظات / الشروط", "bank_info": "المعلومات البنكية",
        "bank": "البنك", "branch": "الفرع", "account_name": "اسم الحساب", "iban": "IBAN",
        "swift": "SWIFT / BIC", "phone": "الهاتف", "email": "البريد", "website": "الموقع",
        "tax_no": "الرقم الضريبي", "thank_you": "شكراً لثقتكم بنا.",
    },
}


def lang_code(lang):
    value = (lang or "tr_TR").lower().replace("-", "_")
    code = value.split("_")[0]
    return code if code in LABELS else "en"


def tr_label(key, lang=None):
    code = lang_code(lang)
    return LABELS.get(code, LABELS["en"]).get(key, LABELS["en"].get(key, key))


def safe_filename(value, default="document"):
    value = unicodedata.normalize("NFKC", str(value or default)).strip()
    value = re.sub(r"[\\/:*?\"<>|]+", "-", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^\w\-.]+", "_", value, flags=re.UNICODE)
    value = value.strip("._-")
    value = (value or default)[:120]

    # Windows treats these basenames as devices even when an extension is
    # present.  Prefixing them also makes archives created on Linux safe to
    # extract on Windows clients.
    stem = value.split(".", 1)[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update("COM%s" % index for index in range(1, 10))
    reserved.update("LPT%s" % index for index in range(1, 10))
    if stem in reserved:
        value = "_%s" % value
    return value[:120]


def normalize_hex(value, fallback="#17345F"):
    value = (value or "").strip().upper()
    if re.fullmatch(r"#[0-9A-F]{6}", value):
        return value
    return fallback


def hex_to_rgb(value, fallback="#17345F"):
    value = normalize_hex(value, fallback).lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def plain_text(value):
    # Numeric zero is meaningful in quantities, discounts and totals; Python's
    # truthiness must not turn it into an empty document cell.
    if value is None or value is False:
        return ""
    if isinstance(value, str):
        return html2plaintext(value).strip()
    return str(value)


def format_date_value(value, lang=None, env=None):
    if not value:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return str(value)
    if env is not None:
        return format_date(env, value, lang_code=lang)
    code = lang_code(lang)
    if code == "en":
        return value.strftime("%d %b %Y")
    if code in ("de", "fr", "es", "it", "pt", "ru"):
        return value.strftime("%d.%m.%Y")
    if code == "ar":
        return value.strftime("%Y/%m/%d")
    return value.strftime("%d.%m.%Y")
