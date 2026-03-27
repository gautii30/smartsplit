# SmartSplit — Complete Codebase Explained

> A beginner-friendly walkthrough of every file, with technical depth for the developer.
> Written March 2026.

---

## Table of Contents

1. [backend/models.py — The Database](#1-backendmodelspy--the-database)
2. [backend/splitter.py — The Settlement Algorithm](#2-backendsplitterpy--the-settlement-algorithm)
3. [backend/parser.py — The AI Parser](#3-backendparserpy--the-ai-parser)
4. [backend/main.py — The API](#4-backendmainpy--the-api)
5. [frontend/index.html — The UI](#5-frontendindexhtml--the-ui)
6. [tests/test_splitter.py — The Tests](#6-teststest_splitterpy--the-tests)

---

## 1. `backend/models.py` — The Database

### For a non-technical person

Think of this file as the blueprint for a filing cabinet. Before the app can store any data — users, groups, expenses — someone has to decide which drawers exist and what goes in each drawer. `models.py` creates those drawers and labels them.

The app uses **SQLite**, which is just a single file on disk (`smartsplit.db`) that behaves like a full database. No separate database server needed.

---

### The two functions

**`get_db()`** — Opens a connection to the database file and returns it. Every time the API needs to read or write data, it calls this. Two important settings are applied:

- `row_factory = sqlite3.Row` — Makes query results behave like dictionaries (`row["name"]` instead of `row[0]`). Much easier to work with.
- `PRAGMA foreign_keys = ON` — Tells SQLite to enforce relationships between tables. Without this, SQLite silently ignores foreign key constraints.

**`init_db()`** — Runs at app startup (once). Creates all six tables if they don't already exist (`CREATE TABLE IF NOT EXISTS`), so re-running the app never wipes existing data.

---

### The six tables

#### `users`
| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER (auto) | Unique identifier for each user |
| `name` | TEXT UNIQUE | The user's display name — must be unique across all users |
| `created_at` | TEXT | Timestamp of when they first logged in |

SmartSplit has no passwords — just names. `UNIQUE` on `name` means you can't accidentally create two "Gautam" accounts.

---

#### `groups`
| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER (auto) | Unique group ID |
| `name` | TEXT | e.g. "Goa Trip", "Flat 302" |
| `created_by` | TEXT | Name of the person who made the group |
| `created_at` | TEXT | Creation timestamp |

Groups don't have a foreign key back to `users` — by design. The app is name-based, not account-based, so `created_by` is just a plain string.

---

#### `members`
| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER (auto) | Row ID |
| `group_id` | INTEGER | Which group this membership belongs to |
| `name` | TEXT | Member's name |

`UNIQUE(group_id, name)` is a **composite unique constraint** — the same name can exist in different groups, but not twice in the same group. The `ON DELETE CASCADE` foreign key means: if a group is deleted, all its member records are automatically deleted too.

---

#### `expenses`
| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER (auto) | Unique expense ID |
| `group_id` | INTEGER | Which group this expense belongs to |
| `description` | TEXT | What was bought (e.g. "Dinner at Olive") |
| `amount` | REAL | Total amount paid |
| `category` | TEXT | food / travel / groceries / etc. Defaults to "general" |
| `paid_by` | TEXT | Who fronted the money |
| `created_by` | TEXT | Who added the record (may differ from payer) |
| `created_at` | TEXT | When the expense was added |
| `updated_at` | TEXT | When it was last edited |

`paid_by` vs `created_by`: Priya might be adding an expense on behalf of Rahul who paid. `paid_by = "Rahul"`, `created_by = "Priya"`.

---

#### `expense_splits`
| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER (auto) | Row ID |
| `expense_id` | INTEGER | Which expense this split belongs to |
| `member_name` | TEXT | Person whose share this is |
| `share` | REAL | Their portion of the total (in rupees) |

This is the most important table for math. For a ₹300 dinner split 3 ways, there would be 3 rows here — one per person, each with `share = 100.0`. `ON DELETE CASCADE` means deleting an expense automatically deletes all its split rows.

---

#### `activity_log`
| Column | Type | Purpose |
|--------|------|---------|
| `id` | INTEGER (auto) | Row ID |
| `group_id` | INTEGER | Which group this event belongs to |
| `user_name` | TEXT | Who performed the action |
| `action` | TEXT | Machine-readable action code (e.g. "added_expense") |
| `details` | TEXT | Human-readable description |
| `created_at` | TEXT | When it happened |

Every meaningful action (adding an expense, removing a member, settling up) writes a row here. The Activity tab in the UI reads from this table to show a timeline.

---

### Interesting design decisions

- **No passwords**: Authentication is just "enter your name." The name is stored in `localStorage` on the browser for auto-login. This keeps the app simple but means it's not secure for real money.
- **Cascade deletes everywhere**: Deleting a group wipes members, expenses, splits, and activity in one shot. No orphaned rows.
- **`updated_at` defaults to `datetime('now')` at insert time**: This means it starts equal to `created_at` and is only different after an edit. SQLite doesn't have auto-update triggers here — the `UPDATE` SQL in `main.py` manually sets it.

---

## 2. `backend/splitter.py` — The Settlement Algorithm

### For a non-technical person

Imagine three friends went on a trip. Alice paid for the hotel, Bob paid for food. At the end, everyone needs to figure out who owes whom. You don't want 10 separate payments going back and forth — you want the fewest possible transactions to make everyone whole.

This file does two things:
1. Figures out each person's **net balance** (how much they're owed or owe in total)
2. **Simplifies the debts** into the minimum number of payments

---

### `calculate_balances(expenses)` — Step-by-step with an example

**Setup**: Three friends — Alice, Bob, Charlie.

- Expense 1: Alice paid ₹300 for dinner. Split equally among all 3. Each owes ₹100.
- Expense 2: Bob paid ₹150 for cab. Split equally among all 3. Each owes ₹50.

**How the function works:**

For each expense, it does two things:
1. **Credits** the payer the full amount they paid
2. **Debits** each participant their share

```
After Expense 1 (Alice paid 300, each share = 100):
  Alice:  +300 (credit) - 100 (her own share) = +200
  Bob:    - 100
  Charlie: - 100

After Expense 2 (Bob paid 150, each share = 50):
  Alice:  +200 - 50 = +150
  Bob:    -100 + 150 (credit) - 50 = 0
  Charlie: -100 - 50 = -150
```

**Final balances:**
- Alice: `+150` (is owed ₹150)
- Bob: `0` (perfectly even)
- Charlie: `−150` (owes ₹150)

> **Key insight**: Positive balance = this person fronted more than their share → they are owed money. Negative balance = they consumed more than they paid → they owe money.

---

### `simplify_debts(balances)` — The greedy algorithm

Once we have net balances, we need to figure out who pays whom. The goal is the **fewest transactions**.

**How it works:**

1. Split people into two lists: **debtors** (negative balance) and **creditors** (positive balance)
2. Sort both lists by absolute amount, largest first
3. Match the biggest debtor to the biggest creditor
4. Settle `min(debt, credit)` in one transaction
5. Subtract that amount from both. Remove whoever hit zero. Repeat.

**Example continued** (Alice: +150, Charlie: −150):

- Biggest debtor: Charlie owes 150
- Biggest creditor: Alice is owed 150
- Settled = min(150, 150) = 150
- Result: **Charlie pays Alice ₹150** — done in one transaction!

**A more complex example** (from the tests):
- Alice: +60, Bob: +40, Charlie: −100

Round 1:
- Biggest debtor: Charlie (100), Biggest creditor: Alice (60)
- Settle 60 → Charlie pays Alice ₹60
- Remaining: Alice: 0, Bob: +40, Charlie: −40

Round 2:
- Biggest debtor: Charlie (40), Biggest creditor: Bob (40)
- Settle 40 → Charlie pays Bob ₹40

**Result**: 2 transactions instead of a possible 3 or more.

---

### `aggregate_friend_balances(user, groups_data)` — The Friends view math

This function is **intentionally different** from `simplify_debts`. Here's why:

`simplify_debts` is great for a group's "Settle Up" tab — it minimizes total transactions. But it does this by **rerouting debts through third parties**. Example: if Gautam owes Priya ₹100 and Rahul owes Priya ₹100, the greedy algorithm might say "Gautam, just pay Rahul directly." This makes Rahul disappear from Gautam's Friends view entirely, which is confusing and wrong.

Instead, `aggregate_friend_balances` uses **direct pairwise accounting**:

For each expense across all groups:
- If **the user paid**: every other participant in that expense owes the user their share. Add that to the friend's balance.
- If **someone else paid**: if the user is a participant, the user owes the payer their own share. Subtract from that friend's balance.

The function also tracks **per-group breakdowns** so the Friends view can show "Rahul owes you ₹50 from Goa Trip, ₹30 from Flat 302."

**Why `_pairwise_add` is a separate helper**: The accumulation logic (create the friend entry if it doesn't exist, create the group sub-entry if it doesn't exist, add the amount) happens many times in a loop, so it's extracted to avoid repetition.

---

### Edge cases & interesting decisions

- **EPSILON = 0.005**: Floating-point arithmetic causes tiny errors (e.g. ₹33.33 × 3 = ₹99.99, not ₹100). Any balance smaller than half a paisa is treated as zero.
- **`round(..., 2)` everywhere**: All calculations round to 2 decimal places at each step to prevent error accumulation over many expenses.
- **Why not use `simplify_debts` for friends**: The greedy algorithm's chain-rerouting collapses multi-party debts in ways that make individual friends invisible. Pairwise accounting preserves the actual bilateral relationships.

---

## 3. `backend/parser.py` — The AI Parser

### For a non-technical person

This is the "magic" file. When you type `"Priya paid 1500 for dinner, split with Rahul and me"` into the app, something has to understand that sentence and turn it into structured data: who paid, how much, what for, who splits it.

This file does that in two ways:
1. **The smart way (Gemini)**: Sends the sentence to Google's AI and asks it to fill out a structured form
2. **The fallback way (regex)**: If the AI is unavailable, tries to extract the information using pattern matching rules

---

### The `ParseWarning` exception

Before any parsing even starts, the code checks one thing: if you explicitly named people to split with ("split with Rahul and Priya"), are those names actually in the group?

If not, instead of silently splitting with everyone (which would be wrong), the code raises a `ParseWarning`. This is a **custom exception class** — it's intentionally distinct from regular Python exceptions so that the API endpoint can catch it specifically and return a user-facing warning message instead of a server error.

**Why raise before calling Gemini?** Calling the AI costs money (API quota). If the input is already invalid, there's no point wasting it.

---

### The four helper functions (shared logic)

These four functions work together like a pipeline:

**`_extract_explicit_split_clause(text)`**

Uses a regex to find patterns like "split with X and Y", "split between A and B", "split among A, B, C". Returns just the names part, or `None` if no explicit split was mentioned.

```
Input:  "Priya paid 1500 for dinner, split with Rahul and me"
Output: "Rahul and me"
```

**`_tokenise_names(clause, default_user)`**

Splits the clause on "and" and commas. Replaces "me" or "I" with the actual user's name.

```
Input:  "Rahul and me", default_user="Gautam"
Output: ["Rahul", "Gautam"]
```

**`_validate_explicit_names(tokens, group_members, default_user)`**

For each name token, tries to find a matching group member using `_fuzzy_match`. Returns two lists: matched names and unmatched names.

```
tokens = ["Rahul", "Aryan"]
group_members = ["Gautam", "Rahul Singh", "Priya"]
→ matched = ["Rahul Singh"]   (substring match: "Rahul" in "Rahul Singh")
→ unmatched = ["Aryan"]       (no match found)
```

**`_fuzzy_match(name, members)`**

Tries three levels of matching in order:
1. Exact match (case-insensitive): "rahul" == "Rahul"
2. Substring match: "rahul" is in "Rahul Singh" OR "rahul singh" contains "rahul"
3. Returns `None` if nothing matched

**`_check_explicit_split(text, default_user, group_members)`**

Orchestrates all the above. Returns:
- `None` if no explicit split was mentioned (caller should default to all members)
- A list of matched member names if an explicit split was found and all names matched
- Raises `ParseWarning` if any named person couldn't be matched

---

### `parse_expense()` — The primary (Gemini) path

```
1. Check for explicit split names (raises ParseWarning if invalid)
2. If no API key configured → use fallback instead
3. Build a detailed prompt and send to Gemini 2.0 Flash
4. Strip markdown fences from the response (Gemini sometimes wraps JSON in ```json blocks)
5. Parse the JSON response
6. Validate all required fields exist
7. Fuzzy-match all names against actual group members (fix capitalisation, etc.)
8. Ensure the payer is always included in participants
9. Return the structured result
```

**The Gemini prompt** is carefully engineered with numbered rules. The critical ones:

- Rule 1: "If a specific person's name appears as payer... set `paid_by` to THAT person, NOT to the logged-in user." (Common failure: AI defaults to assuming the logged-in user always pays.)
- Rule 2a: "If the sentence explicitly names who to split with, ONLY include those people." (Common failure: AI adds everyone in the group even when only some people were mentioned.)
- Rule 7: "amount must be a plain number (no ₹ or Rs symbols)." (Common failure: AI returns `"amount": "₹1500"` instead of `1500`.)

**If anything goes wrong** (network error, JSON parse failure, missing fields, negative amount), the `except Exception` block catches it and calls `parse_expense_fallback`. **Exception**: `ParseWarning` is raised *before* the try/except block, so it always propagates up to the caller and is never swallowed.

---

### `parse_expense_fallback()` — The regex path

When Gemini isn't available or fails, this function takes over. It's less intelligent but works offline:

1. **Amount**: Finds the first number in the text using `\b(\d+(?:[.,]\d+)?)\b`
2. **Category**: Scans the text for keywords from `CATEGORY_KEYWORDS` (e.g. "pizza" → food, "uber" → travel)
3. **Payer**: Looks for patterns like `"<Name> paid"`, `"<Name> spent"`, `"paid by <Name>"`. Defaults to the logged-in user.
4. **Participants**: Uses the `explicit_participants` result from `_check_explicit_split` if available, otherwise defaults to all group members.
5. **Description**: Takes the original text and strips out numbers, leaving the descriptive words.

---

### What was AI-generated vs hand-written

| Part | Origin |
|------|--------|
| `ParseWarning` class and all `_check_explicit_split` helpers | Hand-written — this is pure Python business logic |
| The Gemini API call and prompt text | Hand-written prompt, Gemini API library |
| Regex for amount extraction | Hand-written |
| `CATEGORY_KEYWORDS` dictionary | Hand-written |
| `_fuzzy_match` | Hand-written |
| The actual parsing of expense sentences | **Google Gemini 2.0 Flash** (the AI model) |

---

## 4. `backend/main.py` — The API

### For a non-technical person

This is the "front desk" of the backend. The browser sends requests like "give me all expenses for group 5" or "add this new expense," and `main.py` handles them — pulling from the database, calling the parser, running calculations, and sending back the answer.

It uses **FastAPI**, a Python library that makes building these request handlers easy. Every function decorated with `@app.get(...)` or `@app.post(...)` is an endpoint — a URL the browser can call.

---

### App startup

```python
@asynccontextmanager
async def lifespan(app):
    init_db()   # create tables if they don't exist
    yield       # app runs here
```

`init_db()` is called once when the server starts. The `yield` is FastAPI's way of saying "after setup, run the app until it shuts down."

---

### Request models (Pydantic)

These are Python classes that define the shape of incoming JSON data. FastAPI automatically validates the request body against them — if a required field is missing or the wrong type, it returns a 422 error before your code even runs.

| Class | Used for |
|-------|---------|
| `LoginRequest` | POST /auth/login |
| `CreateGroupRequest` | POST /groups |
| `AddMemberRequest` | POST /groups/{id}/members |
| `ParseRequest` | POST /groups/{id}/expenses/parse |
| `SaveExpenseRequest` | POST /groups/{id}/expenses |
| `UpdateExpenseRequest` | PUT /groups/{id}/expenses/{id} |
| `SettleRequest` | POST /settle |

---

### Every API endpoint

#### Auth

**`POST /api/auth/login`**
- Takes: `{ name: "Gautam" }`
- Does: `INSERT OR IGNORE INTO users` — creates the user if they don't exist, does nothing if they do
- Returns: The user record `{ id, name, created_at }`
- Calls: `models.get_db()`

**`GET /api/auth/me?name=Gautam`**
- Does: Looks up the user by name
- Returns: User record or 404
- Used for: Auto-login on page refresh (checks the saved name is still valid)

---

#### Groups

**`GET /api/groups?user=Gautam`**
- Does: Finds all groups where Gautam is a member OR created the group (using `LEFT JOIN` + `OR`). For each group, fetches member names and expense count.
- Returns: List of groups with `members` array and `expense_count`

**`POST /api/groups`**
- Takes: `{ name: "Goa Trip", created_by: "Gautam" }`
- Does: Creates the group, auto-adds creator as first member, logs "created_group" to activity
- Returns: New group with 201 status

**`DELETE /api/groups/{group_id}`**
- Does: Deletes the group. Because of `ON DELETE CASCADE`, all members, expenses, splits, and activity are deleted automatically by SQLite.
- Returns: `{ ok: true }`

---

#### Members

**`POST /api/groups/{group_id}/members`**
- Takes: `{ name: "Priya" }`
- Does: Inserts into `members`. If the name already exists (violates `UNIQUE(group_id, name)`), catches the exception and returns 409 Conflict.
- Returns: `{ name: "Priya" }` with 201

**`DELETE /api/groups/{group_id}/members/{member_name}`**
- Does: Removes the member. If they weren't in the group, returns 404.

---

#### Expenses

**`GET /api/groups/{group_id}/expenses`**
- Does: Fetches all expenses for the group, ordered newest-first. For each expense, also fetches its splits from `expense_splits`.
- Calls: `_fetch_expenses(db, group_id)` — a shared helper used by multiple endpoints
- Returns: List of expense objects each with a `splits` array

**`POST /api/groups/{group_id}/expenses/parse`** ← Uses AI
- Takes: `{ text: "...", default_user: "Gautam" }`
- Does: Calls `parse_expense()` from `parser.py`. Does NOT save anything.
- If `ParseWarning` is raised: returns `{ warning: "...", parsed: null }` — a soft warning, not an error
- If parsing succeeds: returns `{ parsed: { description, amount, paid_by, participants, category } }`
- If amount ≤ 0: returns 400 error
- Calls: `parser.parse_expense()`

**`POST /api/groups/{group_id}/expenses`**
- Takes: The full expense fields (from the confirm step after preview)
- Does: Saves the expense, calculates equal share per participant, saves splits, logs to activity
- Returns: The saved expense with its splits

**`PUT /api/groups/{group_id}/expenses/{expense_id}`**
- Does: Updates description/amount/category/paid_by. Deletes all old split rows and recreates them (simpler than diffing).

**`DELETE /api/groups/{group_id}/expenses/{expense_id}`**
- Does: Deletes the expense (cascade removes its splits), logs to activity

---

#### Settlement

**`GET /api/groups/{group_id}/settle`**
- Does: Fetches all expenses, runs `calculate_balances()`, runs `simplify_debts()`
- Returns: `{ balances: [...], transactions: [...] }`
- Calls: `splitter.calculate_balances()`, `splitter.simplify_debts()`

---

#### Activity

**`GET /api/groups/{group_id}/activity`**
- Returns: Last 100 activity log entries for the group, newest first

---

#### Dashboard

**`GET /api/groups/{group_id}/dashboard`**
- Does: Computes total spent, category breakdown (total per category), per-person spending (total paid per person), and who the top spender is
- All computed in Python from a single `SELECT * FROM expenses WHERE group_id = ?`
- Returns: `{ total_spent, expense_count, category_breakdown, person_totals, top_spender }`

---

#### Friends

**`GET /api/friends/balances?user=Gautam`**
- Does: Finds all groups Gautam belongs to, fetches all expenses for each, calls `aggregate_friend_balances()`
- Returns: `{ total_owed_to_you, total_you_owe, net_balance, friends: [...] }`
- Calls: `splitter.aggregate_friend_balances()`

**`POST /api/settle`**
- Takes: `{ payer, payee, amount, group_id }`
- Does: Records a settlement payment by creating a special expense where `paid_by = payer` and only `payee` is in the splits with the full amount as their share. This mathematically cancels the debt.
- Why this works: If Priya was owed ₹500 (positive balance), and Bob "pays" her ₹500 using this mechanism, Bob gets a +500 credit (he's now the payer) and Priya gets a −500 debit (she's the only participant). Net effect: both go to zero.

---

### Shared helpers

**`log_activity(db, group_id, user_name, action, details)`** — Inserts a row into `activity_log`. Called by every endpoint that changes data.

**`row_to_dict(row)`** — Converts a `sqlite3.Row` object to a plain `dict` so FastAPI can serialize it to JSON.

**`_fetch_expenses(db, group_id)`** — Fetches expenses with their splits in one place. Used by `list_expenses`, `settle`, and `friends_balances` to avoid duplicating the join logic.

---

## 5. `frontend/index.html` — The UI

### For a non-technical person

The entire user interface is one file. Everything you see — the login screen, the sidebar, the expense cards, the charts — is defined here. The browser downloads this file once and then talks to the backend API to get and save data.

Think of it as three layers in one file:
- **HTML** (lines 1–551): The skeleton — buttons, text boxes, panels
- **CSS** (lines 11–347): The styling — colors, fonts, layout
- **JavaScript** (lines 553–1286): The brain — reacts to clicks, calls the API, updates the display

---

### The HTML structure

```
<body>
  #login-screen          ← Full-page login card (hidden after login)
  #app                   ← The main app (hidden before login)
    .navbar              ← Top bar: logo, Friends/Groups tabs, theme toggle, user pill
    .app-body
      .sidebar           ← Left panel: group list + "New group" button
      .main
        #friends-view    ← Cross-group balance summary + friend cards
        #welcome-screen  ← "Select or create a group" placeholder
        #group-view      ← Active group: header + 4 tabs
          .g-header      ← Group name + member chips
          .tabs          ← Expenses / Settle Up / Dashboard / Activity
          .tab-content
            #panel-expenses   ← AI input, parse warning, preview, expense list
            #panel-settle     ← Balance cards + transaction cards
            #panel-dashboard  ← Stats grid + two Chart.js charts
            #panel-activity   ← Timeline of events
  #modal-group           ← "Create Group" modal
  #modal-member          ← "Add Member" modal
  #modal-settle-friend   ← "Record Settlement" modal
  #toast                 ← Floating notification (bottom-right)
```

---

### The CSS design system

Rather than hard-coding colors, the CSS uses **custom properties** (CSS variables) defined in `:root`. This makes theming trivial:

```css
:root {
  --accent: #5C6BC0;   /* indigo */
  --green: #2E7D32;
  --red: #C62828;
  --surface: #ffffff;
  ...
}
[data-theme="dark"] {
  --accent: #7986CB;
  --surface: #16213e;
  ...
}
```

Switching from light to dark mode is one line: `document.documentElement.setAttribute('data-theme', 'dark')`. Every element that uses `var(--surface)` automatically updates.

**Category badges** have separate color classes (`cat-food`, `cat-travel`, etc.) with dark-mode overrides that swap the pastel backgrounds for deep, saturated versions that are visible on dark backgrounds.

---

### The JavaScript application state

All runtime data lives in one object `S`:

```javascript
let S = {
  user: null,           // logged-in user object
  groups: [],           // all groups the user belongs to
  activeGid: null,      // which group is currently open
  expenses: [],         // expenses for the active group
  parsed: null,         // last AI-parsed expense (pending confirmation)
  catChart: null,       // Chart.js instance for category doughnut
  perChart: null,       // Chart.js instance for per-person bar chart
  settleTarget: null,   // friend being settled with (for modal)
  activeView: 'friends' // 'friends' or 'groups'
};
```

Having one central state object means any function can read the current state without passing arguments everywhere.

---

### The `api()` function — how frontend talks to backend

```javascript
async function api(method, path, body) {
  const opts = {method, headers: {}};
  if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const res = await fetch('/api' + path, opts);
  if (!res.ok) {
    const e = await res.json().catch(() => ({detail: res.statusText}));
    throw new Error(e.detail || res.statusText);
  }
  return res.status === 204 ? null : res.json();
}
```

Every backend call goes through this one function. It:
1. Prepends `/api` to every path
2. Sets `Content-Type: application/json` when there's a body
3. Throws a JavaScript `Error` with the server's error message if the response isn't OK (so `catch(e)` blocks get meaningful messages)
4. Returns `null` for 204 No Content responses (used by some DELETEs)

---

### The main views and how they work

#### Login flow

1. Page loads → check `localStorage.getItem('ss_user')`
2. If a name is saved → call `GET /api/auth/me?name=...` to verify it still exists
3. If valid → call `bootApp()` (skip login screen)
4. `bootApp()` hides the login screen, shows the app, and calls `loadGroups()` + `switchView('friends')`

#### Friends view

- `loadFriends()` calls `GET /api/friends/balances?user=...`
- Each friend card is built by `renderFriendCard(f)` which returns an HTML string
- Clicking a friend card toggles `toggleFriendBreakdown()` to show/hide per-group breakdown
- "Settle Up" button opens `openSettleFriend()` which pre-fills the modal with the friend's balance and their shared groups

#### Groups view (sidebar + tabs)

- `loadGroups()` fetches the group list and calls `renderSidebar()` to rebuild the sidebar HTML
- Clicking a group calls `selectGroup(id)` which shows the group header, resets to the Expenses tab, and calls `loadExpenses()`
- Each of the 4 tabs is lazy-loaded: `loadSettle()`, `loadDashboard()`, `loadActivity()` are only called when you click their tab button

#### The AI parse flow

```
User types text → clicks Parse
  → doParse() called
  → hideParseWarning() + hidePreview()   ← clear stale UI
  → POST /api/groups/{id}/expenses/parse
  → if res.warning → showParseWarning(msg) → stop
  → else → S.parsed = res.parsed; showPreview(res.parsed)
  → User clicks "Confirm & Add"
  → POST /api/groups/{id}/expenses   (saves it)
  → loadExpenses()   (refresh the list)
```

The parse step is intentionally **two steps**: parse (preview only) + confirm (actually save). This lets the user verify the AI's interpretation before committing.

#### Dashboard charts

Two Chart.js instances are stored in `S.catChart` and `S.perChart`. A critical pattern: before creating new charts, the old ones must be destroyed (`S.catChart.destroy()`). Without this, Chart.js throws errors about canvas elements already being in use. The chart containers also stay permanently in the DOM — they're just hidden with `display:none` when there's no data. (An earlier bug had the code replacing the container's `innerHTML`, which destroyed the `<canvas>` elements, causing `getContext` errors on next visit.)

---

### Security note: `esc()` function

All user-supplied text rendered into HTML goes through:

```javascript
function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
```

This escapes HTML special characters, preventing **XSS attacks** — e.g., if someone's name was `<script>alert(1)</script>`, it would render as literal text rather than executing.

---

### Interesting frontend decisions

- **No framework**: No React, Vue, or Angular. Pure vanilla JavaScript. This keeps the bundle tiny (zero dependencies except Chart.js from CDN) but means all DOM manipulation is manual.
- **Single-file**: All CSS and JS are inlined in the HTML. Simpler to deploy, but the file is ~1,300 lines.
- **`me-sel` dropdown fix**: The "I/me" dropdown is populated with member names in HTML. Setting `selected` inside the `<option>` string can fail silently if the value doesn't match exactly. The fix: build all options without `selected`, then do `sel.value = S.user.name` after setting `innerHTML`. JavaScript's assignment to `.value` is reliable.
- **Sidebar count sync**: After adding/deleting expenses, calling `loadGroups()` again would make an unnecessary round-trip. Instead, `loadExpenses()` updates `S.groups[idx].expense_count` locally and calls `renderSidebar()` to refresh the display.

---

## 6. `tests/test_splitter.py` — The Tests

### For a non-technical person

Tests are small programs that check whether the code does what it's supposed to. Each test sets up a specific scenario ("Alice paid ₹100, split with Bob"), runs the code, and checks the result matches what we expect. If someone changes the code in a way that breaks something, the tests catch it.

This file tests the math in `splitter.py` — not the UI or API.

---

### Test structure

The file uses **pytest** and has three test classes, each targeting one function:

---

### `TestCalculateBalances` — 5 tests

These test `calculate_balances()` which computes net balances from a list of expenses.

| Test | Scenario | What it verifies |
|------|----------|-----------------|
| `test_simple_equal_split` | Alice pays 100, split with Bob | Alice: +50, Bob: −50 |
| `test_multiple_expenses` | Alice pays 100, Bob pays 60, both split equally | Alice: +20, Bob: −20 (cross-check both expenses cancel each other partially) |
| `test_no_expenses` | Empty list | Returns `{}` (no crash, no balances) |
| `test_single_person_pays_self` | One person, pays and owes themselves | Net 0 (degenerate case) |
| `test_rounding_three_way_split` | 100 split 3 ways (33.33 each) | Uses `pytest.approx` with 1 cent tolerance — can't expect exact floats |

---

### `TestSimplifyDebts` — 6 tests

These test `simplify_debts()` which produces the minimum-transaction settlement plan.

| Test | Scenario | What it verifies |
|------|----------|-----------------|
| `test_simple_two_person` | Bob owes Alice 50 | Exactly 1 transaction: Bob → Alice, 50 |
| `test_three_person_simplification` | Alice +60, Bob +40, Charlie −100 | Exactly 2 transactions, Charlie pays all 100 total |
| `test_chain_simplification` | A owes B, B owes C | Collapses to 1 transaction: A pays C directly |
| `test_everyone_even` | All zero balances | Empty transactions list |
| `test_complex_four_person` | 4 people with various balances | Total transferred equals total owed (conservation check) |
| `test_rounding_edge_case` | Balances from 3-way split (66.67, −33.33, −33.33) | 2 transactions, total ~66.67 |

The chain test is particularly interesting — it verifies the greedy algorithm's "debt rerouting" behavior (A→B→C collapses to A→C). This is the exact behavior that's **correct for group settle-up** but **wrong for the Friends view** (which is why `aggregate_friend_balances` was written separately).

---

### `TestAggregateFriendBalances` — 7 tests

These test `aggregate_friend_balances()` which computes cross-group pairwise balances.

| Test | Scenario | What it verifies |
|------|----------|-----------------|
| `test_single_group_friend_owes_user` | Gautam pays 100, Rahul is participant | Rahul owes Gautam 50 (net_balance = +50) |
| `test_single_group_user_owes_friend` | Priya pays 100, Gautam is participant | Gautam owes Priya 50 (net_balance = −50) |
| `test_cross_group_aggregation` | Gautam pays in two groups, Rahul in both | Balances add up (50 + 30 = 80), 2 group entries |
| `test_cross_group_mixed_directions` | Rahul pays in one group, Gautam in another | Net is correct (−100 + 150 = +50) |
| `test_no_shared_expenses_returns_empty` | User in group with no expenses | Returns `{}` |
| `test_user_not_in_group_ignored` | Group where user has no involvement | Returns `{}` |
| `test_multiple_friends_independent` | Gautam pays, Rahul and Priya both participants | Both friends tracked independently (100 each) |

---

### The `make_expense` and `_make_group` helpers

Rather than writing out the full expense dictionary structure in every test, two helpers create them:

```python
def make_expense(paid_by, amount, participants):
    share = round(amount / len(participants), 2)
    return {
        "paid_by": paid_by,
        "amount": amount,
        "splits": [{"member_name": p, "share": share} for p in participants],
    }
```

This constructs the same data structure that `_fetch_expenses()` in `main.py` returns from the database — so the tests exercise the same data format the real app uses.

---

### What's NOT tested (and why that's OK for now)

- `parser.py` — Testing the AI parser reliably would require mocking the Gemini API, which adds complexity. The regex fallback could be unit tested, but it's a secondary concern.
- `main.py` endpoints — Would require a test client (FastAPI's `TestClient`) and a test database. That's integration testing territory.
- The frontend — Would require a browser automation tool like Playwright or Selenium.

For a project at this stage, having solid tests on the core math (splitter) is the right priority — that's where bugs are hardest to spot manually and most costly to get wrong.

---

## Summary: How the files connect

```
Browser (index.html)
    │
    │  HTTP requests to /api/...
    ▼
main.py (FastAPI)
    ├── models.py     — database reads/writes
    ├── parser.py     — NL text → structured expense (via Gemini)
    └── splitter.py   — balance math & debt simplification
            ▲
            │
    test_splitter.py  — verifies splitter.py correctness
```

**The data flow for adding an expense:**

1. User types: `"Priya paid 1500 for dinner"`
2. Browser POSTs to `/api/groups/5/expenses/parse`
3. `main.py` calls `parse_expense()` in `parser.py`
4. `parser.py` calls Gemini → gets back `{description, amount, paid_by, participants, category}`
5. `main.py` returns this as a preview to the browser
6. User confirms → browser POSTs to `/api/groups/5/expenses`
7. `main.py` inserts into `expenses` and `expense_splits` tables via `models.py`
8. Browser refreshes expense list

**The data flow for Settle Up:**

1. User clicks "Settle Up" tab
2. Browser GETs `/api/groups/5/settle`
3. `main.py` calls `_fetch_expenses()` → `calculate_balances()` → `simplify_debts()`
4. Returns `{balances, transactions}` to browser
5. Browser renders balance cards and transaction arrows
