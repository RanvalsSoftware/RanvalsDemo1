"""Language metadata for generated DOCX; content and numeric values stay intact."""
from io import BytesIO
import re
import unicodedata
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree

LOCALES = {"tr_TR": "tr-TR", "en_GB": "en-GB", "en_US": "en-US", "ru_RU": "ru-RU",
           "fr_FR": "fr-FR", "it_IT": "it-IT", "ar_001": "ar-SA"}


def _has_rtl_letters(text):
    return any(unicodedata.bidirectional(char) in ("R", "AL") for char in text)


def localize_docx(content, code):
    locale = LOCALES.get(code)
    if not locale:
        return content
    # Loaded only after the DOCX renderer has successfully used python-docx.
    from docx.oxml import OxmlElement, parse_xml
    from docx.oxml.ns import qn

    rtl = code.startswith("ar")
    result = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(result, "w", ZIP_DEFLATED) as output:
        for item in source.infolist():
            payload = source.read(item.filename)
            if re.fullmatch(r"word/(document|header\d+|footer\d+)\.xml", item.filename):
                root = parse_xml(payload)
                for paragraph in root.iter(qn("w:p")):
                    text = "".join(node.text or "" for node in paragraph.iter(qn("w:t")))
                    if rtl and _has_rtl_letters(text):
                        ppr = paragraph.get_or_add_pPr()
                        bidi = ppr.find(qn("w:bidi"))
                        if bidi is None:
                            bidi = OxmlElement("w:bidi")
                            ppr.insert_element_before(bidi, "w:adjustRightInd", "w:snapToGrid", "w:spacing",
                                "w:ind", "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc",
                                "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl",
                                "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange")
                        bidi.set(qn("w:val"), "1")
                        jc = ppr.get_or_add_jc()
                        if jc.get(qn("w:val")) in (None, "left", "start"):
                            jc.set(qn("w:val"), "right")
                    for run in paragraph.iter(qn("w:r")):
                        rpr = run.get_or_add_rPr()
                        lang = rpr.find(qn("w:lang"))
                        if lang is None:
                            lang = OxmlElement("w:lang")
                            rpr.insert_element_before(lang, "w:eastAsianLayout", "w:specVanish", "w:oMath", "w:rPrChange")
                        lang.set(qn("w:val"), locale)
                        if rtl:
                            lang.set(qn("w:bidi"), locale)
                        run_text = "".join(node.text or "" for node in run.iter(qn("w:t")))
                        if rtl and _has_rtl_letters(run_text):
                            rpr.get_or_add_rtl().set(qn("w:val"), "1")
                            rpr.get_or_add_rFonts().set(qn("w:cs"), "Arial")
                payload = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
            output.writestr(item, payload)
    return result.getvalue()
