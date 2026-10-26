# t1c1-ton-up
Private hackathon repository for Ton Up

---

# Kirana Ledger Digitizer

Kirana Ledger Digitizer converts photographs of handwritten kirana shop ledgers into structured digital transaction records.
It uses Gemma 4 to extract transaction details from ledger images and supports human review and correction.
The application provides an editable spreadsheet-style interface in Kannada, English, and Hindi.
It uses Supabase PostgreSQL to store confirmed records and Python with Pandas to calculate sales, product trends, and outstanding dues.
It generates multilingual business insights and supports dashboard visualizations and Excel export.

## Key Features

- Handwritten ledger image upload and AI-powered data extraction.
- Editable digital records with validation and human confirmation.
- Kannada, English, and Hindi interface support.
- Sales summaries, product analysis, and customer outstanding dues.
- Multilingual business insights and dashboard visualizations.
- Export confirmed records to Excel.

## Technology Stack

- Python
- Streamlit
- Gemma 4 API
- OpenCV
- Pydantic
- Supabase PostgreSQL
- Pandas
- Plotly
- Excel export using openpyxl

## Planned Workflow

1. Upload a handwritten ledger photograph.
2. Preprocess the image and extract transaction data using Gemma 4.
3. Validate extracted records and allow users to correct them.
4. Confirm records before storing them in Supabase PostgreSQL.
5. Calculate business metrics using Python and Pandas.
6. Generate verified multilingual insights and display the results in a dashboard.
7. Export confirmed digital records to Excel.

## Data Accuracy

AI-extracted records may contain errors. Users must review and correct uncertain fields before confirming transactions. Financial calculations are performed using Python rather than relying on AI-generated arithmetic.

## Project Status

- **Milestone 1 (Complete)**: Core project structure, safe configuration loading, multilingual packs (en, kn, hi, te [draft], ta [draft]), Supabase PostgreSQL schema, canonical 50-item kirana catalog, and test suite.
- **Milestone 2 (Upcoming)**: Ledger image upload, OpenCV preprocessing, and Gemma 4 OCR extraction.
- **Milestone 3 (Upcoming)**: Interactive human review/correction UI, Supabase sync, and Excel export.

## Project Structure

```text
Ton Up/
├── app.py                  # Main Streamlit web application
├── core/                   # Core business logic and foundation
│   ├── config.py           # Safe configuration and secret masking
│   ├── i18n.py             # Language pack manager and fallback translation
│   ├── models.py           # Pydantic v2 domain models
│   ├── catalog.py          # Kirana product catalog loader & matcher
│   └── db.py               # Supabase connection manager
├── data/
│   └── items.csv           # 50 common kirana products (en, kn, hi)
├── languages/              # Multilingual JSON translation packs
│   ├── en.json             # English (Verified)
│   ├── kn.json             # Kannada (Verified)
│   ├── hi.json             # Hindi (Verified)
│   ├── te.json             # Telugu (Draft - Needs Native Review)
│   └── ta.json             # Tamil (Draft - Needs Native Review)
├── sql/
│   └── schema.sql          # Supabase PostgreSQL schema & indexes
├── tests/                  # Test suite (config safety, i18n, schema, catalog)
│   ├── test_config.py
│   ├── test_i18n.py
│   ├── test_schema.py
│   └── test_catalog.py
├── .env.example            # Template for environment configuration
├── .gitignore              # Ignores .env, caches, logs, and uploads
└── requirements.txt        # Python 3.11 dependency specifications
```

## Getting Started (Milestone 1)

### 1. Setup Environment
```bash
cp .env.example .env
# Edit .env to add your Gemma API key and Supabase credentials
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run Tests
```bash
# Standard unittest (runs out-of-the-box):
python -m unittest discover tests -v

# Or using pytest:
pytest -v
```

### 4. Launch Application
```bash
streamlit run app.py
```

## Configuration

API credentials and database connection settings will be stored in environment variables. Secret credentials must not be committed to the repository.

## Future Improvements

- Improve extraction accuracy for different handwriting styles.
- Refine multilingual insights and dashboard usability.
- Expand testing with real-world ledger images.
- Improve error handling and deployment readiness.
