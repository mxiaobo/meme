"""Settlement business logic."""
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


def compute_preview(db, month):
    """Compute what a settlement for `month` (YYYY-MM) would look like.

    Returns: list of dicts (one per participant who has activity in `month`).
    """
    rows = db.execute(
        """SELECT s.*, p.name AS participant_name, pr.code, pr.name AS project_name
           FROM subscriptions s
           JOIN participants p ON p.id = s.participant_id
           JOIN projects pr ON pr.id = s.project_id
           WHERE s.status = 'open'
             AND s.sell_date IS NOT NULL
             AND substr(s.sell_date, 1, 7) = ?
             AND s.sell_revenue IS NOT NULL""",
        (month,),
    ).fetchall()

    by_part = {}
    for r in rows:
        pid = r["participant_id"]
        bucket = by_part.setdefault(
            pid,
            {
                "participant_id": pid,
                "participant_name": r["participant_name"],
                "subscriptions": [],
                "net_profit": 0.0,
            },
        )
        profit = float(r["sell_revenue"]) - float(r["cost"])
        bucket["subscriptions"].append(
            {
                "id": r["id"],
                "project_code": r["code"],
                "project_name": r["project_name"],
                "lots": r["lots"],
                "cost": float(r["cost"]),
                "sell_revenue": float(r["sell_revenue"]),
                "sell_date": r["sell_date"],
                "profit": profit,
            }
        )
        bucket["net_profit"] += profit

    result = []
    for pid, b in by_part.items():
        net = round(b["net_profit"], 2)
        share = round(max(net, 0) * SHARE_RATIO, 2)
        before = round(advance_balance(db, pid), 2)
        deducted = round(min(share, before), 2) if before > 0 else 0.0
        after = round(max(0.0, before - share), 2)
        payout = round(share - before, 2)
        b.update(
            net_profit=net,
            share=share,
            advance_before=before,
            deducted=deducted,
            advance_after=after,
            payout=payout,
        )
        result.append(b)

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
    rows = db.execute(
        """SELECT s.*, p.name AS participant_name,
                  pr.code AS project_code, pr.name AS project_name
           FROM subscriptions s
           JOIN participants p ON p.id = s.participant_id
           JOIN projects pr ON pr.id = s.project_id
           WHERE s.settlement_id = ?
           ORDER BY s.sell_date, p.name""",
        (settlement_id,),
    ).fetchall()
    return [dict(r) for r in rows]
