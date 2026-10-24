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

Prototype development in progress. Features, integrations, and testing will be updated as development advances.

## Configuration

API credentials and database connection settings will be stored in environment variables. Secret credentials must not be committed to the repository.

## Future Improvements

- Improve extraction accuracy for different handwriting styles.
- Refine multilingual insights and dashboard usability.
- Expand testing with real-world ledger images.
- Improve error handling and deployment readiness.
