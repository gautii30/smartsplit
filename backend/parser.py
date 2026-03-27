"""
Expense parser for SmartSplit.
Primary: Google Gemini 2.0 Flash
Fallback: regex-based parser
"""

import re
import os
import json
import logging

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "food", "travel", "groceries", "utilities",
    "entertainment", "shopping", "rent", "medical", "general"
}

CATEGORY_KEYWORDS = {
    "food": ["food", "pizza", "dinner", "lunch", "breakfast", "restaurant", "eat", "meal",
             "snack", "coffee", "chai", "biryani", "burger", "sushi", "curry", "drinks", "bar"],
    "travel": ["travel", "uber", "ola", "cab", "taxi", "flight", "train", "bus", "petrol",
               "fuel", "toll", "auto", "metro", "hotel", "stay", "airbnb", "hostel accommodation"],
    "groceries": ["grocery", "groceries", "vegetables", "fruits", "milk", "supermarket",
                  "kirana", "provisions", "eggs", "bread", "rice", "dal"],
    "utilities": ["electricity", "water", "gas", "internet", "wifi", "bill", "utility",
                  "utilities", "recharge", "phone", "mobile", "broadband"],
    "entertainment": ["movie", "netflix", "cinema", "concert", "show", "game", "sport",
                      "entertainment", "spotify", "ott", "subscription", "tickets"],
    "shopping": ["shopping", "clothes", "amazon", "flipkart", "shoes", "shirt", "dress",
                 "mall", "apparel", "fashion", "accessories"],
    "rent": ["rent", "flat", "house", "pg", "hostel", "room", "deposit", "maintenance"],
    "medical": ["medical", "medicine", "doctor", "hospital", "pharmacy", "clinic",
                "health", "chemist", "prescription", "test", "scan"],
}


# ── Custom exception ───────────────────────────────────────────────────────────

class ParseWarning(Exception):
    """
    Raised when the user explicitly names people to split with, but one or more
    of those names cannot be matched to any group member.
    Caught by the API endpoint and returned as a warning (not a 4xx error),
    so the frontend can display a helpful message without crashing.
    """
    pass


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _fuzzy_match(name: str, members: list) -> str | None:
    """Case-insensitive fuzzy match of a name against group members."""
    name_lower = name.lower().strip()
    # Exact match first
    for member in members:
        if member.lower() == name_lower:
            return member
    # Substring match (handles "rahul" → "Rahul Singh" etc.)
    for member in members:
        if name_lower in member.lower() or member.lower() in name_lower:
            return member
    return None


def _detect_category(text: str) -> str:
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return "general"


def _extract_explicit_split_clause(text: str) -> str | None:
    """
    Return the raw split clause if the text contains an explicit split instruction.
    e.g. "split with Rahul and Priya" → "Rahul and Priya"
    Returns None if no explicit split instruction is found.
    """
    m = re.search(
        r"\bsplit\s+(?:between|with|among)\s+(.+?)(?:\s+for\b|\s+on\b|\s+at\b|\.|,|$)",
        text, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _tokenise_names(clause: str, default_user: str) -> list[str]:
    """
    Break a split clause into individual name tokens.
    Resolves "me" / "I" → default_user.
    e.g. "me and Rahul and Priya" → [default_user, "Rahul", "Priya"]
    """
    # Resolve first-person pronouns
    clause = re.sub(r"\bme\b|\bI\b", default_user, clause, flags=re.IGNORECASE)
    # Split on "and" or commas
    tokens = re.split(r"\s+and\s+|,\s*", clause, flags=re.IGNORECASE)
    return [t.strip() for t in tokens if t.strip()]


def _validate_explicit_names(
    tokens: list[str], group_members: list, default_user: str
) -> tuple[list[str], list[str]]:
    """
    Map each token to a group member via fuzzy/case-insensitive matching.

    Returns:
        matched   – list of resolved member names
        unmatched – list of tokens that had no match (these cause a warning)
    """
    matched: list[str] = []
    unmatched: list[str] = []
    for token in tokens:
        member = _fuzzy_match(token, group_members)
        if member:
            if member not in matched:
                matched.append(member)
        else:
            unmatched.append(token)
    return matched, unmatched


def _check_explicit_split(text: str, default_user: str, group_members: list) -> list[str] | None:
    """
    If the text has an explicit split instruction, validate named people against
    group members.  Raises ParseWarning if any named person is not found.
    Returns the list of matched member names, or None if there was no explicit
    split instruction (caller should then default to all members).
    """
    clause = _extract_explicit_split_clause(text)
    if clause is None:
        return None  # no explicit instruction → caller decides default

    tokens = _tokenise_names(clause, default_user)
    if not tokens:
        return None

    matched, unmatched = _validate_explicit_names(tokens, group_members, default_user)

    if unmatched:
        missing = ", ".join(f"'{n}'" for n in unmatched)
        members_display = ", ".join(group_members) if group_members else "(none)"
        raise ParseWarning(
            f"Could not find {missing} in this group. "
            f"Members are: {members_display}."
        )

    return matched  # all found — use only these people


# ── Primary parser (Gemini) ────────────────────────────────────────────────────

def parse_expense(text: str, default_user: str, group_members: list) -> dict:
    """
    Parse natural language expense using Google Gemini 2.0 Flash.

    Raises ParseWarning (before calling Gemini) if the user names specific split
    participants that don't exist in the group.
    Falls back to parse_expense_fallback on any other Gemini failure.
    """
    # Validate explicit split names BEFORE the API call to avoid wasting quota
    # and to give a precise error message while context is clear.
    if group_members:
        _check_explicit_split(text, default_user, group_members)
        # If we reach here, either there's no explicit split OR all named people matched.

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "your_key_here":
        return parse_expense_fallback(text, default_user, group_members)

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")

        members_str = ", ".join(group_members) if group_members else "none"

        prompt = f"""You are parsing an expense description into structured data for a bill-splitting app.

Expense text: "{text}"

Context:
- "I" or "me" refers to: {default_user}
- Group members (use these exact names): {members_str}

CRITICAL RULES:
1. PAYER: If a specific person's name appears as the payer (e.g., "Priya paid", "Rahul spent", "Amit bought"), set paid_by to THAT person, NOT to {default_user}. Only use {default_user} as payer when "I", "me", or no payer is mentioned.
2. PARTICIPANTS — this is the most important rule:
   a. If the sentence explicitly names who to split with (e.g., "split between X and Y", "split with X and Y", "between me and X"), ONLY include those named people (plus the payer). Do NOT add other group members.
   b. Only include ALL group members if no specific people are mentioned for the split.
3. participants MUST always include paid_by.
4. "me" or "I" in the split list refers to {default_user}.
5. Match all names to the closest group member name from the list provided.
6. Return ONLY valid JSON — no markdown, no explanation.
7. amount must be a plain number (no ₹ or Rs symbols).
8. category must be exactly one of: food, travel, groceries, utilities, entertainment, shopping, rent, medical, general.

Return this exact JSON structure:
{{
  "description": "concise description of what was bought",
  "amount": 0.0,
  "paid_by": "exact member name who paid",
  "participants": ["name1", "name2"],
  "category": "category"
}}"""

        response = model.generate_content(prompt)
        raw = response.text.strip()

        # Strip markdown code fences
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

        result = json.loads(raw)

        # Validate required fields
        for field in ["description", "amount", "paid_by", "participants", "category"]:
            if field not in result:
                raise ValueError(f"Missing field: {field}")

        result["amount"] = round(float(result["amount"]), 2)

        if result["category"] not in VALID_CATEGORIES:
            result["category"] = _detect_category(text)

        # Fuzzy-match all names against actual group members
        if group_members:
            matched = []
            for p in result["participants"]:
                m = _fuzzy_match(p, group_members)
                if m and m not in matched:
                    matched.append(m)
            if matched:
                result["participants"] = matched

            payer_match = _fuzzy_match(result["paid_by"], group_members)
            if payer_match:
                result["paid_by"] = payer_match

        # Ensure payer is in participants
        if result["paid_by"] not in result["participants"]:
            result["participants"].insert(0, result["paid_by"])

        if result["amount"] <= 0:
            raise ValueError("Amount must be positive")

        return result

    except Exception as e:
        logger.warning(f"Gemini parse failed: {e}. Using fallback.")
        return parse_expense_fallback(text, default_user, group_members)


# ── Fallback parser (regex) ────────────────────────────────────────────────────

def parse_expense_fallback(text: str, default_user: str, group_members: list) -> dict:
    """
    Regex-based fallback parser.

    Raises ParseWarning if the text explicitly names split participants that
    cannot be found in the group members list.
    """
    # Validate explicit split names — same check as the primary parser
    explicit_participants: list[str] | None = None
    if group_members:
        explicit_participants = _check_explicit_split(text, default_user, group_members)
        # ParseWarning propagates up if any named person is missing.

    # Extract first number as amount
    amount_match = re.search(r"\b(\d+(?:[.,]\d+)?)\b", text)
    amount = 0.0
    if amount_match:
        amount = round(float(amount_match.group(1).replace(",", "")), 2)

    category = _detect_category(text)

    # Try to detect named payer
    paid_by = default_user
    if group_members:
        for member in group_members:
            if re.search(rf"\b{re.escape(member)}\b\s+(?:paid|spent|bought|covered)",
                         text, re.IGNORECASE):
                paid_by = member
                break
        for member in group_members:
            if re.search(rf"paid\s+by\s+{re.escape(member)}", text, re.IGNORECASE):
                paid_by = member
                break

    # Participants: use explicitly named people if found, otherwise all members
    if explicit_participants is not None:
        participants = explicit_participants
    else:
        participants = list(group_members) if group_members else [paid_by]

    if paid_by not in participants:
        participants.insert(0, paid_by)

    # Clean description
    description = re.sub(r"\b\d+(?:[.,]\d+)?\b", "", text)
    description = re.sub(r"\s+", " ", description).strip(" ,.-")
    if not description:
        description = text.strip()

    return {
        "description": description,
        "amount": amount,
        "paid_by": paid_by,
        "participants": participants,
        "category": category,
    }
