"""Settlement business logic with chain-based per-stock profit."""
from datetime import date
from .db import get_db, backup_db

SHARE_RATIO = 0.30


def advance_balance(db, participant_id):
    row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS bal FROM advances WHERE participant_id = ?",
        (participant_id,),
    ).fetchone()
    return float(row["bal"] or 0)


def all_advance_balances(db):
    rows = db.execute(
        """SELECT p.id, p.name, COALESCE(SUM(a.amount), 0) AS balance
           FROM participants p
           LEFT JOIN advances a ON a.participant_id = p.id
           GROUP BY p.id ORDER BY p.name"""
    ).fetchall()
    return [dict(r) for r in rows]


def active_settlement_for_month(db, month):
    return db.execute(
        "SELECT * FROM settlements WHERE month = ? AND status = 'settled'",
        (month,),
    ).fetchone()


def month_of(date_str):
    return date_str[:7] if date_str else None


def chain_profit_for_subscription(db, sub_id):
    """Compute chain profit for a single subscription.

    Within the same calendar month for the same participant, find the previous
    sold subscription (by sell_date asc, id asc) and use its balance_after.
    If none, use participant.initial_balance as the chain head.
    Returns None if the subscription itself has no balance_after / sell_date.
    """
    sub = db.execute(
        """SELECT s.*, p.initial_balance
           FROM subscriptions s
           JOIN participants p ON p.id = s.participant_id
           WHERE s.id = ?""",
        (sub_id,),
    ).fetchone()
    if not sub or sub["balance_after"] is None or not sub["sell_date"]:
        return None

    prev = db.execute(
        """SELECT balance_after FROM subscriptions
           WHERE participant_id = ?
             AND balance_after IS NOT NULL
             AND sell_date IS NOT NULL
             AND substr(sell_date, 1, 7) = substr(?, 1, 7)
             AND (sell_date < ? OR (sell_date = ? AND id < ?))
           ORDER BY sell_date DESC, id DESC LIMIT 1""",
        (
            sub["participant_id"], sub["sell_date"],
            sub["sell_date"], sub["sell_date"], sub["id"],
        ),
    ).fetchone()
    prev_balance = float(prev["balance_after"]) if prev else float(sub["initial_balance"])
    return round(float(sub["balance_after"]) - prev_balance, 2)


def compute_preview(db, month):
    """Compute a settlement preview for `month` (YYYY-MM).

    Returns one entry per participant who has at least one sold subscription
    in that month, with the chain-based per-sub profits and the rolled up
    monthly figures.
    """
    rows = db.execute(
        """SELECT s.*,
                  p.name AS participant_name,
                  p.initial_balance,
                  pr.code AS project_code,
                  pr.name AS project_name
           FROM subscriptions s
           JOIN participants p ON p.id = s.participant_id
           JOIN projects pr ON pr.id = s.project_id
           WHERE s.status = 'open'
             AND s.balance_after IS NOT NULL
             AND s.sell_date IS NOT NULL
             AND substr(s.sell_date, 1, 7) = ?
           ORDER BY s.participant_id, s.sell_date, s.id""",
        (month,),
    ).fetchall()

    by_part = {}
    for r in rows:
        pid = r["participant_id"]
        bucket = by_part.setdefault(pid, {
            "participant_id": pid,
            "participant_name": r["participant_name"],
            "initial_balance": float(r["initial_balance"]),
            "raw_subs": [],
        })
        bucket["raw_subs"].append(dict(r))

    result = []
    for pid, b in by_part.items():
        prev_balance = b["initial_balance"]
        items = []
        for r in b["raw_subs"]:
            ba = float(r["balance_after"])
            profit = round(ba - prev_balance, 2)
            items.append({
                "id": r["id"],
                "project_code": r["project_code"],
                "project_name": r["project_name"],
                "lots": r["lots"],
                "cost": float(r["cost"] or 0),
                "balance_after": ba,
                "sell_date": r["sell_date"],
                "prev_balance": prev_balance,
                "profit": profit,
            })
            prev_balance = ba

        net = round(sum(s["profit"] for s in items), 2)
        share = round(max(net, 0) * SHARE_RATIO, 2)
        before = round(advance_balance(db, pid), 2)
        deducted = round(min(share, before), 2) if before > 0 else 0.0
        after = round(max(0.0, before - share), 2)
        payout = round(share - before, 2)

        result.append({
            "participant_id": pid,
            "participant_name": b["participant_name"],
            "initial_balance": b["initial_balance"],
            "subscriptions": items,
            "net_profit": net,
            "share": share,
            "advance_before": before,
            "deducted": deducted,
            "advance_after": after,
            "payout": payout,
        })

    result.sort(key=lambda x: x["participant_name"])
    return result


def confirm_settlement(db, month, lines):
    """Persist a settlement based on previously computed lines."""
    if active_settlement_for_month(db, month):
        raise ValueError(f"{month} 已经有一份生效的结算单，请先反结算。")
    if not lines:
        raise ValueError("当月没有可结算的记录。")

    backup_db()

    today = date.today().isoformat()
    cur = db.execute(
        "INSERT INTO settlements (month, status) VALUES (?, 'settled')",
        (month,),
    )
    settlement_id = cur.lastrowid

    sub_ids = []
    for line in lines:
        db.execute(
            """INSERT INTO settlement_lines
               (settlement_id, participant_id, net_profit, share,
                advance_before, deducted, advance_after, payout)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                settlement_id,
                line["participant_id"],
                line["net_profit"],
                line["share"],
                line["advance_before"],
                line["deducted"],
                line["advance_after"],
                line["payout"],
            ),
        )
        if line["deducted"] > 0:
            db.execute(
                """INSERT INTO advances
                   (participant_id, amount, kind, settlement_id, date, note)
                   VALUES (?, ?, 'deduction', ?, ?, ?)""",
                (
                    line["participant_id"],
                    -line["deducted"],
                    settlement_id,
                    today,
                    f"{month} 月度结算抵扣",
                ),
            )
        for sub in line["subscriptions"]:
            sub_ids.append(sub["id"])

    if sub_ids:
        placeholders = ",".join("?" * len(sub_ids))
        db.execute(
            f"UPDATE subscriptions SET status='settled', settlement_id=? "
            f"WHERE id IN ({placeholders})",
            (settlement_id, *sub_ids),
        )

    db.commit()
    return settlement_id


def reverse_settlement(db, settlement_id):
    s = db.execute(
        "SELECT * FROM settlements WHERE id = ?", (settlement_id,)
    ).fetchone()
    if not s:
        raise ValueError("结算单不存在")
    if s["status"] != "settled":
        raise ValueError("结算单已是反结算状态")

    backup_db()

    db.execute(
        """UPDATE subscriptions SET status='open', settlement_id=NULL
           WHERE settlement_id = ?""",
        (settlement_id,),
    )
    db.execute("DELETE FROM advances WHERE settlement_id = ?", (settlement_id,))
    db.execute(
        """UPDATE settlements
           SET status='reversed', reversed_at=datetime('now','localtime')
           WHERE id = ?""",
        (settlement_id,),
    )
    db.commit()


def get_settlement_lines(db, settlement_id):
    rows = db.execute(
        """SELECT sl.*, p.name AS participant_name
           FROM settlement_lines sl
           JOIN participants p ON p.id = sl.participant_id
           WHERE sl.settlement_id = ?
           ORDER BY p.name""",
        (settlement_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_settlement_subscriptions(db, settlement_id):
    """Return subs of a settlement annotated with their chain profit.

    Re-computes chain profit on the fly using each participant's stored
    initial_balance (still valid since balance_after on settled subs is locked).
    """
    rows = db.execute(
        """SELECT s.*,
                  p.name AS participant_name,
                  p.initial_balance,
                  pr.code AS project_code,
                  pr.name AS project_name
           FROM subscriptions s
           JOIN participants p ON p.id = s.participant_id
           JOIN projects pr ON pr.id = s.project_id
           WHERE s.settlement_id = ?
           ORDER BY s.participant_id, s.sell_date, s.id""",
        (settlement_id,),
    ).fetchall()

    out = []
    last_balance_by_pid = {}
    for r in rows:
        pid = r["participant_id"]
        prev = last_balance_by_pid.get(pid, float(r["initial_balance"]))
        ba = float(r["balance_after"]) if r["balance_after"] is not None else None
        profit = round(ba - prev, 2) if ba is not None else None
        d = dict(r)
        d["prev_balance"] = prev
        d["profit"] = profit
        out.append(d)
        if ba is not None:
            last_balance_by_pid[pid] = ba
    return out
