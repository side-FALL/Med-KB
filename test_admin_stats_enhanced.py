"""Quick verification test for new admin panel data methods."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent))

from database_models import (
    create_default_root, add_operation_log, add_learning_record, utcnow_iso,
)
from user_data_manager import UserDataManager


def main():
    # Setup mock storage
    test_root = create_default_root()
    mock_client = MagicMock()
    mock_client.get_record.return_value = test_root

    def mock_update(record):
        mock_client.get_record.return_value = record

    mock_client.update_record.side_effect = mock_update
    manager = UserDataManager(client=mock_client, auto_init=False, current_user=None)

    # Create a user (use valid SHA-256 hash: 64 hex chars)
    _TEST_HASH = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    manager.create_user("alice", _TEST_HASH)

    # Add operation logs (simulate queries)
    root = test_root
    logs = root.get("logs", {})
    logs = add_operation_log(logs, action="query", username="alice", detail="test query 1")
    logs = add_operation_log(logs, action="query", username="alice", detail="test query 2")
    logs = add_operation_log(logs, action="login", username="alice", detail="login")
    root["logs"] = logs

    # Add learning records
    alice_data = root["users"]["alice"]
    alice_data = add_learning_record(alice_data, topic="test", source_type="qa")
    alice_data = add_learning_record(alice_data, topic="test2", source_type="quiz")
    root["users"]["alice"] = alice_data

    # Test get_operation_logs (1 register + 2 query + 1 login = 4 logs)
    op_logs = manager.get_operation_logs()
    assert len(op_logs) == 4, f"Expected 4 logs, got {len(op_logs)}"
    query_logs = [l for l in op_logs if l["action"] == "query"]
    assert len(query_logs) == 2, f"Expected 2 query logs, got {len(query_logs)}"
    print(f"get_operation_logs: PASS ({len(op_logs)} logs, {len(query_logs)} queries)")

    # Test get_all_learning_records
    records = manager.get_all_learning_records()
    assert len(records) == 2, f"Expected 2 records, got {len(records)}"
    for rec in records:
        assert rec["username"] == "alice", f"Wrong username: {rec['username']}"
    print(f"get_all_learning_records: PASS ({len(records)} records)")

    # Verify deep copy - modifying returned data should not affect storage
    op_logs[0]["action"] = "HACKED"
    assert root["logs"]["operation_logs"][0]["action"] != "HACKED", "Deep copy failed!"
    records[0]["source_type"] = "HACKED"
    assert root["users"]["alice"]["learning_records"][0]["source_type"] != "HACKED", "Deep copy failed!"
    print("Deep copy verification: PASS")

    # Test empty data
    test_root2 = create_default_root()
    mock_client2 = MagicMock()
    mock_client2.get_record.return_value = test_root2
    mock_client2.update_record.side_effect = lambda r: mock_client2.get_record.return_value.__class__ and setattr(mock_client2, "get_record", MagicMock(return_value=r))
    manager2 = UserDataManager(client=mock_client2, auto_init=False, current_user=None)
    assert manager2.get_operation_logs() == []
    assert manager2.get_all_learning_records() == []
    print("Empty data handling: PASS")

    print("\nAll new manager method tests PASSED!")


if __name__ == "__main__":
    main()
