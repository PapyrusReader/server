# Reading timer synchronization

Signed-in devices share one active reading timer per account through the
`reading_timers` PowerSync stream. Guest timers remain local. The selected book,
stopwatch/countdown mode, recorded intervals, running timestamp and paused/finished
state are in the payload. Devices compute elapsed time from UTC timestamps locally.
There is no network write for each tick. Countdown elapsed time is capped at its
goal even when no device is open.

## Concurrent and offline actions

Every transition carries a unique payload version and the preceding `base_version`.
The upload handler locks the owner before checking that base version. The first
accepted transition wins. A stale branch and its descendants are acknowledged
without applying their changes, then PowerSync reconciles them to server state.
This follows PowerSync's [server-authoritative conflict resolution](https://docs.powersync.com/usage/lifecycle-maintenance/handling-update-conflicts).

A partial unique index permits one active timer per owner. If two disconnected
devices start independent timers, the first uploaded start wins. The other session
is marked discarded. Offline edits remain usable locally until reconnection, but
cannot replace a more recent accepted branch. Device clocks should be synchronized.

Finishing and saving writes the terminal timer, its reading activities and optional
book progress in one SQLite transaction and one upload batch. The server validates
these effects before committing. Conflicting or repeated saves skip the entire
batch, including progress updates. Terminal rows remain durable so later offline
writes cannot revive a saved or discarded session. Stable activity IDs use the
session UUID and interval index.

The old local `reading_timer` draft is moved to the new table once when its profile
opens. Guest and authenticated databases remain separate. Account/server switches
invalidate the old repository and subscription.

## Rollout

Deploy the server and replication changes before shipping the new client:

1. Apply Alembic revision `c0d1e2f34567` (`uv run alembic upgrade head`). This adds a
   table and indexes, leaving books and reading history untouched.
2. Add `public.reading_timers` to the PowerSync publication and grant the source
   role SELECT access. The local setup script includes both changes.
3. Apply the updated `powersync/sync-config.yaml` and run the updated API.
4. Release the client that writes the new table.

Older servers do not accept timer mutations. A new client must use the updated
server, otherwise its queued timer transactions remain pending. Timer save batches
are never split by tracking-capability discovery, because separating the ledger
from the terminal state would defeat exactly-once save handling.

## Verification

- `tests/services/test_reading_timer_sync.py` covers handoff, competing transitions,
  simultaneous saves, retries, ownership, atomic rollback and deleted books.
- `tests/test_reading_timer_migration.py` verifies additive migration and metadata.
- Client persistence tests cover queued transaction boundaries, migration, guest
  isolation, recovery and retained terminal states.
- `integration_test/powersync_timer_integration_test.dart` uses three isolated local
  databases and two disposable accounts against live API and PowerSync services.
  Run with `RUN_POWERSYNC_INTEGRATION=true` on macOS. It checks countdown handoff,
  pause/resume, offline conflicts, competing saves, reset and account isolation.
