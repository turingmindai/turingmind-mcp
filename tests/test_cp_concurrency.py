from __future__ import annotations

import pytest
import concurrent.futures
import uuid

def test_cp_sync_concurrency(api_client, tier_repo):
    """TC-CP-22: Verify concurrent sync requests are handled successfully without SQLite lock errors."""
    client, db = api_client
    
    # Generate 10 concurrent requests with different composer IDs
    num_requests = 10
    payloads = [
        {
            "repo": tier_repo,
            "files": [f"src/module_{i}.py"],
            "composer_id": f"composer-concurrent-{uuid.uuid4()}",
            "branch": "main",
        }
        for i in range(num_requests)
    ]

    # Use ThreadPoolExecutor to trigger concurrent client calls
    def send_sync(payload):
        return client.post("/api/v2/sync", json=payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_requests) as executor:
        futures = [executor.submit(send_sync, p) for p in payloads]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Assert that all concurrent syncs completed successfully (status 200)
    for res in results:
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "synced"
