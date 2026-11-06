from types import SimpleNamespace
from uuid import uuid4

from flask import Flask

from app.admin import lr_control
from app import orm


class MockSession:
    def __init__(self, shipment):
        self.shipment = shipment
        self.flushed = False
        self.committed = False

    def get(self, model, identifier, with_for_update=False):
        return self.shipment

    def execute(self, statement, parameters=None):
        return None

    def flush(self):
        self.flushed = True

    def commit(self):
        self.committed = True


def test_mock_operator_can_cancel_shipment(monkeypatch):
    app = Flask(__name__)
    shipment = SimpleNamespace(
        archive_record=None,
        lr_status="issued",
        identifier_value="HQ000001",
    )
    session = MockSession(shipment)

    monkeypatch.setattr(lr_control.db, "session", session)
    monkeypatch.setattr(lr_control, "current_admin_user", lambda: SimpleNamespace(id=uuid4()))

    identifier = uuid4()
    with app.app_context(), app.test_request_context(
        f"/admin/consignments/{identifier}/cancel",
        method="POST",
        json={"confirmed": True, "reason": "Operator cancellation test"},
    ):
        sentinel = object()
        previous = orm.__dict__.get("Consignment", sentinel)
        orm.__dict__["Consignment"] = object
        try:
            response = lr_control.cancel_lr.__wrapped__(identifier)
        finally:
            if previous is sentinel:
                orm.__dict__.pop("Consignment", None)
            else:
                orm.__dict__["Consignment"] = previous

    assert response.status_code == 200
    assert response.get_json()["lr_status"] == "cancelled"
    assert shipment.lr_status == "cancelled"
    assert shipment.cancellation_reason == "Operator cancellation test"
    assert session.flushed is True
    assert session.committed is True
