import os
from datetime import date
from flask import Flask, render_template
from ipo.db import init_db, get_db, close_db
from ipo.logic import all_advance_balances
from ipo.routes_participants import bp as participants_bp
from ipo.routes_projects import bp as projects_bp
from ipo.routes_settlements import bp as settlements_bp


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    base = os.path.dirname(os.path.abspath(__file__))
    app.config["DATABASE"] = os.path.join(base, "data", "portfolio.db")
    app.config["BACKUP_DIR"] = os.path.join(base, "data", "backups")
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-local-only")

    init_db(app)
    app.teardown_appcontext(close_db)

    app.register_blueprint(participants_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(settlements_bp)

    @app.route("/")
    def dashboard():
        db = get_db()
        active_count = db.execute(
            "SELECT COUNT(*) AS c FROM participants WHERE status='active'"
        ).fetchone()["c"]
        total_count = db.execute(
            "SELECT COUNT(*) AS c FROM participants"
        ).fetchone()["c"]

        open_subs = db.execute(
            """SELECT COUNT(*) AS c FROM subscriptions
               WHERE status='open'"""
        ).fetchone()["c"]

        sold_unsettled = db.execute(
            """SELECT COUNT(*) AS c FROM subscriptions
               WHERE status='open' AND balance_after IS NOT NULL"""
        ).fetchone()["c"]

        balances = all_advance_balances(db)

        last = db.execute(
            """SELECT s.*,
                      COALESCE(SUM(sl.share), 0) AS total_share,
                      COALESCE(SUM(sl.payout), 0) AS total_payout,
                      COUNT(sl.id) AS line_count
               FROM settlements s
               LEFT JOIN settlement_lines sl ON sl.settlement_id = s.id
               WHERE s.status='settled'
               GROUP BY s.id
               ORDER BY s.month DESC, s.id DESC LIMIT 1"""
        ).fetchone()

        return render_template(
            "dashboard.html",
            active_count=active_count,
            total_count=total_count,
            open_subs=open_subs,
            sold_unsettled=sold_unsettled,
            balances=balances,
            last=last,
            today=date.today().isoformat(),
        )

    @app.template_filter("money")
    def money(v):
        if v is None:
            return ""
        try:
            return f"{float(v):,.2f}"
        except (TypeError, ValueError):
            return str(v)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=True)
