"""Keep uploaded file bodies in RAM; document persistence belongs to Supabase."""
from io import BytesIO

from flask import Request


class MemoryUploadRequest(Request):
    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        # Flask's default multipart stream may spool large uploads to local disk.
        # Existing request/file size limits still bound the accepted uploads.
        return BytesIO()
