"""
Settlement algorithm for SmartSplit.

calculate_balances: computes net balance per person (positive = owed money, negative = owes money)
simplify_debts: greedy algorithm that minimizes the number of transactions needed to settle all debts
"""


def calculate_balances(expenses):
    """
    Calculate net balance for each person across all expenses.

    Args:
        expenses: list of dicts, each with:
            - paid_by (str): who paid
            - amount (float): total expense amount
            - splits (list of dicts): each has member_name and share

    Returns:
        dict mapping member_name -> net balance
        Positive balance means they are owed money.
        Negative balance means they owe money.
    """
    balances = {}

    for expense in expenses:
        payer = expense["paid_by"]
        amount = round(float(expense["amount"]), 2)

        # Payer gets credit for the full amount they paid
        balances[payer] = round(balances.get(payer, 0.0) + amount, 2)

        # Each participant is debited their share
        for split in expense["splits"]:
            member = split["member_name"]
            share = round(float(split["share"]), 2)
            balances[member] = round(balances.get(member, 0.0) - share, 2)

    return balances


def simplify_debts(balances):
    """
    Simplify debts using a greedy algorithm that minimizes number of transactions.

    Strategy:
        1. Separate people into debtors (negative balance) and creditors (positive balance)
        2. Sort both lists descending by absolute value
        3. Match the largest debtor with the largest creditor
        4. Settle as much as possible in one transaction
        5. Repeat until all balances are zero

    Args:
        balances: dict mapping member_name -> net balance

    Returns:
        list of dicts: [{from: str, to: str, amount: float}, ...]
    """
    EPSILON = 0.01  # ignore dust amounts from floating point

    # Build mutable lists of (name, amount) for debtors and creditors
    debtors = []   # people who owe money (negative balance)
    creditors = [] # people who are owed money (positive balance)

    for name, balance in balances.items():
        balance = round(balance, 2)
        if balance < -EPSILON:
            debtors.append([name, abs(balance)])
        elif balance > EPSILON:
            creditors.append([name, balance])

    # Sort descending by amount
    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)

    transactions = []

    while debtors and creditors:
        debtor_name, debt = debtors[0]
        creditor_name, credit = creditors[0]

        # Settle the smaller of the two amounts
        settled = round(min(debt, credit), 2)

        transactions.append({
            "from": debtor_name,
            "to": creditor_name,
            "amount": settled
        })

        # Update remaining balances
        debtors[0][1] = round(debt - settled, 2)
        creditors[0][1] = round(credit - settled, 2)

        # Remove fully settled parties
        if debtors[0][1] <= EPSILON:
            debtors.pop(0)
        if creditors[0][1] <= EPSILON:
            creditors.pop(0)

    return transactions
