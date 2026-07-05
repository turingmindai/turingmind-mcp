# Control Plane Test Plan (P0 + Session + Contention)

Comprehensive test matrix for cognition control plane work shipped Jul 5 2026.
Run automated suite from `turingmind-mcp/`:

```bash
uv sync --extra dev
uv run pytest tests/test_unified_db.py \
  tests/test_v2_verification.py \
  tests/test_cp_sync_bundle.py \
  tests/test_cp_session_gc.py \
  tests/test_session_lifecycle.py \
  tests/test_p0_hardening.py \
  tests/test_sync_correctness.py \
  tests/test_cp_concurrency.py \
  tests/test_codex_hook_retry.py \
  tests/test_cp_control_plane.py -q
```

---

## 1. Unified SQLite store

| ID | Scenario | Expected | Automated |
|----|----------|----------|-----------|
| U-01 | Fresh DB opens unified `memory.db` | v2 tables + memory tables coexist | `test_unified_db.py` |
| U-02 | Legacy `v2_memory.db` present | One-time migration; legacy renamed | `test_unified_db.py` |
| U-03 | DELETE spec_node | `memory_entries.node_id` nulled via trigger | `test_unified_db.py` |
| U-04 | Invalid `node_id` on memory insert | Rejected by BEFORE trigger | `test_unified_db.py` |
| U-05 | v2 verification on unified path | Graph CRUD works | `test_v2_verification.py` |

**Manual:** Inspect `~/.turingmind/memory.db` with `sqlite3` — confirm no active `v2_memory.db`.

---

## 2. Atomic sync + graph invalidation

| ID | Scenario | Expected | Automated |
|----|----------|----------|-----------|
| S-01 | Sync with seeded rules/patterns | Valid `recall_bundle` + delta | `test_cp_sync_bundle.py` |
| S-02 | Malformed memory (>2000 chars) | `TM-SCHEMA-ERR`, empty bundle, **recall_history unchanged** | `test_cp_sync_bundle.py`, `test_sync_correctness.py` |
| S-03 | Overlapping invalidation + cascade | Downstream node keeps `code_change` evidence | `test_sync_correctness.py` |
| S-04 | v2 read inside `BEGIN` | Read does not commit transaction early | `test_sync_correctness.py` (uses `unified_v2_db` fixture) |
| S-05 | Sync failure mid-transaction | Graph + session roll back together | Manual (kill API during sync) |

**Manual:** Edit a file mapped to two linked SpecNodes; verify both direct + cascade evidence in graph UI.

---

## 3. Memory ranking + delivery

| ID | Scenario | Expected | Automated |
|----|----------|----------|-----------|
| M-01 | Scoped rule ranks above repo-wide | Higher score for touched file | `test_p0_hardening.py` |
| M-02 | `get_relevant_memory` uses shared ranker | Consistent ordering without branch mode | `test_p0_hardening.py` |
| M-03 | Python `apply_recall_delivery` | Writes `.turingmind/recalled.md` | `test_p0_hardening.py` |
| M-04 | Composer change | `recalled-index.json` reset | `test_p0_hardening.py` |

**Manual (Cursor):** Edit file → hook sync → verify `recalled.md` delta; switch composer → index reset.

**Manual (Codex/Claude/AG):** `turingmind sync <files>` → `.turingmind/recalled.md` + `session.json` updated.

---

## 4. Session lifecycle

| ID | Scenario | Expected | Automated |
|----|----------|----------|-----------|
| L-01 | Sync creates session | Row in `coding_sessions` | `test_cp_session_gc.py` |
| L-02 | Heartbeat extends TTL +4h | `expires_at` in future | `test_cp_session_gc.py` |
| L-03 | Expired session GC | Row deleted + summary observation | `test_cp_session_gc.py`, `test_session_lifecycle.py` |
| L-04 | `POST /session/{id}/end` | Session archived + deleted | `test_session_lifecycle.py` |
| L-05 | `POST /session/end` by composer | Same | `test_session_lifecycle.py` |
| L-06 | Throttled heartbeat hook | Max 1 heartbeat / 5 min | Manual (hook.log timestamps) |
| L-07 | Cursor `sessionEnd` hook | Session row removed | Live verified Jul 5 |
| L-08 | Codex `SessionEnd` with `session.json` | End succeeds without event composer_id | Live verified Jul 5 |

**Manual:** Set `expires_at` in past → wait 60s → confirm GC log `Session GC: {archived: N}`.

---

## 5. SQLite contention

| ID | Scenario | Expected | Automated |
|----|----------|----------|-----------|
| C-01 | Mutating routes serialized | No overlapping write lock errors under load | `test_cp_concurrency.py`, Manual (parallel sync) |
| C-02 | Commit retry on busy | Transient lock succeeds after backoff | Manual |
| C-03 | Hook retry on 503/locked 500 | Retries; no retry on 404 | Live verified Jul 5 (`mcp.js` + `_hook_common.py`) |
| C-04 | Spool overflow warning | ERROR log at 50+ spool lines | Live verified Jul 5 |
| C-05 | Background reconcile + sync | No corruption; possible brief lock wait | Manual |

**Manual load test:**
```bash
# Terminal 1: API
uv run python -m turingmind_mcp.api_server

# Terminal 2: parallel sync
for i in $(seq 1 20); do
  curl -s -X POST http://127.0.0.1:8477/api/v2/sync \
    -H 'Content-Type: application/json' \
    -d '{"repo":"org/repo","files":["src/a.py"],"composer_id":"load-'$i'"}' &
done
wait
```
All responses should be 200; check logs for lock retries, not unhandled 500s.

---

## 6. Hook / plugin integration

| ID | Scenario | Expected | Manual |
|----|----------|----------|--------|
| H-01 | Cursor `afterFileEdit` | Sync + delivery + heartbeat | Yes |
| H-02 | Cursor `sessionEnd` | Session end API called | Live verified Jul 5 |
| H-03 | Codex `turingmind sync` | Delivery + session.json | Live verified Jul 5 |
| H-04 | Codex `SessionEnd` | Reads session.json composer_id | Live verified Jul 5 |
| H-05 | API down | Spool grows + SPOOL_OVERFLOW warning | Live verified Jul 5 |

---

## 7. Regression watchlist (post-fix)

These were bugs found in review — tests above should prevent recurrence:

1. **Schema before session write** — `TM-SCHEMA-ERR` must not append to `recall_history` (S-02)
2. **v2 read must not `with conn:` on write txn** — S-04
3. **Cascade uses transaction conn** — S-03
4. **`bundle_delta` cleared on schema error** — S-02
5. **Codex SessionEnd reads session.json** — L-08

---

## 8. Sign-off checklist

- [x] Full automated suite green (30 tests — section command above)
- [x] Load test C-01: 20 parallel syncs → all HTTP 200 (~642ms), no 500s
- [x] Codex SessionEnd L-08: reads `session.json` composer_id, logs `Session ended: <id>`
- [x] Cursor sessionEnd H-02: clears `session.json` fields after successful end
- [x] Codex CLI sync H-03: `turingmind sync` → API 200, `session.json` updated
- [x] Spool overflow C-04: 50 failed POSTs → `SPOOL_OVERFLOW` ERROR in hook.log
- [x] Spool overflow C-04: 50 failed POSTs → `SPOOL_OVERFLOW` ERROR in hook.log
- [x] TC-CP-05 spool replay + sync idempotency — `test_cp_spool.py`, `mcp.test.js`
- [x] H-01 hook path (automated) — `cluster.test.js` + sync via `postWithSpool` + `event_id`
- [ ] Manual Cursor `afterFileEdit` IDE smoke (optional one-time sign-off in real IDE)
- [x] `docs/cognition-control-plane-plan.html` — correctness audit section + gap rows #1, #3, #5, #6, #7, #8 updated

---

## 9. Implementation audit status (Jul 5 2026)

| Item | Was marked | Actual status |
|------|------------|---------------|
| Schema before `recall_history` | Done | ✅ Verified — `control_plane.py:161–220`, `test_sync_correctness.py` |
| Transactional cascade reads | Done | ✅ Verified — `conn` threaded, `test_sync_correctness.py` |
| Session end ID (CLI/Codex) | Done | ✅ Verified — `recall_delivery.py` + `_hook_common.py`, live hooks |
| SQLite contention | Done | ✅ Verified — write lock + load test 20/20 |
| Memory ranker + stop words | Done | ✅ Verified — `test_p0_hardening.py` |
| `mcp.js` + Codex selective retry | ~~Pending~~ | ✅ **Done** — `mcp.js` + `_hook_common.is_retryable_api_error`, `test_codex_hook_retry.py` |
| Unified `memory.db` merge | ~~Pending~~ | ✅ **Done** — migration on init, `test_unified_db.py` |

**Optional follow-ups:** background reconcile under write lock · ~~delete empty legacy `v2_memory.db` shell~~ ✅ done · optional IDE smoke for H-01.

### Live verification (Jul 5 2026)

```bash
# Health
curl http://127.0.0.1:8477/api/v2/health   # → {"status":"ok"}

# Load test (20 parallel)
# → 20 × HTTP 200

# Codex SessionEnd (from repo root, session.json pre-seeded)
echo '{}' | uv run python ../turingmind-codex-plugin/hooks/scripts/on_session_end.py
# → hook.log: Session ended: <session_id>

# Cursor sessionEnd
node ../turingmind-cursor-plugin/.../on-session-end.js
# → session.json composer_id/session_id nulled

# Codex CLI sync (from repo with PYTHONPATH to turingmind_mcp)
PYTHONPATH=src uv run python ../turingmind-codex-plugin/cli/turingmind sync src/foo.py --repo org/repo
# → session.json updated; recalled-index.json written

# Spool overflow (dead API port, 50 postWithSpool calls)
# → hook.log: SPOOL_OVERFLOW: 50 events queued
```
