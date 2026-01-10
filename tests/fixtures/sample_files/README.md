# Sample Test Files for File Integration Feature

Test fixtures for CSV, Excel, and fixed-width file parsing.

## CSV Files (`csv/`)

| File | Description | Delimiter | Rows | Source |
|------|-------------|-----------|------|--------|
| `airports.csv` | US airport metadata (IATA, lat/long, name) | `,` | ~3.3k | [Vega Datasets](https://unpkg.com/vega-datasets@2.0.0/data/airports.csv) |
| `semicolon_delimited.csv` | Test file with semicolon delimiter | `;` | small | [Infinyon Labs](https://github.com/infinyon/labs-csv-json-sm) |
| `stocks.tsv` | Stock prices time series | `\t` (tab) | ~560 | [Vega Datasets](https://unpkg.com/vega-datasets@2.0.0/data/stocks.tsv) |
| `edge_cases_quotes.csv` | Quoted fields, embedded commas, multiline | `,` | small | [univocity-parsers](https://github.com/uniVocity/univocity-parsers) |
| `superstore_orders.csv` | Retail order data (orders, sales, profit) | `,` | ~10k | [Superstore Dataset](https://github.com/sumit0072/Superstore-Data-Analysis) |
| `airports_pipe_delimited.psv` | Pipe-delimited variant of airports | `\|` | ~100 | Generated |

### Edge Cases Covered
- Standard comma delimiter
- Semicolon delimiter (European locale)
- Tab delimiter (TSV)
- Pipe delimiter
- Quoted fields with embedded commas
- Multiline quoted fields
- Escaped quotes (`""`)
- Large row count (~10k)

## Excel Files (`excel/`)

| File | Description | Sheets | Source |
|------|-------------|--------|--------|
| `eva_submission_multisheet.xlsx` | EBI submission template | Multiple | [EBI EVA](https://github.com/EBIvariation/eva-sub-cli) |
| `sample_financial.xlsx` | Microsoft sample financial data | Multiple | [Microsoft](https://go.microsoft.com/fwlink/?LinkID=521962) |
| `treasury_broadband_template.xlsx` | Government data template | 1+ | [US Treasury](https://home.treasury.gov) |

### Edge Cases Covered
- Multiple sheets
- Mixed data types (dates, numbers, text, currency)
- Government/structured templates
- Validation rules

## Fixed-Width Files (`fixed_width/`)

| File | Description | Record Length | Source |
|------|-------------|---------------|--------|
| `nacha_ppd_debit.ach` | NACHA ACH PPD debit batch | 94 chars | [moov-io/ach](https://github.com/moov-io/ach) |
| `ghcnd_stations.txt` | NOAA weather station master | Variable | [NCEI](https://www.ncei.noaa.gov/pub/data/ghcn/daily/) |

### Edge Cases Covered
- Strict positional parsing (NACHA format)
- Padding rules (spaces, zeros)
- Legacy mainframe-style formats
- Large file (~11MB, 130k+ records)

## Usage in Tests

```python
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "sample_files"

def test_csv_parsing():
    csv_file = FIXTURES / "csv" / "airports.csv"
    # ... test parsing

def test_fixed_width_nacha():
    ach_file = FIXTURES / "fixed_width" / "nacha_ppd_debit.ach"
    # ... test fixed-width parsing
```

## Generating Additional Variants

```python
# Create UTF-16LE variant with BOM (encoding stress test)
from pathlib import Path

src = Path("csv/airports.csv")
text = src.read_text(encoding="utf-8")
Path("csv/airports_utf16le.csv").write_bytes(("\ufeff" + text).encode("utf-16le"))
```
