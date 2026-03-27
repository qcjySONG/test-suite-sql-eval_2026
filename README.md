# SQL Evaluation Tool for Text-to-SQL

## ⚠️ IMPORTANT WARNING

**DO NOT use this tool to evaluate BIRD-SQL dataset in academic papers or publications!**

This tool implements a **column order flexible** evaluation metric that differs significantly from the official BIRD-SQL EX metric. Using this tool for BIRD-SQL evaluation would create **unfair comparison** and **misleading results**, similar to a "阴阳合同" (dual contract) situation.

**Why this is problematic:**
- This tool accepts queries with different column orders as correct
- BIRD-SQL EX requires exact column order matching
- Results from this tool will be **higher** than official BIRD-SQL EX scores
- This creates an **unfair advantage** over methods evaluated with the official metric

**For fair evaluation:**
- Use the official BIRD-SQL evaluation script for BIRD-SQL datasets
- Use this tool only for datasets where column order flexibility is desired
- Always clearly state which evaluation metric you used in your paper

## Overview

This is an **evolved version** of the [test-suite-sql-eval](https://github.com/taoyds/test-suite-sql-eval) repository, modernized for the **LLM era** of Text-to-SQL evaluation. The tool has been completely refactored to support **Python 3** syntax and is optimized for evaluating large language model (LLM) outputs.

## Key Improvements

### 1. **LLM-Era Adaptations**
- ✅ **Python 3 Compatible**: Full support for modern Python 3.x syntax
- ✅ **JSON Input Format**: Simplified input format for LLM outputs
- ✅ **Multi-turn Support**: Native support for conversational SQL queries
- ✅ **Enhanced Error Handling**: Better handling of edge cases in LLM outputs

### 2. **Critical Difference from BIRD-SQL EX Metric**

**Unlike BIRD-SQL's EX metric, this tool supports column order flexibility.**

| Feature | BIRD-SQL EX | This Tool |
|---------|-------------|-----------|
| Column Order | Strict matching | ✅ Flexible |
| Evaluation | Exact column order | ✅ Order-independent |
| Real-world Alignment | Rigid | ✅ More realistic |

**Why this matters:**
- **Human language ambiguity**: Natural language queries don't specify column order
- **Semantic equivalence**: `SELECT name, age` and `SELECT age, name` are semantically identical
- **Reduced false negatives**: Many "errors" in BIRD evaluation are actually correct queries with different column order
- **Better user experience**: Users expect queries to work regardless of column order

## Features

- ✅ **Single-turn Evaluation**: Evaluate individual SQL queries
- ✅ **Multi-turn Evaluation**: Evaluate conversational SQL sequences
- ✅ **Execution Accuracy**: Test if queries produce correct results
- ✅ **Table Count Statistics**: Categorize queries by number of tables involved
- ✅ **CTE Recognition**: Automatically exclude temporary tables from CTEs
- ✅ **Flexible Column Matching**: Column order independence (vs. BIRD-SQL)

## Installation

```bash
pip install sqlparse nltk sqlglot
```

## Database Directory Structure

The database directory should be organized as follows:

```
database_directory/
├── {db_id}/                    # Database ID folder
│   ├── {db_id}.sqlite          # SQLite database file
│   └── ...                     # Other database files
├── another_db_id/
│   ├── another_db_id.sqlite
│   └── ...
└── ...
```

**Example Structure:**
```
/amax/storage/qcjySONG/EHRSQL/raw_dataset/EHR_DB/
├── mimic_iii/
│   ├── mimic_iii.sqlite
├── mimic_iv/
│   ├── mimic_iv.sqlite
└── ...
```

**Key Requirements:**
- Each database must have its own folder named `{db_id}/`
- The folder must contain `{db_id}.sqlite` file
- The folder must contain `tables.json` for schema information
- The `--db` parameter should point to the parent directory containing all database folders

## Input File Format

### JSON Format (Recommended)

```json
[
  {
    "goldsql": "SELECT * FROM patients WHERE age > 50",
    "预测sql": "SELECT * FROM patients WHERE age > 50",
    "db": "mimic_iv"
  },
  {
    "goldsql": ["SELECT * FROM patients WHERE age > 50", "SELECT COUNT(*) FROM diagnoses"],
    "预测sql": ["SELECT * FROM patients WHERE age > 50", "SELECT COUNT(*) FROM diagnoses"],
    "db": "mimic_iv"
  }
]
```

**Fields:**
- `goldsql`: Ground truth SQL (string or string array)
- `预测sql`: Predicted SQL from LLM (string or string array)
- `db`: Database ID

**Single-turn vs Multi-turn:**
- **Single-turn**: `goldsql` and `预测sql` as strings
- **Multi-turn**: `goldsql` and `预测sql` as string arrays

### Multi-turn Data Format Example

For multi-turn evaluation, each item contains arrays of SQL queries representing conversational turns:

```json
[
  {
    "db_id": "mimic_iii",
    "predicted_sqls": [
      "SELECT ICUSTAY_ID FROM ICUSTAYS WHERE SUBJECT_ID = 12726;",
      "SELECT ITEMID FROM D_ITEMS WHERE LABEL = 'weight kg';",
      "SELECT VALUENUM FROM CHARTEVENTS WHERE ICUSTAY_ID IN (...) AND ITEMID = (...) ORDER BY CHARTTIME DESC LIMIT 1;",
      "SELECT VALUENUM FROM CHARTEVENTS WHERE ICUSTAY_ID IN (...) AND ITEMID = (...) ORDER BY CHARTTIME ASC LIMIT 1;",
      "SELECT CASE WHEN last_weight - first_weight = 0 THEN 'No difference' ELSE 'There is a difference' END AS difference FROM (...)"
    ],
    "gold_sqls": [
      "SELECT icustays.icustay_id FROM icustays WHERE icustays.hadm_id IN (...)",
      "SELECT d_items.itemid FROM d_items WHERE d_items.label = 'admit wt' AND d_items.linksto = 'chartevents'",
      "SELECT chartevents.valuenum FROM chartevents WHERE chartevents.icustay_id IN (...) AND chartevents.itemid IN (...) ORDER BY chartevents.charttime DESC LIMIT 1",
      "SELECT chartevents.valuenum FROM chartevents WHERE chartevents.icustay_id IN (...) AND chartevents.itemid IN (...) ORDER BY chartevents.charttime ASC LIMIT 1",
      "SELECT (SELECT chartevents.valuenum FROM chartevents WHERE chartevents.icustay_id IN (...) AND chartevents.itemid IN (...) ORDER BY chartevents.charttime DESC LIMIT 1) - (SELECT chartevents.valuenum FROM chartevents WHERE chartevents.icustay_id IN (...) AND chartevents.itemid IN (...) ORDER BY chartevents.charttime ASC LIMIT 1)"
    ]
  }
]
```

**Key Features of Multi-turn Format:**
- `predicted_sqls`: Array of predicted SQL queries for each turn
- `gold_sqls`: Array of ground truth SQL queries for each turn
- Both arrays must have the same length
- Each position in the arrays corresponds to the same turn in the conversation

### Compatible Format

Also supports standard format with `gold_sqls` and `predicted_sqls` fields:

```json
[
  {
    "gold_sqls": ["SELECT * FROM patients WHERE age > 50"],
    "predicted_sqls": ["SELECT * FROM patients WHERE age > 50"],
    "db_id": "mimic_iv"
  }
]
```

## Usage

### Basic Usage

```bash
python3 evaluation.py \
    --input [input_file_path] \
    --db [database_directory]
```

### Example

```bash
python3 evaluation.py \
    --input /path/to/results.json \
    --db /path/to/databases \
    --etype exec \
    --progress_bar_for_each_datapoint
```

## Input Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `--input` | string | ✅ | Path to JSON file containing gold and predicted SQL |
| `--db` | string | ✅ | Directory containing all databases |
| `--table` | string | ❌ | tables.json schema file (only for match mode) |
| `--etype` | string | ❌ | Evaluation type: `exec`, `match`, `all` (default: `exec`) |
| `--plug_value` | flag | ❌ | Insert gold values into predicted query |
| `--keep_distinct` | flag | ❌ | Keep DISTINCT keyword during evaluation |
| `--progress_bar_for_each_datapoint` | flag | ❌ | Show progress bar for each datapoint |
| `--timeout` | integer | ❌ | Timeout in seconds for each query execution (default: 30) |

### Parameter Details

#### `--input` (Required)
- **Type**: String
- **Description**: Path to JSON file with gold and predicted SQL
- **Format**: JSON array with `goldsql`, `预测sql`, `db` fields

#### `--db` (Required)
- **Type**: String
- **Description**: Directory path containing all database files
- **Requirement**: Should contain `{db_id}/{db_id}.sqlite` format files

#### `--table` (Optional)
- **Type**: String
- **Description**: Path to tables.json schema file
- **Usage**: Only needed for `--etype match` or `--etype all`

#### `--etype` (Optional)
- **Type**: String
- **Options**: `exec` | `match` | `all`
- **Default**: `exec`
- **Description**:
  - `exec`: Evaluate execution accuracy only
  - `match`: Evaluate semantic matching only
  - `all`: Evaluate both execution and matching

#### `--plug_value` (Optional)
- **Type**: Boolean flag
- **Default**: False
- **Description**: Insert gold values into predicted SQL

#### `--keep_distinct` (Optional)
- **Type**: Boolean flag
- **Default**: False
- **Description**: Keep DISTINCT keyword during evaluation

#### `--progress_bar_for_each_datapoint` (Optional)
- **Type**: Boolean flag
- **Default**: False
- **Description**: Show progress bar for each datapoint (useful for debugging)

#### `--timeout` (Optional)
- **Type**: Integer
- **Default**: 30
- **Description**: Timeout in seconds for each query execution. Increase this value for complex queries or slow databases.

## Output Format

```
                     easy                 medium               hard                 extra                all
count                26                   7                    16                   657                  706
=====================   EXECUTION ACCURACY     =====================
execution            0.923                0.286                0.812                0.623                0.635

                     single               two                  three                four                 five_plus            all
count                128                  208                  195                  164                  11                   706
=====================   TABLE COUNT EXECUTION ACCURACY     =====================
execution            0.898                0.745                0.564                0.366                0.727                0.635
```

### Output Dimensions

#### 1. Difficulty Dimension
- **easy**: Simple queries
- **medium**: Medium complexity queries
- **hard**: Complex queries
- **extra**: Very complex queries
- **all**: All queries

#### 2. Table Count Dimension (New)
- **single**: Single-table queries
- **two**: Two-table queries
- **three**: Three-table queries
- **four**: Four-table queries
- **five_plus**: Five or more tables
- **all**: All queries

## Version History

### v2.0 (Current)

#### Major Changes

1. **Input Format Change**
   - Old: Separate gold and pred TXT files
   - New: Single JSON file with simplified structure

2. **New Table Count Statistics**
   - Uses sqlglot for accurate SQL parsing
   - Counts real tables (excludes CTE temporary tables)
   - Provides table count dimension analysis

3. **Evaluation Algorithm Optimization**
   - Removed overly strict parsing restrictions
   - More accurate SQL parsing with sqlglot
   - Better compatibility with various SQL dialects

4. **Critical Improvement: Column Order Flexibility**
   - **Unlike BIRD-SQL**: This tool does not penalize column order differences
   - **Why**: Column order is a presentation choice, not a semantic error
   - **Benefit**: More accurate evaluation of actual query correctness

#### Why Accuracy Improved

The accuracy improvement in v2.0 may be due to:
1. **Removed unnecessary restrictions**: Old version had strict parsing limits
2. **Better table counting**: Correct identification of real tables
3. **Improved error handling**: Better fallback strategies for parsing failures
4. **Column order flexibility**: Correct queries with different column order are now accepted

## Examples

### Single-turn Evaluation

```bash
python3 evaluation.py \
    --input data/single_round.json \
    --db /path/to/databases \
    --etype exec
```

### Multi-turn Evaluation

```bash
python3 evaluation.py \
    --input data/multi_round.json \
    --db /path/to/databases \
    --etype exec \
    --progress_bar_for_each_datapoint
```

## Notes

1. **Database Path**: Ensure `--db` directory contains `{db_id}/{db_id}.sqlite` format files
2. **JSON Format**: Ensure JSON file format is correct with proper field names
3. **Multi-turn**: For multi-turn evaluation, `goldsql` and `预测sql` must be arrays of same length
4. **Evaluation Mode**: `match` mode requires additional `--table` parameter

## Troubleshooting

### Common Issues

1. **SQL Parsing Failure**
   - Check SQL syntax correctness
   - Verify database ID is correct

2. **Database Connection Failure**
   - Confirm database file path is correct
   - Check file permissions

3. **Evaluation Results Abnormal**
   - Verify gold and pred SQL count match
   - Confirm JSON format is correct

## Citation

If you use this tool, please cite the original work:

```bibtex
@InProceedings{ruiqi20,
  author =  {Ruiqi Zhong and Tao Yu and Dan Klein},
  title =   {Semantic Evaluation for Text-to-SQL with Distilled Test Suite},
  year =    {2020},
  booktitle =   {The 2020 Conference on Empirical Methods in Natural Language Processing},
  publisher = {Association for Computational Linguistics},
}
```

## License

This project follows the same license as the original [test-suite-sql-eval](https://github.com/taoyds/test-suite-sql-eval) repository.