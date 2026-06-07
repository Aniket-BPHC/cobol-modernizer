"""
rag/cobol_docs.py
─────────────────
The raw COBOL knowledge base.

Each entry is a dict with:
  id       – stable unique key used as the ChromaDB document id
  topic    – the COBOL construct or concept this chunk covers
  tags     – list of COBOL keywords/patterns this chunk is relevant to
  text     – the actual documentation content that gets embedded + injected

Sourced from:
  • IBM Enterprise COBOL Language Reference, v6.3
  • GnuCOBOL Programmer's Guide, v3.2
  • COBOL for the 21st Century (Stern & Stern)
  • SWIFT / banking migration guidance notes
"""

CHUNKS: list[dict] = [

    # ── IDENTIFICATION DIVISION ────────────────────────────────────────────
    {
        "id": "id_div",
        "topic": "IDENTIFICATION DIVISION",
        "tags": ["IDENTIFICATION DIVISION", "PROGRAM-ID", "AUTHOR", "DATE-WRITTEN"],
        "text": (
            "IDENTIFICATION DIVISION is the first division of every COBOL program. "
            "It contains the PROGRAM-ID paragraph (mandatory) which names the program, "
            "and optional paragraphs: AUTHOR, INSTALLATION, DATE-WRITTEN, DATE-COMPILED, SECURITY. "
            "In Python translation, PROGRAM-ID becomes the module name or a module-level docstring. "
            "None of the optional paragraphs have runtime effect; they translate to comments."
        ),
    },

    # ── DATA DIVISION / WORKING-STORAGE ───────────────────────────────────
    {
        "id": "working_storage",
        "topic": "WORKING-STORAGE SECTION",
        "tags": ["WORKING-STORAGE", "DATA DIVISION", "01", "77", "VALUE"],
        "text": (
            "WORKING-STORAGE SECTION declares variables that persist for the life of the program. "
            "Level-01 items are group items (structs); level-77 are standalone elementary items. "
            "VALUE clause sets the initial value. "
            "In Python, each 01-level group becomes a dataclass or dict; "
            "each elementary item becomes a typed variable. "
            "Always use decimal.Decimal for numeric items — never float."
        ),
    },
    {
        "id": "pic_clause",
        "topic": "PIC / PICTURE clause",
        "tags": ["PIC", "PICTURE", "S9", "V", "9(", "X(", "A(", "Z("],
        "text": (
            "PIC (PICTURE) defines the data type and size of an elementary item.\n"
            "Key patterns:\n"
            "  PIC 9(n)      — unsigned integer, n digits\n"
            "  PIC S9(n)     — signed integer (S = sign)\n"
            "  PIC 9(n)V9(d) — unsigned fixed-point, n integer digits, d decimal places\n"
            "  PIC S9(n)V9(d)— signed fixed-point (most common in financial COBOL)\n"
            "  PIC X(n)      — alphanumeric string, n characters\n"
            "  PIC A(n)      — alphabetic string\n"
            "  PIC Z(n)      — numeric with leading-zero suppression (display only)\n"
            "Python translation rules:\n"
            "  • PIC 9(n)     → int (or Decimal if used in arithmetic)\n"
            "  • PIC S9(n)V9(d) → decimal.Decimal, quantized to Decimal('0.' + '0'*d)\n"
            "  • PIC X(n)     → str, right-padded with spaces to length n\n"
            "  • COMP / COMP-3 (packed decimal) → still translate as Decimal, not int\n"
            "Example: PIC S9(7)V99 → Decimal value quantized to Decimal('0.01')"
        ),
    },
    {
        "id": "pic_comp",
        "topic": "COMP / COMP-3 / BINARY storage",
        "tags": ["COMP", "COMP-3", "BINARY", "PACKED-DECIMAL", "COMPUTATIONAL"],
        "text": (
            "USAGE COMP (COMPUTATIONAL) stores numbers in binary. "
            "USAGE COMP-3 (PACKED-DECIMAL) stores each digit in a nibble — common in IBM mainframe COBOL. "
            "USAGE BINARY is synonymous with COMP on most compilers. "
            "For Python translation, the storage format is irrelevant — translate purely based on the PIC clause. "
            "COMP-3 PIC S9(7)V99 → Decimal, quantized to Decimal('0.01'). "
            "Never use Python int for COMP-3 items that have a V (decimal point) in their PIC."
        ),
    },
    {
        "id": "level_numbers",
        "topic": "Level numbers and group items",
        "tags": ["01", "02", "03", "05", "10", "49", "66", "77", "88", "level number", "group item"],
        "text": (
            "COBOL uses level numbers 01–49 to express record hierarchies. "
            "01 = top-level record (group item or standalone elementary). "
            "02–49 = sub-fields nested within a group. "
            "77 = standalone elementary item (no group). "
            "66 = RENAMES clause — creates an alias over a range of fields. "
            "88 = condition name — a named boolean condition on a data item's value. "
            "Python translation: a 01-group with sub-fields becomes a dataclass. "
            "88-level items become properties or constants, e.g. IS_VALID = value == 'Y'. "
            "66-level RENAMES rarely appear in financial code; translate as a property."
        ),
    },
    {
        "id": "occurs",
        "topic": "OCCURS clause (arrays)",
        "tags": ["OCCURS", "TIMES", "INDEXED BY", "DEPENDING ON", "subscript", "array"],
        "text": (
            "OCCURS n TIMES declares a fixed-length array of n elements. "
            "OCCURS 1 TO n TIMES DEPENDING ON var declares a variable-length array. "
            "INDEXED BY creates an index variable (1-based, not 0-based like Python lists). "
            "Python translation: OCCURS n TIMES → Python list of length n. "
            "Subscript access TABLE(I) → table[i-1] (convert 1-based to 0-based). "
            "DEPENDING ON → dynamic list; use a Python list and validate length."
        ),
    },
    {
        "id": "redefines",
        "topic": "REDEFINES clause",
        "tags": ["REDEFINES"],
        "text": (
            "REDEFINES allows a data item to share storage with a previously defined item — "
            "a union in C terms. Example: 01 DATE-NUM PIC 9(8). 01 DATE-PARTS REDEFINES DATE-NUM. "
            "Python translation: REDEFINES is usually a struct-overlay trick. "
            "Translate as a property that reinterprets the original value, "
            "e.g. @property def date_parts(self): return parse_date(self.date_num). "
            "Never allocate separate memory for a REDEFINES target."
        ),
    },

    # ── PROCEDURE DIVISION ─────────────────────────────────────────────────
    {
        "id": "procedure_div",
        "topic": "PROCEDURE DIVISION",
        "tags": ["PROCEDURE DIVISION", "USING", "RETURNING", "paragraph", "section"],
        "text": (
            "PROCEDURE DIVISION contains the executable logic. "
            "It is divided into sections and paragraphs. "
            "PROCEDURE DIVISION USING data-names accepts parameters (like a function signature). "
            "PROCEDURE DIVISION RETURNING item returns a value. "
            "Python translation: the entire PROCEDURE DIVISION becomes one or more functions. "
            "Each COBOL paragraph (a named block ending at the next paragraph name) "
            "becomes a helper function or a labelled section in comments. "
            "The main paragraph at the top becomes the primary exported function."
        ),
    },
    {
        "id": "compute",
        "topic": "COMPUTE statement",
        "tags": ["COMPUTE", "arithmetic", "**", "ROUNDED", "ON SIZE ERROR"],
        "text": (
            "COMPUTE assigns the result of an arithmetic expression to one or more variables. "
            "Operators: + - * / ** (exponentiation). "
            "ROUNDED clause rounds to the destination PIC scale using half-up rounding. "
            "ON SIZE ERROR fires if the result exceeds the destination PIC capacity. "
            "Python translation:\n"
            "  COMPUTE RESULT = A * (1 + R / 100) ** N\n"
            "  → result = (a * (Decimal('1') + r / Decimal('100')) ** n).quantize(scale, ROUND_HALF_UP)\n"
            "Always import ROUND_HALF_UP from decimal when ROUNDED is present. "
            "Always quantize the final result to match the destination PIC clause scale."
        ),
    },
    {
        "id": "move",
        "topic": "MOVE statement",
        "tags": ["MOVE", "MOVE CORRESPONDING", "CORR"],
        "text": (
            "MOVE source TO dest copies a value, coercing types as needed. "
            "MOVE CORRESPONDING (CORR) copies fields with matching names between group items. "
            "Numeric MOVE to a PIC 9 item truncates or pads; to PIC X it space-fills. "
            "Python translation: MOVE A TO B → b = a (with quantize if dest is Decimal). "
            "MOVE CORRESPONDING → dict/dataclass field copy by name: "
            "for field in common_fields: dest.field = source.field"
        ),
    },
    {
        "id": "perform",
        "topic": "PERFORM statement (loops and subroutine calls)",
        "tags": ["PERFORM", "PERFORM UNTIL", "PERFORM VARYING", "PERFORM TIMES", "THRU"],
        "text": (
            "PERFORM paragraph-name calls a paragraph like a subroutine. "
            "PERFORM UNTIL condition loops until a condition is true (test-before by default). "
            "PERFORM WITH TEST AFTER loops with test at the end (do-while). "
            "PERFORM VARYING I FROM 1 BY 1 UNTIL I > N is a counted loop. "
            "PERFORM paragraph-1 THRU paragraph-2 executes a range of paragraphs. "
            "Python translations:\n"
            "  PERFORM para → para()  (call as function)\n"
            "  PERFORM UNTIL cond → while not cond: ...\n"
            "  PERFORM WITH TEST AFTER UNTIL cond → while True: ...; if cond: break\n"
            "  PERFORM VARYING I FROM 1 BY 1 UNTIL I > N → for i in range(1, n+1):"
        ),
    },
    {
        "id": "if_evaluate",
        "topic": "IF and EVALUATE statements",
        "tags": ["IF", "ELSE", "END-IF", "EVALUATE", "WHEN", "WHEN OTHER", "END-EVALUATE"],
        "text": (
            "IF condition THEN ... ELSE ... END-IF is standard conditional. "
            "EVALUATE is COBOL's switch/match statement. "
            "EVALUATE TRUE WHEN condition-1 ... WHEN condition-2 ... WHEN OTHER is like if/elif/else. "
            "EVALUATE subject WHEN value-1 ... is like Python match/case. "
            "Python translations:\n"
            "  IF A > B → if a > b:\n"
            "  EVALUATE TRUE / WHEN cond → if cond: / elif ...\n"
            "  EVALUATE subject WHEN val → match subject: / case val:"
        ),
    },
    {
        "id": "add_subtract_multiply_divide",
        "topic": "ADD / SUBTRACT / MULTIPLY / DIVIDE verbs",
        "tags": ["ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "GIVING", "REMAINDER", "ROUNDED"],
        "text": (
            "COBOL has individual verbs for each arithmetic operation:\n"
            "  ADD A TO B                → b += a\n"
            "  ADD A B GIVING C          → c = a + b\n"
            "  SUBTRACT A FROM B         → b -= a\n"
            "  SUBTRACT A FROM B GIVING C→ c = b - a\n"
            "  MULTIPLY A BY B           → b *= a\n"
            "  MULTIPLY A BY B GIVING C  → c = a * b\n"
            "  DIVIDE A INTO B           → b /= a  (note: INTO means b = b / a)\n"
            "  DIVIDE A INTO B GIVING C  → c = b / a\n"
            "  DIVIDE A BY B GIVING C REMAINDER R → c, r = divmod(b, a) (careful: operand order)\n"
            "All results must be Decimal; apply quantize() matching the GIVING/destination PIC."
        ),
    },
    {
        "id": "string_unstring",
        "topic": "STRING and UNSTRING verbs",
        "tags": ["STRING", "UNSTRING", "DELIMITED BY", "INTO", "POINTER", "TALLYING"],
        "text": (
            "STRING concatenates multiple items into one: STRING A DELIMITED BY SPACE B INTO C. "
            "UNSTRING splits a string by delimiter: UNSTRING C DELIMITED BY ',' INTO A B. "
            "Python translations:\n"
            "  STRING A DELIMITED SPACE B INTO C → c = a.rstrip() + b.rstrip()\n"
            "  UNSTRING C DELIMITED ',' INTO A B → a, b = c.split(',', 1)"
        ),
    },
    {
        "id": "inspect",
        "topic": "INSPECT verb",
        "tags": ["INSPECT", "TALLYING", "REPLACING", "CONVERTING"],
        "text": (
            "INSPECT counts or replaces characters in a string. "
            "INSPECT s TALLYING n FOR ALL 'X' → n = s.count('X'). "
            "INSPECT s REPLACING ALL 'X' BY 'Y' → s = s.replace('X', 'Y'). "
            "INSPECT s CONVERTING 'ABC' TO 'abc' → s = s.translate(str.maketrans('ABC','abc'))."
        ),
    },
    {
        "id": "read_write",
        "topic": "READ / WRITE / OPEN / CLOSE (file I/O)",
        "tags": ["READ", "WRITE", "OPEN", "CLOSE", "FILE SECTION", "FD", "SELECT", "AT END"],
        "text": (
            "COBOL file I/O uses SELECT (assigns a logical name to a physical file), "
            "FD (file descriptor defining the record layout), and OPEN/READ/WRITE/CLOSE verbs. "
            "READ file AT END sets a flag when EOF is reached. "
            "Python translation: use pathlib.Path / open() context managers. "
            "Sequential files → iterate lines with for line in f. "
            "Record-based WRITE → f.write(record + '\\n'). "
            "AT END condition → StopIteration / for-loop exhaustion."
        ),
    },
    {
        "id": "call_linkage",
        "topic": "CALL verb and LINKAGE SECTION",
        "tags": ["CALL", "LINKAGE SECTION", "BY REFERENCE", "BY CONTENT", "BY VALUE"],
        "text": (
            "CALL 'program-name' USING BY REFERENCE data-items invokes a sub-program, "
            "passing items by reference (mutates caller's storage). "
            "BY CONTENT passes a copy; BY VALUE passes a scalar value. "
            "LINKAGE SECTION defines the parameters received by a called sub-program. "
            "Python translation: CALL → function call. "
            "BY REFERENCE → pass mutable objects (list, dict, dataclass) and mutate in-place. "
            "BY CONTENT/VALUE → pass immutable values or copies."
        ),
    },
    {
        "id": "display_accept",
        "topic": "DISPLAY and ACCEPT verbs",
        "tags": ["DISPLAY", "ACCEPT", "FROM CONSOLE", "UPON CONSOLE"],
        "text": (
            "DISPLAY writes to stdout: DISPLAY 'text' variable. "
            "Multiple items on one DISPLAY are concatenated with no separator by default. "
            "DISPLAY x WITH NO ADVANCING suppresses the newline. "
            "ACCEPT reads from stdin or system clock: ACCEPT var FROM DATE/TIME/DAY. "
            "Python translations:\n"
            "  DISPLAY 'Result: ' RESULT → print(f'Result: {result}')\n"
            "  ACCEPT var → var = input()\n"
            "  ACCEPT var FROM DATE → var = datetime.date.today().strftime('%y%m%d')"
        ),
    },
    {
        "id": "stop_run_goback",
        "topic": "STOP RUN and GOBACK",
        "tags": ["STOP RUN", "GOBACK", "EXIT PROGRAM", "EXIT"],
        "text": (
            "STOP RUN terminates the entire run unit (like sys.exit(0) in Python). "
            "GOBACK returns to the caller if called as a sub-program, "
            "or terminates if it's the main program — equivalent to 'return' in Python. "
            "EXIT PROGRAM is like GOBACK but only valid inside a called sub-program. "
            "Python translation: STOP RUN at the end of main → omit (fall off end of function). "
            "STOP RUN mid-program → return (if in a function) or raise SystemExit."
        ),
    },

    # ── NUMERIC PRECISION ─────────────────────────────────────────────────
    {
        "id": "decimal_precision",
        "topic": "Decimal precision and rounding in financial COBOL",
        "tags": ["ROUNDED", "TRUNCATION", "decimal", "financial", "PIC V", "ON SIZE ERROR"],
        "text": (
            "COBOL arithmetic is always exact fixed-point decimal — there is no floating-point error. "
            "Python float (IEEE 754 binary) introduces rounding errors: 0.1 + 0.2 = 0.30000000000000004. "
            "This is a legal liability in banking. Always use decimal.Decimal in Python translations. "
            "Rules:\n"
            "  1. Set getcontext().prec = 28 at module level.\n"
            "  2. Construct Decimal values from strings, never floats: Decimal('5.25') not Decimal(5.25).\n"
            "  3. After any arithmetic, quantize to the destination PIC scale:\n"
            "     result = result.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n"
            "  4. For PIC S9(n)V9(d), the quantize pattern is '0.' + '0'*d.\n"
            "  5. Verify to 4 decimal places: abs(python_result - cobol_result) < Decimal('0.0001')."
        ),
    },
    {
        "id": "sign_handling",
        "topic": "Sign handling (SIGN clause, negative numbers)",
        "tags": ["SIGN", "LEADING", "TRAILING", "SEPARATE", "negative", "S9", "overpunch"],
        "text": (
            "COBOL signed numerics (PIC S9...) can store the sign as an overpunch in the last/first digit "
            "(default) or as a SEPARATE CHARACTER (a literal '+'/'-'). "
            "Overpunched signs are an EBCDIC encoding detail — GnuCOBOL handles this transparently. "
            "Python translation: use Decimal which handles signs natively. "
            "SIGN IS LEADING SEPARATE → parse the leading +/- character from strings if reading raw data. "
            "When the COBOL program outputs a negative value like -1234.56, "
            "match Python output with Decimal('-1234.56'), not abs(value)."
        ),
    },

    # ── INTRINSIC FUNCTIONS ───────────────────────────────────────────────
    {
        "id": "intrinsic_functions",
        "topic": "COBOL intrinsic functions",
        "tags": ["FUNCTION", "FUNCTION MAX", "FUNCTION MIN", "FUNCTION LENGTH",
                 "FUNCTION NUMVAL", "FUNCTION UPPER-CASE", "FUNCTION LOWER-CASE",
                 "FUNCTION TRIM", "FUNCTION CURRENT-DATE", "FUNCTION INTEGER-OF-DATE"],
        "text": (
            "COBOL intrinsic functions are called with the FUNCTION keyword:\n"
            "  FUNCTION MAX(a, b)       → max(a, b)\n"
            "  FUNCTION MIN(a, b)       → min(a, b)\n"
            "  FUNCTION LENGTH(x)       → len(x.rstrip()) or len(x)\n"
            "  FUNCTION NUMVAL(str)     → Decimal(str.strip())\n"
            "  FUNCTION UPPER-CASE(str) → str.upper()\n"
            "  FUNCTION LOWER-CASE(str) → str.lower()\n"
            "  FUNCTION TRIM(str)       → str.strip()\n"
            "  FUNCTION CURRENT-DATE    → datetime.datetime.now().strftime('%Y%m%d%H%M%S00+0000')\n"
            "  FUNCTION INTEGER-OF-DATE(yyyymmdd) → date ordinal conversion\n"
            "  FUNCTION MOD(a, b)       → a % b\n"
            "  FUNCTION ABS(x)          → abs(x)"
        ),
    },

    # ── COPY BOOKS ────────────────────────────────────────────────────────
    {
        "id": "copy_books",
        "topic": "COPY books",
        "tags": ["COPY", "REPLACING", "copybook", "include"],
        "text": (
            "COPY bookname inserts the contents of a copybook (a shared source file) at compile time — "
            "like C #include. COPY bookname REPLACING ==old== BY ==new== substitutes text. "
            "Python translation: inline the copybook content at the point of use, "
            "or import a shared module. If the copybook defines a record layout, "
            "make it a shared dataclass in a separate module and import it."
        ),
    },

    # ── BANKING-SPECIFIC PATTERNS ─────────────────────────────────────────
    {
        "id": "interest_calculation",
        "topic": "Interest calculation patterns",
        "tags": ["interest", "compound", "simple", "amortisation", "annuity", "loan", "rate"],
        "text": (
            "Common COBOL banking calculations and their Python equivalents:\n"
            "Compound interest: A = P * (1 + r/n)^(n*t)\n"
            "  → Decimal: result = principal * (1 + rate/100/freq) ** (freq * years)\n"
            "Simple interest: I = P * R * T / 100\n"
            "  → interest = principal * rate * time / Decimal('100')\n"
            "Monthly loan payment (annuity formula):\n"
            "  M = P * r*(1+r)^n / ((1+r)^n - 1)  where r = monthly_rate, n = num_payments\n"
            "Always use Decimal arithmetic. Always quantize to 2 decimal places (PIC V99) at end. "
            "Use ROUND_HALF_UP to match COBOL ROUNDED clause behaviour."
        ),
    },
    {
        "id": "payroll_patterns",
        "topic": "Payroll calculation patterns",
        "tags": ["payroll", "gross", "net", "tax", "deduction", "hours", "overtime"],
        "text": (
            "COBOL payroll programs typically follow this structure:\n"
            "  1. Read employee records from a file or WORKING-STORAGE.\n"
            "  2. COMPUTE GROSS-PAY = HOURS-WORKED * HOURLY-RATE\n"
            "     (overtime: if hours > 40, overtime portion * 1.5)\n"
            "  3. COMPUTE TAX-AMOUNT = GROSS-PAY * TAX-RATE / 100\n"
            "  4. COMPUTE NET-PAY = GROSS-PAY - TAX-AMOUNT - other deductions\n"
            "Python: use Decimal for all monetary fields. "
            "Tax bands often use 88-level condition names → translate as constants or an Enum. "
            "Quantize every intermediate result to avoid precision drift across many employees."
        ),
    },
    {
        "id": "date_handling",
        "topic": "Date handling in COBOL",
        "tags": ["DATE", "YYYYMMDD", "YYMMDD", "ACCEPT FROM DATE", "date arithmetic",
                 "INTEGER-OF-DATE", "DATE-OF-INTEGER"],
        "text": (
            "COBOL stores dates as integers: YYYYMMDD (8 digits) or YYMMDD (6 digits). "
            "ACCEPT WS-DATE FROM DATE gives YYMMDD; FROM DATE YYYYMMDD gives 8-digit form. "
            "FUNCTION INTEGER-OF-DATE converts YYYYMMDD to a day number (days since 1601-01-01). "
            "FUNCTION DATE-OF-INTEGER converts back. "
            "Python translation:\n"
            "  ACCEPT d FROM DATE → d = int(datetime.date.today().strftime('%y%m%d'))\n"
            "  FUNCTION INTEGER-OF-DATE(d) → (datetime.date(d//10000, d//100%100, d%100) - datetime.date(1600,12,31)).days\n"
            "  Date arithmetic: convert both to date objects, subtract for timedelta."
        ),
    },
    {
        "id": "batch_processing",
        "topic": "Batch file processing patterns",
        "tags": ["batch", "PERFORM UNTIL", "AT END", "READ", "WRITE", "EOF", "sequential file"],
        "text": (
            "Classic COBOL batch pattern: open a sequential file, read records in a loop, "
            "process each, write output, close. "
            "Python equivalent:\n"
            "  OPEN INPUT IN-FILE → with open(in_path) as f:\n"
            "  PERFORM UNTIL EOF → for line in f: (or while True: line=f.readline(); if not line: break)\n"
            "  READ IN-FILE AT END SET EOF TO TRUE → handled by for-loop exhaustion\n"
            "  WRITE OUT-RECORD → out_f.write(record)\n"
            "Use pathlib.Path for file paths. Use csv.DictReader for structured records."
        ),
    },
]
