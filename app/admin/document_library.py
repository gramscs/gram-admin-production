"""Read shipment document details from the original shipment records."""
from flask import render_template, request

from app import orm as models
from app.admin import admin_bp
from app.admin.auth import require_admin
from app.admin.reporting import normalize_shipment_filters


@admin_bp.get('/admin/documents')
@require_admin
def documents_panel():
    try:
        filters = normalize_shipment_filters(request.args)
        companies = models.Company.query.order_by(models.Company.name).all()
        return render_template('admin/document_library.html', filters=filters, companies=companies)
    except ValueError as error:
        return render_template('admin/document_library.html', filters={}, companies=[], error=str(error)), 400
