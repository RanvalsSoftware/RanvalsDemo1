from io import BytesIO


class PngDependencyError(RuntimeError):
    pass


MAX_RENDER_DPI = 600


def render_png_pages(pdf_bytes, dpi=150, max_pages=50, max_output_bytes=None):
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes.startswith(b"%PDF"):
        raise PngDependencyError("PNG çıktısı için geçerli bir PDF belgesi oluşturulamadı.")
    try:
        dpi = int(dpi)
    except (TypeError, ValueError) as exc:
        raise PngDependencyError("PNG çözünürlüğü geçersiz.") from exc
    if dpi < 72 or dpi > MAX_RENDER_DPI:
        raise PngDependencyError(
            "PNG çözünürlüğü 72 ile %s DPI arasında olmalıdır." % MAX_RENDER_DPI
        )
    if max_output_bytes is not None and (
        not isinstance(max_output_bytes, int) or max_output_bytes <= 0
    ):
        raise PngDependencyError("PNG çıktı boyutu sınırı geçersiz.")
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - depends on Odoo.sh requirements
        raise PngDependencyError(
            "PNG çıktısı için pypdfium2 paketi kurulmalıdır. requirements.txt dosyasını Odoo.sh köküne ekleyin."
        ) from exc

    try:
        from PIL import Image  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise PngDependencyError("PNG çıktısı için Pillow paketi kurulmalıdır.") from exc

    scale = dpi / 72.0
    result = []
    output_size = 0
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:
        raise PngDependencyError("PDF belgesi okunamadı veya geçersiz.") from exc
    try:
        page_count = len(pdf)
        if max_pages and page_count > max_pages:
            raise PngDependencyError(
                "PNG çıktısında PDF en fazla %s sayfa olabilir; belge %s sayfa." % (max_pages, page_count)
            )
        try:
            for index in range(page_count):
                page = pdf[index]
                bitmap = None
                image = None
                try:
                    bitmap = page.render(scale=scale)
                    image = bitmap.to_pil()
                    stream = BytesIO()
                    image.save(stream, format="PNG", optimize=True, dpi=(dpi, dpi))
                    page_content = stream.getvalue()
                    output_size += len(page_content)
                    if max_output_bytes is not None and output_size > max_output_bytes:
                        raise PngDependencyError(
                            "PNG çıktısı izin verilen toplam dosya boyutunu aşıyor."
                        )
                    result.append(page_content)
                finally:
                    if image is not None:
                        image.close()
                    if bitmap is not None and hasattr(bitmap, "close"):
                        bitmap.close()
                    if hasattr(page, "close"):
                        page.close()
        except PngDependencyError:
            raise
        except Exception as exc:
            raise PngDependencyError("PDF sayfaları PNG biçimine dönüştürülemedi.") from exc
    finally:
        if hasattr(pdf, "close"):
            pdf.close()
    return result
