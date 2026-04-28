from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from .db import get_db
from .logic import (
    active_settlement_for_month, month_of, chain_profit_for_subscription,
)

bp = Blueprint("projects", __name__, url_prefix="/projects")


@bp.route("/")
def index():
    db = get_db()
    rows = db.execute(
        """SELECT p.*,
                  COUNT(s.id) AS sub_count,
                  SUM(CASE WHEN s.balance_after IS NOT NULL THEN 1 ELSE 0 END) AS sold_count,
                  SUM(CASE WHEN s.status = 'settled' THEN 1 ELSE 0 END) AS settled_count,
                  COALESCE(SUM(s.cost), 0) AS total_cost
           FROM projects p
           LEFT JOIN subscriptions s ON s.project_id = p.id
           GROUP BY p.id
           ORDER BY COALESCE(p.listing_date, '') DESC, p.id DESC"""
    ).fetchall()
    return render_template("projects/list.html", projects=rows)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        name = request.form.get("name", "").strip()
        if not code or not name:
            flash("代码和名称不能为空", "error")
            return render_template("projects/form.html", project=None)
        db = get_db()
        cur = db.execute(
            """INSERT INTO projects (code, name, listing_date, note)
               VALUES (?, ?, ?, ?)""",
            (
                code,
                name,
                request.form.get("listing_date") or None,
                request.form.get("note", "").strip() or None,
            ),
        )
        db.commit()
        flash("已创建项目", "ok")
        return redirect(url_for("projects.detail", proj_id=cur.lastrowid))
    return render_template("projects/form.html", project=None)


@bp.route("/<int:proj_id>")
def detail(proj_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (proj_id,)).fetchone()
    if not proj:
        abort(404)

    subs_rows = db.execute(
        """SELECT s.*, p.name AS participant_name, p.status AS participant_status
           FROM subscriptions s
           JOIN participants p ON p.id = s.participant_id
           WHERE s.project_id = ?""",
        (proj_id,),
    ).fetchall()
    subs_by_pid = {s["participant_id"]: dict(s) for s in subs_rows}

    participants = db.execute(
        "SELECT * FROM participants ORDER BY status DESC, name"
    ).fetchall()

    rows = []
    total_cost = 0.0
    total_profit = 0.0
    sold_count = 0
    for p in participants:
        sub = subs_by_pid.get(p["id"])
        if p["status"] == "inactive" and not sub:
            continue
        profit = None
        if sub:
            total_cost += float(sub["cost"] or 0)
            if sub["balance_after"] is not None and sub["sell_date"]:
                profit = chain_profit_for_subscription(db, sub["id"])
                if profit is not None:
                    total_profit += profit
                    sold_count += 1
        rows.append({"participant": dict(p), "sub": sub, "profit": profit})

    return render_template(
        "projects/detail.html",
        project=proj,
        rows=rows,
        total_cost=total_cost,
        total_profit=total_profit,
        sold_count=sold_count,
    )


@bp.post("/<int:proj_id>/info")
def update_info(proj_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (proj_id,)).fetchone()
    if not proj:
        abort(404)
    code = request.form.get("code", "").strip()
    name = request.form.get("name", "").strip()
    if not code or not name:
        flash("代码和名称不能为空", "error")
        return redirect(url_for("projects.detail", proj_id=proj_id))
    db.execute(
        """UPDATE projects SET code=?, name=?, listing_date=?, note=? WHERE id=?""",
        (
            code,
            name,
            request.form.get("listing_date") or None,
            request.form.get("note", "").strip() or None,
            proj_id,
        ),
    )
    db.commit()
    flash("项目信息已更新", "ok")
    return redirect(url_for("projects.detail", proj_id=proj_id))


@bp.post("/<int:proj_id>/subscriptions")
def save_subscriptions(proj_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (proj_id,)).fetchone()
    if not proj:
        abort(404)

    existing = {
        s["participant_id"]: dict(s)
        for s in db.execute(
            "SELECT * FROM subscriptions WHERE project_id = ?", (proj_id,)
        ).fetchall()
    }

    pids = request.form.getlist("pid")
    errors = []

    for pid_str in pids:
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        checked = request.form.get(f"checked_{pid}") == "1"
        cur = existing.get(pid)

        if cur and cur["status"] == "settled":
            continue

        if not checked:
            if cur:
                db.execute("DELETE FROM subscriptions WHERE id = ?", (cur["id"],))
            continue

        try:
            lots = int(request.form.get(f"lots_{pid}") or 0)
        except ValueError:
            lots = 0
        try:
            cost = float(request.form.get(f"cost_{pid}") or 0)
        except ValueError:
            cost = 0.0
        ba_raw = request.form.get(f"balance_after_{pid}", "").strip()
        sell_date_raw = request.form.get(f"sell_date_{pid}", "").strip()

        if ba_raw:
            try:
                balance_after = float(ba_raw)
            except ValueError:
                errors.append(f"参与人 #{pid} 的卖出后余额格式错误")
                continue
        else:
            balance_after = None

        sell_date = sell_date_raw or None

        if (balance_after is None) != (sell_date is None):
            errors.append(
                f"参与人 #{pid}：卖出后余额与卖出日期必须同时填写或同时留空"
            )
            continue

        if sell_date:
            m = month_of(sell_date)
            if active_settlement_for_month(db, m):
                errors.append(
                    f"参与人 #{pid}：卖出日期 {sell_date} 所在月 {m} 已结算，"
                    f"请先反结算后再修改"
                )
                continue

        if cur:
            db.execute(
                """UPDATE subscriptions
                   SET lots=?, cost=?, balance_after=?, sell_date=?
                   WHERE id=?""",
                (lots, cost, balance_after, sell_date, cur["id"]),
            )
        else:
            db.execute(
                """INSERT INTO subscriptions
                   (project_id, participant_id, lots, cost, balance_after, sell_date)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (proj_id, pid, lots, cost, balance_after, sell_date),
            )

    db.commit()

    if errors:
        for e in errors:
            flash(e, "error")
    else:
        flash("已保存", "ok")
    return redirect(url_for("projects.detail", proj_id=proj_id))


@bp.post("/<int:proj_id>/delete")
def delete(proj_id):
    db = get_db()
    proj = db.execute("SELECT * FROM projects WHERE id = ?", (proj_id,)).fetchone()
    if not proj:
        abort(404)
    has_settled = db.execute(
        "SELECT 1 FROM subscriptions WHERE project_id=? AND status='settled' LIMIT 1",
        (proj_id,),
    ).fetchone()
    if has_settled:
        flash("项目存在已结算的记录，不能删除", "error")
        return redirect(url_for("projects.detail", proj_id=proj_id))
    db.execute("DELETE FROM subscriptions WHERE project_id = ?", (proj_id,))
    db.execute("DELETE FROM projects WHERE id = ?", (proj_id,))
    db.commit()
    flash("项目已删除", "ok")
    return redirect(url_for("projects.index"))
