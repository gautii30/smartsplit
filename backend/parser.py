"""
Expense parser for SmartSplit.

Primary: Google Gemini 2.0 Flash via google-generativeai
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
    "food": ["food", "pizza", "dinner", "lunch", "breakfast", "restaurant", "eat", "meal", "snack", "coffee", "chai", "biryani", "burger"],
    "travel": ["travel", "uber", "ola", "cab", "taxi", "flight", "train", "bus", "petrol", "fuel", "toll", "auto"],
    "groceries": ["grocery", "groceries", "vegetables", "fruits", "milk", "supermarket", "kirana", "provisions"],
    "utilities": ["electricity", "water", "gas", "internet", "wifi", "bill", "utility", "utilities", "recharge"],
    "entertainment": ["movie", "netflix", "cinema", "concert", "show", "game", "sport", "entertainment"],
    "shopping": ["shopping", "clothes", "amazon", "flipkart", "shoes", "shirt", "dress", "mall"],
    "rent": ["rent", "flat", "house", "pg", "hostel", "accommodation", "room"],
    "medical": ["medical", "medicine", "doctor", "hospital", "pharmacy", "clinic", "health"],
}


def _fuzzy_match(name: str, members: list[str]) -> str | None:
    """Match a name to a group member, case-insensitive, partial match allowed."""
    name_lower = name.lower().strip()
    # Exact match first
    for member in members:
        if member.lower() == name_lower:
            return member
    # Partial match
    for member in members:
        if name_lower in member.lower() or member.lower() in name_lower:
            return member
    return None


def _detect_category(text: str) -> str:
    """Detect expense category from text using keyword matching."""
    text_lower = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return category
    return "general"


def parse_expense(text: str, default_user: str, group_members: list[str]) -> dict:
    """
    Parse natural language expense description using Google Gemini 2.0 Flash.

    Falls back to regex parser if Gemini is unavailable or returns invalid data.

    Args:
        text: natural language expense description
        default_user: name to use when user says "I" or "me"
        group_members: list of member names in the group

    Returns:
        dict with: description, amount, paid_by, participants (list), category
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or api_key == "your_key_here":
        logger.info("No Gemini API key found, using fallback parser")
        return parse_expense_fallback(text, default_user, group_members)

    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")

        members_str = ", ".join(group_members) if group_members else "unknown"

        prompt = f"""Parse this expense description into structured JSON.

Expense: "{text}"

Context:
- "I" or "me" refers to: {default_user}
- Group members: {members_str}
- "split with X and Y" means the payer AND X and Y all share the expense

Rules:
1. Return ONLY valid JSON, no markdown, no explanation
2. category must be exactly one of: food, travel, groceries, utilities, entertainment, shopping, rent, medical, general
3. participants must include the payer
4. amount must be a number (no currency symbols)
5. Match participant names to group members (fuzzy match if needed)

Return this exact JSON structure:
{{
  "description": "short description of what was bought",
  "amount": 0.0,
  "paid_by": "name of who paid",
  "participants": ["name1", "name2"],
  "category": "category"
}}"""

        response = model.generate_content(prompt)
        raw = response.text.strip()

        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

        result = json.loads(raw)

        # Validate required fields
        required = ["description", "amount", "paid_by", "participants", "category"]
        for field in required:
            if field not in result:
                raise ValueError(f"Missing field: {field}")

        # Validate and coerce types
        result["amount"] = round(float(result["amount"]), 2)

        if result["category"] not in VALID_CATEGORIES:
            result["category"] = _detect_category(text)

        # Fuzzy match participants against group members
        if group_members:
            matched_participants = []
            for p in result["participants"]:
                matched = _fuzzy_match(p, group_members)
                if matched and matched not in matched_participants:
                    matched_participants.append(matched)
            result["participants"] = matched_participants if matched_participants else result["participants"]

            # Fuzzy match paid_by
            matched_payer = _fuzzy_match(result["paid_by"], group_members)
            if matched_payer:
                result["paid_by"] = matched_payer

        # Ensure payer is in participants
        if result["paid_by"] not in result["participants"]:
            result["participants"].insert(0, result["paid_by"])

        return result

    except Exception as e:
        logger.warning(f"Gemini parsing failed: {e}. Using fallback parser.")
        return parse_expense_fallback(text, default_user, group_members)


def parse_expense_fallback(text: str, default_user: str, group_members: list[str]) -> dict:
    """
    Regex-based fallback expense parser.

    Extracts:
    - amount: first number found in text
    - category: keyword matching
    - paid_by: default_user (since regex can't reliably extract payer)
    - participants: all group members (or just default_user if no members)
    - description: cleaned version of the text

    Args:
        text: natural language expense description
        default_user: name to use as default payer
        group_members: list of group member names

    Returns:
        dict with: description, amount, paid_by, participants, category
    """
    # Extract amount — first number (int or float) in the text
    amount_match = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
    amount = round(float(amount_match.group(1)), 2) if amount_match else 0.0

    # Detect category
    category = _detect_category(text)

    # Determine payer
    paid_by = default_user

    # Try to find named payer in text
    if group_members:
        for member in group_members:
            # Match "member paid" or "paid by member"
            if re.search(rf"\b{re.escape(member)}\b.{{0,20}}paid|paid.{{0,20}}\b{re.escape(member)}\b", text, re.IGNORECASE):
                paid_by = member
                break

    # Participants: all group members, or just payer if no members
    participants = list(group_members) if group_members else [paid_by]
    if paid_by not in participants:
        participants.insert(0, paid_by)

    # Description: strip the amount and clean up
    description = re.sub(r"\b\d+(?:\.\d+)?\b", "", text).strip()
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
