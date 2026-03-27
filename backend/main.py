import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Load .env before anything else
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from models import get_db, init_db
from parser import parse_expense
from splitter import calculate_balances, simplify_debts


# --- App setup ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="SmartSplit", lifespan=lifespan)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

# Serve frontend static files
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def serve_frontend():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


# --- Pydantic models ---

class CreateGroupRequest(BaseModel):
    name: str

class AddMemberRequest(BaseModel):
    name: str

class ParseExpenseRequest(BaseModel):
    text: str
    default_user: str = ""

class ManualExpenseRequest(BaseModel):
    description: str
    amount: float
    paid_by: str
    participants: list[str]
    category: str = "general"


# --- Group endpoints ---

@app.get("/api/groups")
def list_groups():
    db = get_db()
    try:
        groups = db.execute("SELECT * FROM groups ORDER BY created_at DESC").fetchall()
        result = []
        for g in groups:
            members = db.execute(
                "SELECT name FROM members WHERE group_id = ? ORDER BY name", (g["id"],)
            ).fetchall()
            result.append({
                "id": g["id"],
                "name": g["name"],
                "created_at": g["created_at"],
                "members": [m["name"] for m in members],
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
        cursor = db.execute("INSERT INTO groups (name) VALUES (?)", (name,))
        db.commit()
        group_id = cursor.lastrowid
        return {"id": group_id, "name": name, "members": []}
    finally:
        db.close()


@app.delete("/api/groups/{group_id}")
def delete_group(group_id: int):
    db = get_db()
    try:
        group = db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "Group not found")
        db.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# --- Member endpoints ---

@app.post("/api/groups/{group_id}/members", status_code=201)
def add_member(group_id: int, req: AddMemberRequest):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Member name cannot be empty")
    db = get_db()
    try:
        group = db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "Group not found")
        try:
            db.execute("INSERT INTO members (group_id, name) VALUES (?, ?)", (group_id, name))
            db.commit()
        except Exception:
            raise HTTPException(409, f"Member '{name}' already exists in this group")
        return {"name": name}
    finally:
        db.close()


@app.delete("/api/groups/{group_id}/members/{member_name}")
def remove_member(group_id: int, member_name: str):
    db = get_db()
    try:
        result = db.execute(
            "DELETE FROM members WHERE group_id = ? AND name = ?", (group_id, member_name)
        )
        db.commit()
        if result.rowcount == 0:
            raise HTTPException(404, "Member not found")
        return {"ok": True}
    finally:
        db.close()


# --- Expense endpoints ---

@app.get("/api/groups/{group_id}/expenses")
def list_expenses(group_id: int):
    db = get_db()
    try:
        group = db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "Group not found")

        expenses = db.execute(
            "SELECT * FROM expenses WHERE group_id = ? ORDER BY created_at DESC", (group_id,)
        ).fetchall()

        result = []
        for e in expenses:
            splits = db.execute(
                "SELECT member_name, share FROM expense_splits WHERE expense_id = ?", (e["id"],)
            ).fetchall()
            result.append({
                "id": e["id"],
                "description": e["description"],
                "amount": e["amount"],
                "category": e["category"],
                "paid_by": e["paid_by"],
                "created_at": e["created_at"],
                "splits": [{"member_name": s["member_name"], "share": s["share"]} for s in splits],
            })
        return result
    finally:
        db.close()


@app.post("/api/groups/{group_id}/expenses/parse", status_code=201)
def parse_and_add_expense(group_id: int, req: ParseExpenseRequest):
    db = get_db()
    try:
        group = db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "Group not found")

        members = db.execute(
            "SELECT name FROM members WHERE group_id = ?", (group_id,)
        ).fetchall()
        group_members = [m["name"] for m in members]

        default_user = req.default_user.strip() or (group_members[0] if group_members else "Me")

        # Parse with AI (or fallback)
        parsed = parse_expense(req.text, default_user, group_members)

        if parsed["amount"] <= 0:
            raise HTTPException(400, "Could not extract a valid amount from the expense description")

        # Return parsed data for preview — client calls /manual to confirm
        return {"parsed": parsed}

    finally:
        db.close()


@app.post("/api/groups/{group_id}/expenses/manual", status_code=201)
def add_expense_manual(group_id: int, req: ManualExpenseRequest):
    db = get_db()
    try:
        group = db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "Group not found")

        if req.amount <= 0:
            raise HTTPException(400, "Amount must be positive")
        if not req.paid_by.strip():
            raise HTTPException(400, "paid_by is required")
        if not req.participants:
            raise HTTPException(400, "At least one participant is required")

        # Calculate equal split
        share = round(req.amount / len(req.participants), 2)

        cursor = db.execute(
            "INSERT INTO expenses (group_id, description, amount, category, paid_by) VALUES (?, ?, ?, ?, ?)",
            (group_id, req.description, req.amount, req.category, req.paid_by)
        )
        expense_id = cursor.lastrowid

        for participant in req.participants:
            db.execute(
                "INSERT INTO expense_splits (expense_id, member_name, share) VALUES (?, ?, ?)",
                (expense_id, participant, share)
            )

        db.commit()

        splits = [{"member_name": p, "share": share} for p in req.participants]
        return {
            "id": expense_id,
            "description": req.description,
            "amount": req.amount,
            "category": req.category,
            "paid_by": req.paid_by,
            "splits": splits,
        }
    finally:
        db.close()


@app.delete("/api/groups/{group_id}/expenses/{expense_id}")
def delete_expense(group_id: int, expense_id: int):
    db = get_db()
    try:
        expense = db.execute(
            "SELECT id FROM expenses WHERE id = ? AND group_id = ?", (expense_id, group_id)
        ).fetchone()
        if not expense:
            raise HTTPException(404, "Expense not found")
        db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# --- Settlement endpoint ---

@app.get("/api/groups/{group_id}/settle")
def settle_group(group_id: int):
    db = get_db()
    try:
        group = db.execute("SELECT id FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(404, "Group not found")

        expenses = db.execute(
            "SELECT * FROM expenses WHERE group_id = ?", (group_id,)
        ).fetchall()

        expenses_with_splits = []
        for e in expenses:
            splits = db.execute(
                "SELECT member_name, share FROM expense_splits WHERE expense_id = ?", (e["id"],)
            ).fetchall()
            expenses_with_splits.append({
                "paid_by": e["paid_by"],
                "amount": e["amount"],
                "splits": [{"member_name": s["member_name"], "share": s["share"]} for s in splits],
            })

        balances = calculate_balances(expenses_with_splits)
        transactions = simplify_debts(balances)

        return {
            "balances": [{"name": k, "balance": v} for k, v in sorted(balances.items())],
            "transactions": transactions,
        }
    finally:
        db.close()
