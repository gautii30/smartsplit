import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from models import get_db, init_db
from parser import parse_expense, ParseWarning
from splitter import calculate_balances, simplify_debts, aggregate_friend_balances


# ── App lifecycle ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="SmartSplit v2", lifespan=lifespan)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

@app.get("/")
def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# ── Request models ─────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    name: str

class CreateGroupRequest(BaseModel):
    name: str
    created_by: str

class AddMemberRequest(BaseModel):
    name: str

class ParseRequest(BaseModel):
    text: str
    default_user: str = ""

class SaveExpenseRequest(BaseModel):
    description: str
    amount: float
    paid_by: str
    participants: list[str]
    category: str = "general"
    created_by: str

class UpdateExpenseRequest(BaseModel):
    description: str
    amount: float
    paid_by: str
    participants: list[str]
    category: str = "general"
    updated_by: str = ""

class SettleRequest(BaseModel):
    payer: str       # who sends the money
    payee: str       # who receives it
    amount: float
    group_id: int


# ── Helpers ────────────────────────────────────────────────────────────────────

def log_activity(db, group_id: int, user_name: str, action: str, details: str = ""):
    db.execute(
        "INSERT INTO activity_log (group_id, user_name, action, details) VALUES (?, ?, ?, ?)",
        (group_id, user_name, action, details)
    )


def row_to_dict(row):
    return dict(row) if row else None


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
def login(req: LoginRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    db = get_db()
    try:
        db.execute("INSERT OR IGNORE INTO users (name) VALUES (?)", (name,))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
        return row_to_dict(user)
    finally:
        db.close()


@app.get("/api/auth/me")
def get_me(name: str = Query(...)):
    db = get_db()
    try:
        user = db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
        if not user:
            raise HTTPException(404, "User not found")
        return row_to_dict(user)
    finally:
        db.close()


# ── Groups ─────────────────────────────────────────────────────────────────────

@app.get("/api/groups")
def list_groups(user: str = Query(...)):
    db = get_db()
    try:
        # Groups where the user is a member OR created the group
        rows = db.execute("""
            SELECT DISTINCT g.* FROM groups g
            LEFT JOIN members m ON m.group_id = g.id
            WHERE m.name = ? OR g.created_by = ?
            ORDER BY g.created_at DESC
        """, (user, user)).fetchall()

        result = []
        for g in rows:
            members = db.execute(
                "SELECT name FROM members WHERE group_id = ? ORDER BY name", (g["id"],)
            ).fetchall()
            expense_count = db.execute(
                "SELECT COUNT(*) as c FROM expenses WHERE group_id = ?", (g["id"],)
            ).fetchone()["c"]
            result.append({
                **row_to_dict(g),
                "members": [m["name"] for m in members],
                "expense_count": expense_count,
            })
        return result
    finally:
        db.close()


@app.post("/api/groups", status_code=201)
def create_group(req: CreateGroupRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Group name cannot be empty")
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO groups (name, created_by) VALUES (?, ?)", (name, req.created_by)
        )
        group_id = cur.lastrowid
        # Auto-add creator as first member
        db.execute(
            "INSERT OR IGNORE INTO members (group_id, name) VALUES (?, ?)",
            (group_id, req.created_by)
        )
        log_activity(db, group_id, req.created_by, "created_group", f"Created group '{name}'")
        db.commit()
        return {"id": group_id, "name": name, "created_by": req.created_by, "members": [req.created_by]}
    finally:
        db.close()


@app.delete("/api/groups/{group_id}")
def delete_group(group_id: int):
    db = get_db()
    try:
        g = db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not g:
            raise HTTPException(404, "Group not found")
        db.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ── Members ────────────────────────────────────────────────────────────────────

@app.post("/api/groups/{group_id}/members", status_code=201)
def add_member(group_id: int, req: AddMemberRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Member name cannot be empty")
    db = get_db()
    try:
        g = db.execute("SELECT id, name FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not g:
            raise HTTPException(404, "Group not found")
        try:
            db.execute("INSERT INTO members (group_id, name) VALUES (?, ?)", (group_id, name))
        except Exception:
            raise HTTPException(409, f"'{name}' is already in this group")
        log_activity(db, group_id, name, "added_member", f"'{name}' joined the group")
        db.commit()
        return {"name": name}
    finally:
        db.close()


@app.delete("/api/groups/{group_id}/members/{member_name}")
def remove_member(group_id: int, member_name: str):
    db = get_db()
    try:
        r = db.execute(
            "DELETE FROM members WHERE group_id = ? AND name = ?", (group_id, member_name)
        )
        if r.rowcount == 0:
            raise HTTPException(404, "Member not found")
        log_activity(db, group_id, member_name, "removed_member", f"'{member_name}' left the group")
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ── Expenses ───────────────────────────────────────────────────────────────────

def _fetch_expenses(db, group_id: int):
    expenses = db.execute(
        "SELECT * FROM expenses WHERE group_id = ? ORDER BY created_at DESC", (group_id,)
    ).fetchall()
    result = []
    for e in expenses:
        splits = db.execute(
            "SELECT member_name, share FROM expense_splits WHERE expense_id = ?", (e["id"],)
        ).fetchall()
        result.append({
            **row_to_dict(e),
            "splits": [{"member_name": s["member_name"], "share": s["share"]} for s in splits],
        })
    return result


@app.get("/api/groups/{group_id}/expenses")
def list_expenses(group_id: int):
    db = get_db()
    try:
        if not db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone():
            raise HTTPException(404, "Group not found")
        return _fetch_expenses(db, group_id)
    finally:
        db.close()


@app.post("/api/groups/{group_id}/expenses/parse")
def parse_only(group_id: int, req: ParseRequest):
    """Parse natural language — returns parsed data but does NOT save."""
    db = get_db()
    try:
        if not db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone():
            raise HTTPException(404, "Group not found")
        members = [r["name"] for r in db.execute(
            "SELECT name FROM members WHERE group_id = ?", (group_id,)
        ).fetchall()]
        default_user = req.default_user.strip() or (members[0] if members else "Me")
        try:
            parsed = parse_expense(req.text, default_user, members)
        except ParseWarning as e:
            return {"warning": str(e), "parsed": None}
        if parsed["amount"] <= 0:
            raise HTTPException(400, "Could not extract a valid amount")
        return {"parsed": parsed}
    finally:
        db.close()


@app.post("/api/groups/{group_id}/expenses", status_code=201)
def save_expense(group_id: int, req: SaveExpenseRequest):
    """Save a confirmed expense (after preview)."""
    if req.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    if not req.participants:
        raise HTTPException(400, "At least one participant required")

    share = round(req.amount / len(req.participants), 2)

    db = get_db()
    try:
        if not db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone():
            raise HTTPException(404, "Group not found")

        cur = db.execute(
            """INSERT INTO expenses (group_id, description, amount, category, paid_by, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (group_id, req.description, req.amount, req.category, req.paid_by, req.created_by)
        )
        expense_id = cur.lastrowid

        for p in req.participants:
            db.execute(
                "INSERT INTO expense_splits (expense_id, member_name, share) VALUES (?, ?, ?)",
                (expense_id, p, share)
            )

        log_activity(db, group_id, req.created_by, "added_expense",
                     f"Added '{req.description}' ₹{req.amount:,.2f} (paid by {req.paid_by})")
        db.commit()

        splits = db.execute(
            "SELECT member_name, share FROM expense_splits WHERE expense_id = ?", (expense_id,)
        ).fetchall()
        expense = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        return {**row_to_dict(expense), "splits": [dict(s) for s in splits]}
    finally:
        db.close()


@app.put("/api/groups/{group_id}/expenses/{expense_id}")
def update_expense(group_id: int, expense_id: int, req: UpdateExpenseRequest):
    if req.amount <= 0:
        raise HTTPException(400, "Amount must be positive")
    if not req.participants:
        raise HTTPException(400, "At least one participant required")

    share = round(req.amount / len(req.participants), 2)

    db = get_db()
    try:
        e = db.execute(
            "SELECT id FROM expenses WHERE id = ? AND group_id = ?", (expense_id, group_id)
        ).fetchone()
        if not e:
            raise HTTPException(404, "Expense not found")

        db.execute(
            """UPDATE expenses SET description=?, amount=?, category=?, paid_by=?,
               updated_at=datetime('now') WHERE id=?""",
            (req.description, req.amount, req.category, req.paid_by, expense_id)
        )
        db.execute("DELETE FROM expense_splits WHERE expense_id = ?", (expense_id,))
        for p in req.participants:
            db.execute(
                "INSERT INTO expense_splits (expense_id, member_name, share) VALUES (?, ?, ?)",
                (expense_id, p, share)
            )

        actor = req.updated_by or req.paid_by
        log_activity(db, group_id, actor, "edited_expense",
                     f"Edited '{req.description}' ₹{req.amount:,.2f}")
        db.commit()

        splits = db.execute(
            "SELECT member_name, share FROM expense_splits WHERE expense_id = ?", (expense_id,)
        ).fetchall()
        expense = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        return {**row_to_dict(expense), "splits": [dict(s) for s in splits]}
    finally:
        db.close()


@app.delete("/api/groups/{group_id}/expenses/{expense_id}")
def delete_expense(group_id: int, expense_id: int, user: str = Query(default="")):
    db = get_db()
    try:
        e = db.execute(
            "SELECT * FROM expenses WHERE id = ? AND group_id = ?", (expense_id, group_id)
        ).fetchone()
        if not e:
            raise HTTPException(404, "Expense not found")
        actor = user or e["created_by"]
        db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        log_activity(db, group_id, actor, "deleted_expense",
                     f"Deleted '{e['description']}' ₹{e['amount']:,.2f}")
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ── Settlement ─────────────────────────────────────────────────────────────────

@app.get("/api/groups/{group_id}/settle")
def settle(group_id: int):
    db = get_db()
    try:
        if not db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone():
            raise HTTPException(404, "Group not found")
        expenses = _fetch_expenses(db, group_id)
        balances = calculate_balances(expenses)
        transactions = simplify_debts(balances)
        return {
            "balances": [{"name": k, "balance": v} for k, v in sorted(balances.items())],
            "transactions": transactions,
        }
    finally:
        db.close()


# ── Activity ───────────────────────────────────────────────────────────────────

@app.get("/api/groups/{group_id}/activity")
def get_activity(group_id: int):
    db = get_db()
    try:
        if not db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone():
            raise HTTPException(404, "Group not found")
        rows = db.execute(
            "SELECT * FROM activity_log WHERE group_id = ? ORDER BY created_at DESC LIMIT 100",
            (group_id,)
        ).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        db.close()


# ── Dashboard ──────────────────────────────────────────────────────────────────

@app.get("/api/groups/{group_id}/dashboard")
def dashboard(group_id: int):
    db = get_db()
    try:
        if not db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone():
            raise HTTPException(404, "Group not found")

        expenses = db.execute(
            "SELECT * FROM expenses WHERE group_id = ?", (group_id,)
        ).fetchall()

        total = round(sum(e["amount"] for e in expenses), 2)

        # Category breakdown
        cat_totals: dict = {}
        for e in expenses:
            cat = e["category"] or "general"
            cat_totals[cat] = round(cat_totals.get(cat, 0.0) + e["amount"], 2)

        # Per-person spending (as payer)
        person_totals: dict = {}
        for e in expenses:
            p = e["paid_by"]
            person_totals[p] = round(person_totals.get(p, 0.0) + e["amount"], 2)

        top_spender = max(person_totals, key=person_totals.get) if person_totals else None

        return {
            "total_spent": total,
            "expense_count": len(expenses),
            "category_breakdown": [{"category": k, "amount": v} for k, v in cat_totals.items()],
            "person_totals": [{"name": k, "amount": v} for k, v in sorted(person_totals.items(), key=lambda x: -x[1])],
            "top_spender": top_spender,
        }
    finally:
        db.close()


# ── Friends (cross-group balances) ─────────────────────────────────────────────

@app.get("/api/friends/balances")
def friends_balances(user: str = Query(...)):
    """Return per-friend net balances aggregated across all groups the user belongs to."""
    db = get_db()
    try:
        groups = db.execute("""
            SELECT DISTINCT g.id, g.name FROM groups g
            LEFT JOIN members m ON m.group_id = g.id
            WHERE m.name = ? OR g.created_by = ?
        """, (user, user)).fetchall()

        groups_data = []
        for g in groups:
            expenses = _fetch_expenses(db, g["id"])
            groups_data.append({
                "group_id": g["id"],
                "group_name": g["name"],
                "expenses": expenses,
            })

        friend_map = aggregate_friend_balances(user, groups_data)

        friends_list = []
        total_owed_to_you = 0.0
        total_you_owe = 0.0

        for name, data in friend_map.items():
            friends_list.append({
                "name": name,
                "net_balance": data["net_balance"],
                "groups": data["groups"],
            })
            if data["net_balance"] > 0.005:
                total_owed_to_you = round(total_owed_to_you + data["net_balance"], 2)
            elif data["net_balance"] < -0.005:
                total_you_owe = round(total_you_owe + abs(data["net_balance"]), 2)

        # Sort: biggest creditor first, then biggest debtor
        friends_list.sort(key=lambda x: -x["net_balance"])

        return {
            "total_owed_to_you": total_owed_to_you,
            "total_you_owe": total_you_owe,
            "net_balance": round(total_owed_to_you - total_you_owe, 2),
            "friends": friends_list,
        }
    finally:
        db.close()


@app.post("/api/settle", status_code=201)
def settle_between_friends(req: SettleRequest):
    """
    Record a settlement payment: payer → payee.
    Creates an expense where payer fronts the full amount and payee owes all of it,
    which correctly cancels out any existing debt between the two.
    """
    if req.amount <= 0:
        raise HTTPException(400, "Amount must be positive")

    db = get_db()
    try:
        g = db.execute("SELECT id, name FROM groups WHERE id = ?", (req.group_id,)).fetchone()
        if not g:
            raise HTTPException(404, "Group not found")

        # Ensure both payer and payee are members of the group (add if needed)
        for person in [req.payer, req.payee]:
            db.execute(
                "INSERT OR IGNORE INTO members (group_id, name) VALUES (?, ?)",
                (req.group_id, person)
            )

        # paid_by = payer, only payee owes the share — this cancels the debt
        desc = f"Settlement: {req.payer} → {req.payee}"
        cur = db.execute(
            """INSERT INTO expenses (group_id, description, amount, category, paid_by, created_by)
               VALUES (?, ?, ?, 'settlement', ?, ?)""",
            (req.group_id, desc, req.amount, req.payer, req.payer)
        )
        expense_id = cur.lastrowid

        db.execute(
            "INSERT INTO expense_splits (expense_id, member_name, share) VALUES (?, ?, ?)",
            (expense_id, req.payee, req.amount)
        )

        log_activity(db, req.group_id, req.payer, "settled_up",
                     f"{req.payer} paid {req.payee} ₹{req.amount:,.2f}")
        db.commit()

        return {"ok": True, "expense_id": expense_id, "group_name": g["name"]}
    finally:
        db.close()
