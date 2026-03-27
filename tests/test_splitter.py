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
        """Alice pays 100, Bob pays 60, split between both."""
        expenses = [
            make_expense("Alice", 100.0, ["Alice", "Bob"]),
            make_expense("Bob", 60.0, ["Alice", "Bob"]),
        ]
        balances = calculate_balances(expenses)
        # Alice: paid 100, owes 50 (her share of Alice's) + 30 (her share of Bob's) = net +20
        # Bob: paid 60, owes 50 (his share of Alice's) + 30 (his share of Bob's) = net -20
        assert balances["Alice"] == 20.0
        assert balances["Bob"] == -20.0

    def test_no_expenses(self):
        """Empty expense list returns empty balances."""
        assert calculate_balances([]) == {}

    def test_single_person_pays_self(self):
        """Only one person, expense is split with just themselves — net zero."""
        expenses = [make_expense("Alice", 100.0, ["Alice"])]
        balances = calculate_balances(expenses)
        assert balances["Alice"] == 0.0

    def test_rounding_three_way_split(self):
        """100 split 3 ways: each share is 33.33, total debited is 99.99, payer nets +66.67."""
        expenses = [make_expense("Alice", 100.0, ["Alice", "Bob", "Charlie"])]
        balances = calculate_balances(expenses)
        # Alice paid 100, owes 33.33 → +66.67
        # Bob owes 33.33 → -33.33
        # Charlie owes 33.33 → -33.33
        assert balances["Alice"] == pytest.approx(66.67, abs=0.01)
        assert balances["Bob"] == pytest.approx(-33.33, abs=0.01)
        assert balances["Charlie"] == pytest.approx(-33.33, abs=0.01)


class TestSimplifyDebts:

    def test_simple_two_person(self):
        """Bob owes Alice 50."""
        balances = {"Alice": 50.0, "Bob": -50.0}
        txns = simplify_debts(balances)
        assert len(txns) == 1
        assert txns[0] == {"from": "Bob", "to": "Alice", "amount": 50.0}

    def test_three_person_simplification(self):
        """Alice is owed 60, Bob is owed 40, Charlie owes 100."""
        balances = {"Alice": 60.0, "Bob": 40.0, "Charlie": -100.0}
        txns = simplify_debts(balances)
        # Should settle in 2 transactions
        assert len(txns) == 2
        total_from_charlie = sum(t["amount"] for t in txns if t["from"] == "Charlie")
        assert total_from_charlie == pytest.approx(100.0, abs=0.01)

    def test_chain_simplification(self):
        """A owes B, B owes C — should simplify to A pays C directly."""
        # A: -50, B: 0, C: +50
        balances = {"A": -50.0, "B": 0.0, "C": 50.0}
        txns = simplify_debts(balances)
        assert len(txns) == 1
        assert txns[0] == {"from": "A", "to": "C", "amount": 50.0}

    def test_everyone_even(self):
        """All balances are zero — no transactions needed."""
        balances = {"Alice": 0.0, "Bob": 0.0, "Charlie": 0.0}
        txns = simplify_debts(balances)
        assert txns == []

    def test_complex_four_person(self):
        """Four people, multiple debts — verify total owed equals total credited."""
        balances = {"A": 100.0, "B": -30.0, "C": -50.0, "D": -20.0}
        txns = simplify_debts(balances)
        total_paid = sum(t["amount"] for t in txns)
        assert total_paid == pytest.approx(100.0, abs=0.01)

    def test_rounding_edge_case(self):
        """Balances with floating point values settle correctly."""
        balances = {"Alice": 66.67, "Bob": -33.33, "Charlie": -33.33}
        txns = simplify_debts(balances)
        assert len(txns) == 2
        total = sum(t["amount"] for t in txns)
        assert total == pytest.approx(66.67, abs=0.02)
