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
