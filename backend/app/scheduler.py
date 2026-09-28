from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db, pipeline
from .config import settings

scheduler = BackgroundScheduler(timezone=settings.tz)


def _execute(task: dict, run_id: int) -> None:
    try:
        pipeline.run_task(task, run_id)
    except Exception as exc:  # noqa: BLE001
        db.append_log(run_id, f"未捕获异常: {type(exc).__name__}: {exc}")
        try:
            db.finish_run(run_id, "failed", error=type(exc).__name__)
        except Exception:
            pass


def _job(task_id: int) -> None:
    task = db.get_task(task_id)
    if not task or not task["enabled"] or task.get("deleted_at"):
        return
    run = db.create_run(task_id)
    _execute(task, run["id"])


def rebuild() -> None:
    scheduler.remove_all_jobs()
    for task in db.list_tasks():
        if not task["enabled"]:
            continue
        try:
            trigger = CronTrigger.from_crontab(task["cron"], timezone=task.get("timezone") or settings.tz)
        except ValueError:
            continue
        scheduler.add_job(_job, trigger, id=f"task-{task['id']}", args=[task["id"]], replace_existing=True)


def start() -> None:
    if not scheduler.running:
        scheduler.start()
    rebuild()


def run_task_now(task_id: int) -> dict:
    task = db.get_task(task_id)
    if not task:
        raise KeyError("task")
    run = db.create_run(task_id)
    _execute(task, run["id"])
    return db.get_run(run["id"])  # type: ignore[return-value]
