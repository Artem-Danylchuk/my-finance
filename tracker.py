"""Phone tracker for the assignment. Production credentials come only from env."""

import argparse
from datetime import date, timedelta
from decimal import InvalidOperation
from functools import wraps
import hashlib
import os
from pathlib import Path
import secrets
import time

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import cash
import homework

NAMES = {"olya": "Оля", "marko": "Марко"}
MONTHS = {8: "Серпень", 9: "Вересень", 10: "Жовтень", 11: "Листопад", 12: "Грудень"}


def create_app(config=None):
    root = homework.ROOT
    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=os.environ.get("TRACKER_SECRET_KEY"),
        PASSWORDS={owner: os.environ.get(f"TRACKER_{owner.upper()}_PASSWORD_HASH", "") for owner in NAMES},
        DATABASE=os.environ.get("TRACKER_DATABASE", str(root / ".private" / "tracker.sqlite3")),
        LOCAL_ONLY=False, PUBLIC_HOST=os.environ.get("TRACKER_PUBLIC_HOST", ""),
        SESSION_COOKIE_NAME="finance_session", SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=16384,
        MAX_FORM_PARTS=12, MAX_FORM_MEMORY_SIZE=16384,
    )
    if config:
        app.config.update(config)
    if not isinstance(app.config["SECRET_KEY"], str) or len(app.config["SECRET_KEY"]) < 32:
        raise ValueError("Встанови TRACKER_SECRET_KEY: випадковий секрет щонайменше 32 символи")
    if any(not h.startswith(("scrypt:", "pbkdf2:sha256:")) for h in app.config["PASSWORDS"].values()):
        raise ValueError("Встанови обидва TRACKER_*_PASSWORD_HASH через змінні середовища")
    if app.config["LOCAL_ONLY"]:
        app.config["SESSION_COOKIE_SECURE"] = False
        app.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"]
    else:
        host = app.config["PUBLIC_HOST"]
        if not host or any(x in host for x in ("/", ":", " ")):
            raise ValueError("Встанови TRACKER_PUBLIC_HOST: точне ім’я HTTPS-сайту без протоколу")
        app.config["TRUSTED_HOSTS"] = [host]

    store = cash.Store(app.config["DATABASE"])
    app.extensions["cash_store"] = store
    bank = homework.records(root)
    categories = sorted({r.category for r in bank if r.category.startswith("Expenses:")})

    @app.template_filter("pln")
    def pln(value):
        return f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",") + " zł"

    @app.template_filter("category")
    def category(value):
        return value.removeprefix("Expenses:").replace("-", " ")

    @app.before_request
    def protect():
        # Do not trust client-supplied X-Forwarded-Proto or identity headers.
        if app.config["LOCAL_ONLY"]:
            if request.remote_addr not in {"127.0.0.1", "::1"}:
                abort(403)
        elif not request.is_secure:
            # A POST may contain credentials; require a fresh HTTPS form rather than forwarding it.
            if request.method != "GET":
                abort(400)
            return redirect(f"https://{app.config['PUBLIC_HOST']}{request.full_path}", code=308)
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST":
            token = request.form.get("csrf", "")
            if not secrets.compare_digest(token, session["csrf"]):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = ("default-src 'none'; style-src 'self' 'unsafe-inline'; "
                                                       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        if not app.config["LOCAL_ONLY"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if session.get("owner") not in NAMES:
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error, status = None, 200
        if request.method == "POST":
            now = int(time.time())
            address = hashlib.sha256((request.remote_addr or "unknown").encode()).hexdigest()
            with store.connect() as db:
                db.execute("DELETE FROM attempts WHERE happened < ?", (now - 600,))
                count = db.execute("SELECT count(*) FROM attempts WHERE address = ?", (address,)).fetchone()[0]
                if count >= 10:
                    return render_template("login.html", error="Забагато спроб. Спробуй за 10 хвилин."), 429
                # Count all attempts, not just failures, before doing expensive password work.
                db.execute("INSERT INTO attempts VALUES (?, ?)", (address, now))
            owner = request.form.get("username", "")
            password = request.form.get("password", "")
            chosen = app.config["PASSWORDS"].get(owner, app.config["PASSWORDS"]["marko"])
            valid = check_password_hash(chosen, password)
            if valid and owner in NAMES:
                session.clear()
                session.update(owner=owner, csrf=secrets.token_urlsafe(32))
                session.permanent = True
                with store.connect() as db:
                    db.execute("DELETE FROM attempts WHERE address = ?", (address,))
                return redirect(url_for("dashboard"), code=303)
            error, status = "Неправильний логін або пароль.", 401
        return render_template("login.html", error=error), status

    @app.get("/")
    @login_required
    def dashboard():
        try:
            month = date.fromisoformat(request.args.get("month", "2026-10") + "-01")
            if month < homework.START or month > date.today().replace(day=1):
                raise ValueError()
        except ValueError:
            abort(400)
        owner = session["owner"]
        rows = bank + store.records()
        own = homework.monthly(rows, owner, month)
        shared = homework.shared_month(rows, month)
        # No other person's private data or totals reach the template.
        months = sorted({r.day.replace(day=1) for r in rows} | {homework.THROUGH.replace(day=1)})
        controls = [(m, homework.monthly(bank, owner, date(2026, m, 1))["bank_variable"])
                    for m in (8, 9)]
        return render_template("dashboard.html", own=own, shared=shared, owner=owner,
                               name=NAMES[owner], names=NAMES, month=month, months=months,
                               labels=MONTHS, controls=controls, categories=categories,
                               form_id=secrets.token_hex(16), through=homework.THROUGH,
                               default_day=min(date.today(), homework.THROUGH).isoformat(),
                               today=date.today().isoformat())

    @app.post("/cash")
    @login_required
    def add_cash():
        try:
            if request.form.get("shared") not in {"personal", "shared"}:
                raise ValueError("Вибери особисту чи спільну витрату")
            store.add(session["owner"], request.form.get("id", ""), request.form.get("day", ""),
                      request.form.get("description", ""), request.form.get("category", ""),
                      request.form.get("amount", ""), request.form["shared"] == "shared", categories)
        except (ValueError, InvalidOperation):
            flash("Не збережено. Перевір дату, суму, опис і категорію. Спільними можуть бути оренда та продукти.", "error")
            return redirect(url_for("dashboard"), code=303)
        flash("Готівкову витрату збережено. Вона вже врахована у звіті.", "success")
        return redirect(url_for("dashboard", month=request.form["day"][:7]), code=303)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"), code=303)

    @app.get("/cash.beancount")
    @login_required
    def export_cash():
        own = [r for r in store.records() if r.owner == session["owner"]]
        return app.response_class(cash.ledger(own), mimetype="text/plain",
                                  headers={"Content-Disposition": 'attachment; filename="cash.beancount"'})

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Телефонний трекер на штучних даних")
    parser.add_argument("--local", action="store_true", help="дозволити HTTP лише на цьому комп’ютері")
    parser.add_argument("--port", type=int, default=5052)
    args = parser.parse_args()
    if not args.local:
        parser.error("Для локального запуску додай --local; на хостингу використовуй WSGI")
    create_app({"LOCAL_ONLY": True}).run(host="127.0.0.1", port=args.port, debug=False)
