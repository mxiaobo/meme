from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from .db import get_db
from .logic import advance_balance

bp = Blueprint("participants", __name__, url_prefix="/participants")


@bp.route("/")
def index():
    db = get_db()
    rows = db.execute(
        """SELECT p.*, COALESCE(SUM(a.amount), 0) AS advance_balance
           FROM participants p
           LEFT JOIN advances a ON a.participant_id = p.id
           GROUP BY p.id
           ORDER BY p.status DESC, p.name"""
    ).fetchall()
    return render_template("participants/list.html", participants=rows)


@bp.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("姓名不能为空", "error")
            return render_template("participants/form.html", participant=None)
        db = get_db()
        db.execute(
            """INSERT INTO participants (name, securities_account, status, note)
               VALUES (?, ?, 'active', ?)""",
            (
                name,
                request.form.get("securities_account", "").strip() or None,
                request.form.get("note", "").strip() or None,
            ),
        )
        db.commit()
        flash("已创建", "ok")
        return redirect(url_for("participants.index"))
    return render_template("participants/form.html", participant=None)


@bp.route("/<int:pid>")
def detail(pid):
    db = get_db()
    p = db.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()
    if not p:
        abort(404)
    advances = db.execute(
        """SELECT a.*, s.month AS settlement_month
           FROM advances a
           LEFT JOIN settlements s ON s.id = a.settlement_id
           WHERE a.participant_id = ?
           ORDER BY a.date DESC, a.id DESC""",
        (pid,),
    ).fetchall()
    bal = advance_balance(db, pid)
    settled_lines = db.execute(
        """SELECT sl.*, s.month
           FROM settlement_lines sl
           JOIN settlements s ON s.id = sl.settlement_id
           WHERE sl.participant_id = ? AND s.status = 'settled'
           ORDER BY s.month DESC""",
        (pid,),
    ).fetchall()
    return render_template(
        "participants/detail.html",
        p=p,
        advances=advances,
        balance=bal,
        settled_lines=settled_lines,
    )


@bp.route("/<int:pid>/edit", methods=["GET", "POST"])
def edit(pid):
    db = get_db()
    p = db.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()
    if not p:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("姓名不能为空", "error")
            return render_template("participants/form.html", participant=p)
        db.execute(
            """UPDATE participants SET name=?, securities_account=?, note=?
               WHERE id=?""",
            (
                name,
                request.form.get("securities_account", "").strip() or None,
                request.form.get("note", "").strip() or None,
                pid,
            ),
        )
        db.commit()
        flash("已更新", "ok")
        return redirect(url_for("participants.detail", pid=pid))
    return render_template("participants/form.html", participant=p)


@bp.post("/<int:pid>/toggle")
def toggle(pid):
    db = get_db()
    p = db.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()
    if not p:
        abort(404)
    new_status = "inactive" if p["status"] == "active" else "active"
    db.execute("UPDATE participants SET status=? WHERE id=?", (new_status, pid))
    db.commit()
    flash(f"已{'停用' if new_status == 'inactive' else '启用'}", "ok")
    return redirect(url_for("participants.detail", pid=pid))


@bp.post("/<int:pid>/advances")
def add_advance(pid):
    db = get_db()
    p = db.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()
    if not p:
        abort(404)
    try:
        amount = float(request.form.get("amount", "0"))
    except ValueError:
        flash("金额格式错误", "error")
        return redirect(url_for("participants.detail", pid=pid))
    if amount == 0:
        flash("金额不能为 0", "error")
        return redirect(url_for("participants.detail", pid=pid))
    d = request.form.get("date") or ""
    if not d:
        flash("日期不能为空", "error")
        return redirect(url_for("participants.detail", pid=pid))
    db.execute(
        """INSERT INTO advances (participant_id, amount, kind, date, note)
           VALUES (?, ?, 'advance', ?, ?)""",
        (pid, amount, d, request.form.get("note", "").strip() or None),
    )
    db.commit()
    flash("已新增预支记录", "ok")
    return redirect(url_for("participants.detail", pid=pid))


@bp.post("/advances/<int:aid>/edit")
def edit_advance(aid):
    db = get_db()
    a = db.execute("SELECT * FROM advances WHERE id = ?", (aid,)).fetchone()
    if not a:
        abort(404)
    if a["kind"] == "deduction":
        flash("结算抵扣记录不可手动修改", "error")
        return redirect(url_for("participants.detail", pid=a["participant_id"]))
    try:
        amount = float(request.form.get("amount", "0"))
    except ValueError:
        flash("金额格式错误", "error")
        return redirect(url_for("participants.detail", pid=a["participant_id"]))
    db.execute(
        "UPDATE advances SET amount=?, date=?, note=? WHERE id=?",
        (
            amount,
            request.form.get("date") or a["date"],
            request.form.get("note", "").strip() or None,
            aid,
        ),
    )
    db.commit()
    flash("已修改", "ok")
    return redirect(url_for("participants.detail", pid=a["participant_id"]))


@bp.post("/advances/<int:aid>/delete")
def delete_advance(aid):
    db = get_db()
    a = db.execute("SELECT * FROM advances WHERE id = ?", (aid,)).fetchone()
    if not a:
        abort(404)
    if a["kind"] == "deduction":
        flash("结算抵扣记录不可手动删除", "error")
        return redirect(url_for("participants.detail", pid=a["participant_id"]))
    db.execute("DELETE FROM advances WHERE id = ?", (aid,))
    db.commit()
    flash("已删除", "ok")
    return redirect(url_for("participants.detail", pid=a["participant_id"]))
