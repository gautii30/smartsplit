# SmartSplit

AI-powered expense splitter with natural language input.

## Setup

```bash
cd smartsplit
pip install -r requirements.txt

# Add your Gemini API key to .env
echo "GEMINI_API_KEY=your_actual_key" > .env
```

## Run

```bash
cd backend
uvicorn main:app --reload
```

Open http://localhost:8000

## Test

```bash
pytest tests/ -v
```

## Usage

1. Create a group (e.g. "Goa Trip")
2. Add members
3. Type an expense in natural language: `"Paid 1500 for pizza, split with Rahul and Priya"`
4. Click Parse → verify the AI-parsed result → Confirm & Add
5. Switch to "Settle Up" tab to see who owes what
