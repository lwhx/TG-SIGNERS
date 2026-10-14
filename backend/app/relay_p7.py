"""P7 authenticated read-only Bot results and album delivery stats."""

from fastapi import APIRouter, Query

from .relay_history import get_ledger

router = APIRouter(prefix="/relay", tags=["P7 Bot 回执与媒体组"])


@router.get("/bot-results", summary="115 Bot 等转存机器人结果，独立于 Telegram 送达状态")
def bot_results(
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(25, ge=1, le=100),
    account_id: int | None = Query(None, ge=1),
    task_id: int | None = Query(None, ge=1),
    status: str | None = Query(
        None, pattern=r"^(pending|succeeded|failed|unclassified|expired)$"
    ),
):
    return get_ledger().bot_results(
        account_id=account_id, task_id=task_id,
        status=status, page=page, page_size=page_size,
    )


@router.get("/media-groups/stats", summary="整组原样转发/复制的独立投递结果统计")
def media_group_stats(
    account_id: int | None = Query(None, ge=1),
    task_id: int | None = Query(None, ge=1),
):
    return get_ledger().album_stats(account_id=account_id, task_id=task_id)
