from datetime import date
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, send_file,
)
from .db import get_db
from .logic import (
    compute_preview, confirm_settlement, reverse_settlement,
    active_settlement_for_month, get_settlement_lines, get_settlement_subscriptions,
)
from .excel_export import build_settlement_workbook

bp = Blueprint("settlements", __name__, url_prefix="/settlements")


def _default_month():
    return date.today().strftime("%Y-%m")


@bp.route("/")
def index():
    db = get_db()
    rows = db.execute(
        """SELECT s.*,
                  COUNT(sl.id) AS line_count,
                  COALESCE(SUM(sl.share), 0) AS total_share,
                  COALESCE(SUM(sl.payout), 0) AS total_payout
           FROM settlements s
           LEFT JOIN settlement_lines sl ON sl.settlement_id = s.id
           GROUP BY s.id
           ORDER BY s.month DESC, s.id DESC"""
    ).fetchall()
    return render_template(
        "settlements/index.html",
        settlements=rows,
        default_month=_default_month(),
    )


@bp.route("/preview")
def preview():
    month = request.args.get("month") or _default_month()
    db = get_db()
    existing = active_settlement_for_month(db, month)
    if existing:
        return redirect(url_for("settlements.detail", sid=existing["id"]))
    lines = compute_preview(db, month)
    return render_template(
        "settlements/preview.html",
        month=month,
        lines=lines,
        default_month=_default_month(),
    )


@bp.post("/confirm")
def confirm():
    month = request.form.get("month")
    if not month:
        abort(400)
    db = get_db()
    try:
        lines = compute_preview(db, month)
        sid = confirm_settlement(db, month, lines)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("settlements.preview", month=month))
    flash(f"{month} 月度结算完成", "ok")
    return redirect(url_for("settlements.detail", sid=sid))


@bp.route("/<int:sid>")
def detail(sid):
    db = get_db()
    s = db.execute("SELECT * FROM settlements WHERE id = ?", (sid,)).fetchone()
    if not s:
        abort(404)
    lines = get_settlement_lines(db, sid)
    subs = get_settlement_subscriptions(db, sid)
    subs_by_pid = {}
    for sub in subs:
        subs_by_pid.setdefault(sub["participant_id"], []).append(sub)
    return render_template(
        "settlements/detail.html",
        s=s, lines=lines, subs_by_pid=subs_by_pid,
    )


@bp.post("/<int:sid>/reverse")
def reverse(sid):
    confirm_text = request.form.get("confirm_text", "")
    if confirm_text.strip() != "确认反结算":
        flash("反结算需要输入：确认反结算", "error")
        return redirect(url_for("settlements.detail", sid=sid))
    db = get_db()
    try:
        reverse_settlement(db, sid)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("settlements.detail", sid=sid))
    flash("已反结算", "ok")
    return redirect(url_for("settlements.detail", sid=sid))


@bp.route("/<int:sid>/export")
def export(sid):
    db = get_db()
    s = db.execute("SELECT * FROM settlements WHERE id = ?", (sid,)).fetchone()
    if not s:
        abort(404)
    lines = get_settlement_lines(db, sid)
    bio = build_settlement_workbook(s["month"], lines)
    return send_file(
        bio,
        as_attachment=True,
        download_name=f"settlement_{s['month']}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
