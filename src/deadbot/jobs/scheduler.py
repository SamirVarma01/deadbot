import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from deadbot.app import AppContext
from deadbot.data.db import repo

log = logging.getLogger(__name__)


async def sync_due_accounts(app: AppContext) -> None:
    """Sync the stalest linked accounts, one at a time to stay inside API rate limits."""
    s = app.settings
    older_than = datetime.now(UTC) - timedelta(minutes=s.sync_interval_minutes)
    async with app.sessions() as session:
        due = await repo.linked_accounts_due(session, older_than, s.sync_batch_size)
    for account_id in due:
        if app.sync.is_syncing(account_id):
            continue
        try:
            await app.sync.sync_account(account_id)
        except Exception:  # already logged and recorded on the player row
            continue


def start_scheduler(app: AppContext) -> AsyncIOScheduler:
    """Must be called from inside the running event loop."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        sync_due_accounts,
        "interval",
        # Run more often than the staleness threshold so batches drain steadily.
        minutes=max(1, app.settings.sync_interval_minutes // 3),
        args=[app],
        id="sync_due_accounts",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(seconds=30),
    )
    scheduler.start()
    log.info("scheduler started")
    return scheduler
