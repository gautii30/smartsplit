import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
from splitter import calculate_balances, simplify_debts


def make_expense(paid_by, amount, participants):
    """Helper: create an expense split equally among participants."""
    share = round(amount / len(participants), 2)
    return {
        "paid_by": paid_by,
        "amount": amount,
        "splits": [{"member_name": p, "share": share} for p in participants],
    }


class TestCalculateBalances:

    def test_simple_equal_split(self):
        """Alice pays 100, split equally between Alice and Bob."""
        expenses = [make_expense("Alice", 100.0, ["Alice", "Bob"])]
        balances = calculate_balances(expenses)
        assert balances["Alice"] == 50.0   # paid 100, owes 50 → net +50
        assert balances["Bob"] == -50.0    # owes 50

    def test_multiple_expenses(self):
        """Alice pays 100, Bob pays 60 — both split equally."""
        expenses = [
            make_expense("Alice", 100.0, ["Alice", "Bob"]),
            make_expense("Bob", 60.0, ["Alice", "Bob"]),
        ]
        balances = calculate_balances(expenses)
        # Alice: +100 - 50 - 30 = +20
        # Bob:   +60  - 50 - 30 = -20
        assert balances["Alice"] == 20.0
        assert balances["Bob"] == -20.0

    def test_no_expenses(self):
        """Empty expense list returns empty balances."""
        assert calculate_balances([]) == {}

    def test_single_person_pays_self(self):
        """Only one person — pays and owes same amount, net zero."""
        expenses = [make_expense("Alice", 100.0, ["Alice"])]
        balances = calculate_balances(expenses)
        assert balances["Alice"] == 0.0

    def test_rounding_three_way_split(self):
        """100 split 3 ways — each share 33.33, verify approximate balances."""
        expenses = [make_expense("Alice", 100.0, ["Alice", "Bob", "Charlie"])]
        balances = calculate_balances(expenses)
        assert balances["Alice"] == pytest.approx(66.67, abs=0.01)
        assert balances["Bob"] == pytest.approx(-33.33, abs=0.01)
        assert balances["Charlie"] == pytest.approx(-33.33, abs=0.01)


class TestSimplifyDebts:

    def test_simple_two_person(self):
        """Bob owes Alice 50 — one transaction."""
        balances = {"Alice": 50.0, "Bob": -50.0}
        txns = simplify_debts(balances)
        assert len(txns) == 1
        assert txns[0] == {"from": "Bob", "to": "Alice", "amount": 50.0}

    def test_three_person_simplification(self):
        """Alice owed 60, Bob owed 40, Charlie owes 100 — 2 transactions."""
        balances = {"Alice": 60.0, "Bob": 40.0, "Charlie": -100.0}
        txns = simplify_debts(balances)
        assert len(txns) == 2
        total = sum(t["amount"] for t in txns if t["from"] == "Charlie")
        assert total == pytest.approx(100.0, abs=0.01)

    def test_chain_simplification(self):
        """A owes B, B owes C — simplified to A pays C directly."""
        balances = {"A": -50.0, "B": 0.0, "C": 50.0}
        txns = simplify_debts(balances)
        assert len(txns) == 1
        assert txns[0] == {"from": "A", "to": "C", "amount": 50.0}

    def test_everyone_even(self):
        """All zero balances — no transactions needed."""
        balances = {"Alice": 0.0, "Bob": 0.0, "Charlie": 0.0}
        assert simplify_debts(balances) == []

    def test_complex_four_person(self):
        """Four people — verify total transferred equals total owed."""
        balances = {"A": 100.0, "B": -30.0, "C": -50.0, "D": -20.0}
        txns = simplify_debts(balances)
        total = sum(t["amount"] for t in txns)
        assert total == pytest.approx(100.0, abs=0.01)

    def test_rounding_edge_case(self):
        """Floating-point balances from 3-way split settle correctly."""
        balances = {"Alice": 66.67, "Bob": -33.33, "Charlie": -33.33}
        txns = simplify_debts(balances)
        assert len(txns) == 2
        total = sum(t["amount"] for t in txns)
        assert total == pytest.approx(66.67, abs=0.02)
