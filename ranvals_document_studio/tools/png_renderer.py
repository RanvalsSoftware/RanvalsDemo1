from io import BytesIO

from odoo import _


class PngDependencyError(RuntimeError):
    pass


MAX_RENDER_DPI = 600


def render_png_pages(pdf_bytes, dpi=150, max_pages=50, max_output_bytes=None):
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes.startswith(b"%PDF"):
        raise PngDependencyError(_("A valid PDF document could not be created for PNG output."))
    try:
        dpi = int(dpi)
    except (TypeError, ValueError) as exc:
        raise PngDependencyError(_("The PNG resolution is invalid.")) from exc
    if dpi < 72 or dpi > MAX_RENDER_DPI:
        raise PngDependencyError(
            _("The PNG resolution must be between 72 and %s DPI.") % MAX_RENDER_DPI
        )
    if max_output_bytes is not None and (
        not isinstance(max_output_bytes, int) or max_output_bytes <= 0
    ):
        raise PngDependencyError(_("The PNG output size limit is invalid."))
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - depends on Odoo.sh requirements
        raise PngDependencyError(
            _("The pypdfium2 package is required for PNG output. Add requirements.txt to the Odoo.sh root.")
        ) from exc

    try:
        from PIL import Image  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise PngDependencyError(_("The Pillow package is required for PNG output.")) from exc

    scale = dpi / 72.0
    result = []
    output_size = 0
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:
        raise PngDependencyError(_("The PDF document could not be read or is invalid.")) from exc
    try:
        page_count = len(pdf)
        if max_pages and page_count > max_pages:
            raise PngDependencyError(
                _("A PDF can contain at most %s pages for PNG output; the document has %s pages.") % (max_pages, page_count)
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
                            _("The PNG output exceeds the permitted total file size.")
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
            raise PngDependencyError(_("The PDF pages could not be converted to PNG format.")) from exc
    finally:
        if hasattr(pdf, "close"):
            pdf.close()
    return result
