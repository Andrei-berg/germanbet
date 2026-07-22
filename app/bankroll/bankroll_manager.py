from datetime import datetime, timezone
from app import db
from app.models import BankrollLog


def get_current_bank() -> float:
    last = BankrollLog.query.order_by(BankrollLog.id.desc()).first()
    return last.amount if last else 0.0


def set_initial_bank(amount: float) -> float:
    existing = BankrollLog.query.first()
    if not existing:
        log = BankrollLog(amount=amount, change=amount, reason="initial")
        db.session.add(log)
        db.session.commit()
        return amount
    current = get_current_bank()
    if current == amount:
        return amount
    change = round(amount - current, 2)
    log = BankrollLog(amount=amount, change=change, reason="adjustment")
    db.session.add(log)
    db.session.commit()
    return amount


def update_bank(change: float, reason: str = "") -> float:
    current = get_current_bank()
    new_amount = current + change
    if new_amount < 0:
        new_amount = 0.0
    log = BankrollLog(amount=new_amount, change=change, reason=reason)
    db.session.add(log)
    db.session.commit()
    return new_amount
