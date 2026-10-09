from types import SimpleNamespace

from gateway.runtime import cleanup_revoked
from gateway.store import InMemoryStore


class DeletionProvider:
    def __init__(self, failure=False):
        self.nodes = {"revoked-node", "approved-node", "ingesting-node"}
        self.failure = failure

    def delete(self, node_id):
        if self.failure:
            raise TimeoutError("sensitive provider response")
        self.nodes.remove(node_id)


def app_with_records(provider):
    store = InMemoryStore()
    for memory_id, state in (("revoked", "REVOKED"), ("approved", "APPROVED"), ("ingesting", "INGESTING")):
        store.put("memory", memory_id, {
            "memory_id": memory_id, "state": state, "node_id": memory_id+"-node", "delete_pending": True,
        })
    return SimpleNamespace(store=store, provider=provider)


def test_maintenance_deletes_only_revoked_nodes_and_preserves_local_revocation():
    app = app_with_records(DeletionProvider())

    assert cleanup_revoked(app) == {"attempted": 1, "deleted": 1}
    assert app.provider.nodes == {"approved-node", "ingesting-node"}
    memory = app.store.get("memory", "revoked")
    assert memory["state"] == "REVOKED"
    assert memory["delete_pending"] is False


def test_failed_provider_deletion_leaves_revocation_and_retry_pending():
    app = app_with_records(DeletionProvider(failure=True))

    assert cleanup_revoked(app) == {"attempted": 1, "deleted": 0}
    assert "revoked-node" in app.provider.nodes
    memory = app.store.get("memory", "revoked")
    assert memory["state"] == "REVOKED"
    assert memory["delete_pending"] is True


def test_failed_acknowledgement_does_not_overwrite_newer_revoked_record():
    app = app_with_records(DeletionProvider())
    original = app.provider.delete
    def delete_and_update(node_id):
        original(node_id)
        memory = app.store.get("memory", "revoked")
        app.store.put("memory", "revoked", dict(memory, reason_codes=["INCIDENT"]),
                      expected_revision=memory["_rev"])
    app.provider.delete = delete_and_update

    assert cleanup_revoked(app) == {"attempted": 1, "deleted": 0}
    memory = app.store.get("memory", "revoked")
    assert memory["state"] == "REVOKED"
    assert memory["reason_codes"] == ["INCIDENT"]
    assert memory["delete_pending"] is True
