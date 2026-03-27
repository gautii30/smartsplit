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


def _fuzzy_match(name: str, members: list) -> str | None:
    """Case-insensitive fuzzy match of a name against group members."""
    name_lower = name.lower().strip()
    for member in members:
        if member.lower() == name_lower:
            return member
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


def parse_expense(text: str, default_user: str, group_members: list) -> dict:
    """
    Parse natural language expense using Google Gemini 2.0 Flash.
    Falls back to regex parser on any failure.
    """
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
1. If a specific person's name appears as the payer (e.g., "Priya paid", "Rahul spent", "Amit bought"), set paid_by to THAT person, NOT to {default_user}
2. "I paid" or "I spent" or just an amount with no named payer → paid_by is {default_user}
3. "split with X and Y" means {default_user} (the payer) AND X and Y all share equally
4. participants MUST include paid_by
5. Match all names to the closest group member name
6. Return ONLY valid JSON — no markdown, no explanation
7. amount must be a plain number (no ₹ or Rs symbols)
8. category must be exactly one of: food, travel, groceries, utilities, entertainment, shopping, rent, medical, general

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


def parse_expense_fallback(text: str, default_user: str, group_members: list) -> dict:
    """
    Regex-based fallback parser.
    Extracts amount, detects category via keywords, defaults payer to default_user.
    """
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
            pattern = rf"(?:{re.escape(member)})\s+(?:paid|spent|bought|covered)"
            if re.search(pattern, text, re.IGNORECASE):
                paid_by = member
                break
        # Also check "paid by X"
        for member in group_members:
            if re.search(rf"paid\s+by\s+{re.escape(member)}", text, re.IGNORECASE):
                paid_by = member
                break

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
