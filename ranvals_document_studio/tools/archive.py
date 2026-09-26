import zipfile
from io import BytesIO
from pathlib import PurePosixPath


def _safe_archive_member_name(value):
    if not isinstance(value, str) or not value or len(value) > 255 or "\x00" in value:
        raise ValueError("Invalid ZIP member name")
    if "\\" in value or ":" in value:
        raise ValueError("Invalid ZIP member name")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or path.name in ("", ".", ".."):
        raise ValueError("Invalid ZIP member name")
    return path.name


def build_zip_archive(files):
    """Build a deterministic, in-memory ZIP from ``(name, bytes)`` pairs."""
    stream = BytesIO()
    used_names = set()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_name, content in files:
            file_name = _safe_archive_member_name(file_name)
            if file_name in used_names:
                raise ValueError("Duplicate ZIP member name")
            if not isinstance(content, (bytes, bytearray)):
                raise TypeError("ZIP member content must be bytes")
            used_names.add(file_name)
            info = zipfile.ZipInfo(file_name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, bytes(content))
    return stream.getvalue()
