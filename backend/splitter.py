"""
Settlement algorithm for SmartSplit.

calculate_balances: computes net balance per person
  Positive = this person is owed money by the group
  Negative = this person owes money to the group

simplify_debts: greedy algorithm minimizing the number of transactions
"""


def calculate_balances(expenses):
    """
    Calculate net balance for each person across all expenses.

    Args:
        expenses: list of dicts, each with:
            - paid_by (str): who paid the full amount
            - amount (float): total expense amount
            - splits (list): each item has member_name and share

    Returns:
        dict mapping member_name -> net_balance (float)
        Positive = owed money, Negative = owes money
    """
    balances = {}

    for expense in expenses:
        payer = expense["paid_by"]
        amount = round(float(expense["amount"]), 2)

        # Payer gets credited the full amount they fronted
        balances[payer] = round(balances.get(payer, 0.0) + amount, 2)

        # Each participant is debited their share
        for split in expense["splits"]:
            member = split["member_name"]
            share = round(float(split["share"]), 2)
            balances[member] = round(balances.get(member, 0.0) - share, 2)

    return balances


def simplify_debts(balances):
    """
    Simplify debts using a greedy algorithm that minimizes transaction count.

    Steps:
      1. Separate into debtors (negative balance) and creditors (positive balance)
      2. Sort both lists by absolute value, largest first
      3. Match the biggest debtor with the biggest creditor
      4. Settle min(debt, credit) in one transaction
      5. Remove the fully-settled party and repeat

    Args:
        balances: dict mapping member_name -> net balance

    Returns:
        list of {from, to, amount} dicts
    """
    EPSILON = 0.005  # ignore floating-point dust

    debtors = []    # owe money (negative balance)
    creditors = []  # are owed money (positive balance)

    for name, balance in balances.items():
        b = round(balance, 2)
        if b < -EPSILON:
            debtors.append([name, abs(b)])
        elif b > EPSILON:
            creditors.append([name, b])

    # Sort largest first
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)

    transactions = []

    while debtors and creditors:
        debtor, debt = debtors[0]
        creditor, credit = creditors[0]

        settled = round(min(debt, credit), 2)
        transactions.append({"from": debtor, "to": creditor, "amount": settled})

        debtors[0][1] = round(debt - settled, 2)
        creditors[0][1] = round(credit - settled, 2)

        if debtors[0][1] <= EPSILON:
            debtors.pop(0)
        if creditors[0][1] <= EPSILON:
            creditors.pop(0)

    return transactions


def aggregate_friend_balances(user: str, groups_data: list) -> dict:
    """
    Compute TRUE pairwise net balances between the user and each friend,
    aggregated across all groups.

    WHY NOT simplify_debts: the greedy minimum-transaction algorithm can reroute
    debts through third parties (e.g. A owes B, B owes C → A pays C directly).
    This makes friends disappear from the user's view and distorts amounts.
    We must use raw per-expense pairwise accounting instead.

    For each expense:
      - If user paid: every other participant owes the user their split share.
      - If someone else paid: if the user is a participant, the user owes
        the payer their own split share.

    Per-group contribution is tracked separately so the UI can show a breakdown.

    Args:
        user: the logged-in user's name
        groups_data: list of dicts, each with:
            - group_id (int)
            - group_name (str)
            - expenses (list): expense dicts with splits

    Returns:
        dict mapping friend_name -> {
            "net_balance": float,   # positive = friend owes user, negative = user owes friend
            "groups": [{"group_name": str, "group_id": int, "balance": float}]
        }
    """
    # Internal structure during accumulation:
    # friends[name] = {"net_balance": float, "groups": {group_id: {"group_name": str, "balance": float}}}
    friends: dict = {}

    for group in groups_data:
        group_name = group["group_name"]
        group_id = group["group_id"]

        for expense in group["expenses"]:
            payer = expense["paid_by"]
            splits = expense["splits"]

            if payer == user:
                # User paid — each other participant owes the user their share
                for split in splits:
                    friend = split["member_name"]
                    if friend == user:
                        continue  # user doesn't owe themselves
                    _pairwise_add(friends, friend, group_id, group_name,
                                  +round(float(split["share"]), 2))
            else:
                # Someone else paid — user owes the payer their own share (if present)
                user_split = next(
                    (s for s in splits if s["member_name"] == user), None
                )
                if user_split is None:
                    continue  # user not involved in this expense
                _pairwise_add(friends, payer, group_id, group_name,
                              -round(float(user_split["share"]), 2))

    # Convert internal per-group dicts to sorted lists for the API response
    result = {}
    for fname, data in friends.items():
        net = round(data["net_balance"], 2)
        groups_list = [
            {
                "group_name": gdata["group_name"],
                "group_id": gid,
                "balance": round(gdata["balance"], 2),
            }
            for gid, gdata in data["groups"].items()
            if abs(gdata["balance"]) > 0.005
        ]
        result[fname] = {"net_balance": net, "groups": groups_list}

    return result


def _pairwise_add(friends: dict, friend: str, group_id: int, group_name: str, amount: float):
    """Accumulate a pairwise balance contribution."""
    if friend not in friends:
        friends[friend] = {"net_balance": 0.0, "groups": {}}
    friends[friend]["net_balance"] = round(friends[friend]["net_balance"] + amount, 2)
    if group_id not in friends[friend]["groups"]:
        friends[friend]["groups"][group_id] = {"group_name": group_name, "balance": 0.0}
    friends[friend]["groups"][group_id]["balance"] = round(
        friends[friend]["groups"][group_id]["balance"] + amount, 2
    )
