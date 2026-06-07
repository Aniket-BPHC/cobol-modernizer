"""
slm/vsam/data.py
────────────────
Synthetic training dataset for the VSAM/mainframe-data SLM.

Each entry is a (copybook + context → Python decoder) pair.

The Python output covers:
  • EBCDIC ↔ ASCII conversion (using the `ebcdic` or `codecs` library)
  • Packed-decimal (COMP-3) decoding via struct + nibble unpacking
  • Binary (COMP / COMP-4) field decoding via struct.unpack
  • Fixed-width record slicing based on PIC byte widths
  • VSAM KSDS/ESDS/RRDS file patterns (sequential read loops)
  • Complete dataclass-based record layouts with typed fields
"""

from __future__ import annotations

VSAM_TRAINING_PAIRS: list[dict[str, str]] = [

    # ══════════════════════════════════════════════════════════════════════
    # EBCDIC DECODING
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
Decode an EBCDIC-encoded fixed-width record.
Copybook:
01 CUSTOMER-RECORD.
   05 CUST-ID       PIC X(8).
   05 CUST-NAME     PIC X(30).
   05 CUST-TYPE     PIC X(2).
Record is EBCDIC encoded, total length 40 bytes.""",
        "output": """\
import codecs
from dataclasses import dataclass

@dataclass
class CustomerRecord:
    cust_id:   str  # PIC X(8)
    cust_name: str  # PIC X(30)
    cust_type: str  # PIC X(2)

    @classmethod
    def from_bytes(cls, raw: bytes) -> 'CustomerRecord':
        \"\"\"Decode a 40-byte EBCDIC-encoded CUSTOMER-RECORD.\"\"\"
        assert len(raw) == 40, f"Expected 40 bytes, got {len(raw)}"
        # Decode each field from EBCDIC (cp037 = IBM US EBCDIC)
        cust_id   = raw[0:8].decode('cp037').strip()
        cust_name = raw[8:38].decode('cp037').strip()
        cust_type = raw[38:40].decode('cp037').strip()
        return cls(cust_id=cust_id, cust_name=cust_name, cust_type=cust_type)

    def to_bytes(self) -> bytes:
        \"\"\"Encode back to 40-byte EBCDIC record.\"\"\"
        return (
            self.cust_id.ljust(8).encode('cp037') +
            self.cust_name.ljust(30).encode('cp037') +
            self.cust_type.ljust(2).encode('cp037')
        )
""",
    },
    {
        "input": """\
Decode EBCDIC record with mixed text and zoned decimal fields.
Copybook:
01 EMPLOYEE-RECORD.
   05 EMP-ID        PIC X(6).
   05 EMP-SURNAME   PIC X(20).
   05 EMP-DEPT      PIC X(4).
   05 EMP-SALARY    PIC 9(7)V99.
Total: 39 bytes. EBCDIC encoding.""",
        "output": """\
import codecs
from dataclasses import dataclass
from decimal import Decimal

@dataclass
class EmployeeRecord:
    emp_id:      str      # PIC X(6)
    emp_surname: str      # PIC X(20)
    emp_dept:    str      # PIC X(4)
    emp_salary:  Decimal  # PIC 9(7)V99 — zoned decimal, 9 bytes

    @classmethod
    def from_bytes(cls, raw: bytes) -> 'EmployeeRecord':
        \"\"\"Decode a 39-byte EBCDIC EMPLOYEE-RECORD.\"\"\"
        assert len(raw) == 39, f"Expected 39 bytes, got {len(raw)}"
        emp_id      = raw[0:6].decode('cp037').strip()
        emp_surname = raw[6:26].decode('cp037').strip()
        emp_dept    = raw[26:30].decode('cp037').strip()
        # Zoned decimal PIC 9(7)V99: 9 digit characters, implied decimal after digit 7
        salary_str = raw[30:39].decode('cp037')
        emp_salary  = Decimal(salary_str[:7] + '.' + salary_str[7:])
        return cls(
            emp_id=emp_id, emp_surname=emp_surname,
            emp_dept=emp_dept, emp_salary=emp_salary,
        )
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # PACKED DECIMAL (COMP-3)
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
Decode a COMP-3 (packed decimal) field.
Field definition: PIC S9(7)V99 COMP-3.
Storage: 5 bytes packed decimal (ceil((9+1)/2) = 5 bytes).
The last nibble is the sign: C=positive, D=negative, F=unsigned.""",
        "output": """\
from decimal import Decimal

def decode_comp3_s9_7v99(raw: bytes) -> Decimal:
    \"\"\"
    Decode PIC S9(7)V99 COMP-3 — 5 bytes packed decimal.
    Format: each byte holds 2 BCD digits; last nibble = sign (C=+, D=-, F=unsigned).
    \"\"\"
    assert len(raw) == 5, f"PIC S9(7)V99 COMP-3 requires 5 bytes, got {len(raw)}"
    digits = ''
    for byte in raw:
        digits += str((byte >> 4) & 0x0F)   # high nibble
        digits += str(byte & 0x0F)           # low nibble
    sign_nibble = byte & 0x0F  # last nibble of last byte
    # Remove sign nibble from digit string
    digit_str = digits[:-1]    # 9 significant digits
    # Apply implied decimal point: PIC 9(7)V99 → 7 integer + 2 decimal digits
    value = Decimal(digit_str[:7] + '.' + digit_str[7:9])
    if sign_nibble == 0xD:
        value = -value
    return value

def encode_comp3_s9_7v99(value: Decimal) -> bytes:
    \"\"\"Encode a Decimal back to PIC S9(7)V99 COMP-3 (5 bytes).\"\"\"
    sign_nibble = 0xD if value < 0 else 0xC
    abs_str = str(abs(value).quantize(Decimal('0.01'))).replace('.', '').zfill(9)
    packed = bytearray(5)
    for i in range(4):
        packed[i] = (int(abs_str[i*2]) << 4) | int(abs_str[i*2+1])
    packed[4] = (int(abs_str[8]) << 4) | sign_nibble
    return bytes(packed)
""",
    },
    {
        "input": """\
Decode packed decimal field PIC 9(5) COMP-3.
Storage: 3 bytes. Unsigned (no sign in PIC, treated as positive).""",
        "output": """\
from decimal import Decimal

def decode_comp3_9_5(raw: bytes) -> int:
    \"\"\"
    Decode PIC 9(5) COMP-3 — 3 bytes unsigned packed decimal.
    PIC 9(5) has 5 digits; ceil((5+1)/2)=3 bytes.
    \"\"\"
    assert len(raw) == 3, f"PIC 9(5) COMP-3 requires 3 bytes, got {len(raw)}"
    digits = ''
    for byte in raw:
        digits += str((byte >> 4) & 0x0F)
        digits += str(byte & 0x0F)
    # Last nibble is sign/filler (F for unsigned); take first 5 digit nibbles
    return int(digits[:5])

def encode_comp3_9_5(value: int) -> bytes:
    \"\"\"Encode an int to PIC 9(5) COMP-3 (3 bytes).\"\"\"
    s = str(value).zfill(5)
    packed = bytearray(3)
    packed[0] = (int(s[0]) << 4) | int(s[1])
    packed[1] = (int(s[2]) << 4) | int(s[3])
    packed[2] = (int(s[4]) << 4) | 0xF  # unsigned sign nibble
    return bytes(packed)
""",
    },
    {
        "input": """\
Decode a full financial record with COMP-3 fields.
Copybook:
01 TRANSACTION-RECORD.
   05 TXN-ID        PIC X(10).
   05 TXN-DATE      PIC 9(8) COMP-3.
   05 TXN-AMOUNT    PIC S9(9)V99 COMP-3.
   05 TXN-TYPE      PIC X(2).
   05 TXN-STATUS    PIC X(1).
Total: 10 + 5 + 6 + 2 + 1 = 24 bytes. EBCDIC text fields.""",
        "output": """\
import struct
from dataclasses import dataclass
from decimal import Decimal

@dataclass
class TransactionRecord:
    txn_id:     str      # PIC X(10)
    txn_date:   int      # PIC 9(8) COMP-3, YYYYMMDD
    txn_amount: Decimal  # PIC S9(9)V99 COMP-3
    txn_type:   str      # PIC X(2)
    txn_status: str      # PIC X(1)

    @classmethod
    def from_bytes(cls, raw: bytes) -> 'TransactionRecord':
        \"\"\"Decode a 24-byte EBCDIC TRANSACTION-RECORD with COMP-3 fields.\"\"\"
        assert len(raw) == 24, f"Expected 24 bytes, got {len(raw)}"

        txn_id   = raw[0:10].decode('cp037').strip()
        txn_date = _decode_comp3_9_8(raw[10:15])
        txn_amount = _decode_comp3_s9_9v99(raw[15:21])
        txn_type   = raw[21:23].decode('cp037').strip()
        txn_status = raw[23:24].decode('cp037').strip()

        return cls(
            txn_id=txn_id, txn_date=txn_date,
            txn_amount=txn_amount, txn_type=txn_type, txn_status=txn_status,
        )


def _decode_comp3_9_8(raw: bytes) -> int:
    \"\"\"PIC 9(8) COMP-3 → int (5 bytes, 8 digit unsigned).\"\"\"
    digits = ''.join(f'{b:02X}' for b in raw)[:-1]  # hex chars minus sign nibble
    return int(digits[:8])

def _decode_comp3_s9_9v99(raw: bytes) -> Decimal:
    \"\"\"PIC S9(9)V99 COMP-3 → Decimal (6 bytes, 11 digits signed).\"\"\"
    nibbles = []
    for b in raw:
        nibbles.append((b >> 4) & 0x0F)
        nibbles.append(b & 0x0F)
    sign = nibbles[-1]
    digits = ''.join(str(n) for n in nibbles[:-1])  # 11 digits
    val = Decimal(digits[:9] + '.' + digits[9:11])
    return -val if sign == 0xD else val
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # BINARY (COMP / COMP-4)
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
Decode COMP (binary) fields in a mainframe record.
Copybook:
01 COUNTER-RECORD.
   05 CTR-ID        PIC X(4).
   05 CTR-COUNT     PIC 9(4) COMP.
   05 CTR-MAX       PIC 9(9) COMP.
   05 CTR-SIGNED    PIC S9(4) COMP.
COMP storage: PIC 9(1-4)=2 bytes, PIC 9(5-9)=4 bytes, PIC 9(10-18)=8 bytes.
Big-endian (mainframe byte order).""",
        "output": """\
import struct
from dataclasses import dataclass

@dataclass
class CounterRecord:
    ctr_id:     str  # PIC X(4)
    ctr_count:  int  # PIC 9(4) COMP  — 2 bytes unsigned big-endian
    ctr_max:    int  # PIC 9(9) COMP  — 4 bytes unsigned big-endian
    ctr_signed: int  # PIC S9(4) COMP — 2 bytes signed big-endian

    @classmethod
    def from_bytes(cls, raw: bytes) -> 'CounterRecord':
        \"\"\"Decode a 12-byte COUNTER-RECORD with COMP binary fields.\"\"\"
        assert len(raw) == 12, f"Expected 12 bytes, got {len(raw)}"
        ctr_id     = raw[0:4].decode('cp037').strip()
        ctr_count  = struct.unpack('>H', raw[4:6])[0]   # unsigned short big-endian
        ctr_max    = struct.unpack('>I', raw[6:10])[0]  # unsigned int big-endian
        ctr_signed = struct.unpack('>h', raw[10:12])[0] # signed short big-endian
        return cls(
            ctr_id=ctr_id, ctr_count=ctr_count,
            ctr_max=ctr_max, ctr_signed=ctr_signed,
        )

    def to_bytes(self) -> bytes:
        return (
            self.ctr_id.ljust(4).encode('cp037') +
            struct.pack('>H', self.ctr_count) +
            struct.pack('>I', self.ctr_max) +
            struct.pack('>h', self.ctr_signed)
        )
""",
    },
    {
        "input": """\
Decode a PIC S9(18) COMP field (8-byte signed binary, mainframe long).
Field occupies 8 bytes, big-endian, two's complement.""",
        "output": """\
import struct

def decode_comp_s9_18(raw: bytes) -> int:
    \"\"\"
    Decode PIC S9(18) COMP — 8 bytes signed big-endian (mainframe long).
    Equivalent to Java long / C int64_t.
    \"\"\"
    assert len(raw) == 8, f"PIC S9(18) COMP requires 8 bytes, got {len(raw)}"
    return struct.unpack('>q', raw)[0]  # signed long long big-endian

def encode_comp_s9_18(value: int) -> bytes:
    \"\"\"Encode int to PIC S9(18) COMP (8 bytes signed big-endian).\"\"\"
    return struct.pack('>q', value)
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # VSAM FILE READING PATTERNS
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
Read a VSAM KSDS sequential file and decode records.
Record length: 80 bytes fixed.
File is raw binary (EBCDIC on mainframe, exported as binary flat file).
Decode each record using CustomerRecord.from_bytes().""",
        "output": """\
from pathlib import Path
from typing import Iterator

def read_vsam_ksds_sequential(
    file_path: str | Path,
    record_length: int = 80,
) -> Iterator['CustomerRecord']:
    \"\"\"
    Read a VSAM KSDS flat file export sequentially.
    Yields decoded CustomerRecord objects one at a time.

    Args:
        file_path: Path to the binary flat file (VSAM export).
        record_length: Fixed record length in bytes (default 80).
    \"\"\"
    path = Path(file_path)
    with path.open('rb') as f:
        while True:
            raw = f.read(record_length)
            if not raw:
                break  # EOF
            if len(raw) < record_length:
                raise ValueError(
                    f"Incomplete record: expected {record_length} bytes, "
                    f"got {len(raw)} at offset {f.tell() - len(raw)}"
                )
            yield CustomerRecord.from_bytes(raw)
""",
    },
    {
        "input": """\
Read a VSAM ESDS (entry-sequenced) file and process all records.
Record length: 120 bytes. Records contain TransactionRecord layout.
Count records, accumulate total amount, report summary.""",
        "output": """\
from decimal import Decimal
from pathlib import Path

def process_vsam_esds(file_path: str | Path) -> dict:
    \"\"\"
    Read a VSAM ESDS flat file, decode TransactionRecord entries,
    return summary statistics.

    Returns:
        dict with keys: record_count, total_amount, error_count
    \"\"\"
    RECORD_LENGTH = 120
    record_count  = 0
    total_amount  = Decimal('0.00')
    error_count   = 0

    with open(file_path, 'rb') as f:
        while True:
            raw = f.read(RECORD_LENGTH)
            if not raw:
                break
            if len(raw) < RECORD_LENGTH:
                error_count += 1
                continue
            try:
                rec = TransactionRecord.from_bytes(raw)
                record_count += 1
                total_amount += rec.txn_amount
            except (ValueError, AssertionError) as exc:
                error_count += 1

    return {
        'record_count': record_count,
        'total_amount': total_amount,
        'error_count':  error_count,
    }
""",
    },
    {
        "input": """\
Read a VSAM RRDS (relative record) file with random access by RRN.
Record length: 50 bytes. Access record number WS-RRN.""",
        "output": """\
from pathlib import Path

def read_vsam_rrds_by_rrn(
    file_path: str | Path,
    rrn: int,
    record_length: int = 50,
) -> bytes | None:
    \"\"\"
    Random-access read from a VSAM RRDS flat-file export by Relative Record Number.
    RRDS records are 1-based; record N starts at byte offset (N-1)*record_length.

    Returns raw bytes for the record, or None if RRN is out of range.
    \"\"\"
    assert rrn >= 1, f"RRDS RRN is 1-based; got {rrn}"
    offset = (rrn - 1) * record_length
    with open(file_path, 'rb') as f:
        f.seek(offset)
        raw = f.read(record_length)
    if len(raw) < record_length:
        return None  # past end of file
    return raw
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # OCCURS + REDEFINES IN BINARY RECORDS
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
Decode a record with OCCURS in a binary copybook.
Copybook:
01 PRICE-TABLE.
   05 PT-ENTRY OCCURS 5 TIMES.
      10 PT-CODE    PIC X(4).
      10 PT-PRICE   PIC S9(7)V99 COMP-3.
Each PT-ENTRY is 4 + 5 = 9 bytes. Total: 45 bytes.""",
        "output": """\
from dataclasses import dataclass
from decimal import Decimal
from typing import List

@dataclass
class PtEntry:
    pt_code:  str      # PIC X(4)
    pt_price: Decimal  # PIC S9(7)V99 COMP-3, 5 bytes

@dataclass
class PriceTable:
    pt_entry: List[PtEntry]  # OCCURS 5 TIMES

    @classmethod
    def from_bytes(cls, raw: bytes) -> 'PriceTable':
        \"\"\"Decode a 45-byte PRICE-TABLE with 5 OCCURS entries.\"\"\"
        assert len(raw) == 45, f"Expected 45 bytes, got {len(raw)}"
        ENTRY_SIZE = 9
        entries = []
        for i in range(5):
            offset = i * ENTRY_SIZE
            chunk  = raw[offset : offset + ENTRY_SIZE]
            pt_code  = chunk[0:4].decode('cp037').strip()
            pt_price = _decode_comp3_s9_7v99(chunk[4:9])
            entries.append(PtEntry(pt_code=pt_code, pt_price=pt_price))
        return cls(pt_entry=entries)


def _decode_comp3_s9_7v99(raw: bytes) -> Decimal:
    \"\"\"PIC S9(7)V99 COMP-3 — 5 bytes signed packed decimal.\"\"\"
    nibbles = [(b >> 4) & 0xF for b in raw] + [b & 0xF for b in raw]
    # interleave: high nibble, low nibble for each byte
    nibbles = []
    for b in raw:
        nibbles.append((b >> 4) & 0xF)
        nibbles.append(b & 0xF)
    sign = nibbles[-1]
    digits = ''.join(str(n) for n in nibbles[:-1])  # 9 digits
    val = Decimal(digits[:7] + '.' + digits[7:9])
    return -val if sign == 0xD else val
""",
    },
    {
        "input": """\
Decode a record using REDEFINES — two views of the same bytes.
Copybook:
01 DATE-RECORD.
   05 DATE-FULL   PIC 9(8).
   05 DATE-PARTS  REDEFINES DATE-FULL.
      10 DATE-YYYY PIC 9(4).
      10 DATE-MM   PIC 9(2).
      10 DATE-DD   PIC 9(2).
4 bytes COMP or 8 bytes zoned decimal. Use zoned decimal (EBCDIC digits).""",
        "output": """\
from dataclasses import dataclass
import datetime

@dataclass
class DateRecord:
    _raw_date: int  # PIC 9(8) — YYYYMMDD as integer

    @classmethod
    def from_bytes(cls, raw: bytes) -> 'DateRecord':
        \"\"\"Decode an 8-byte zoned decimal DATE-RECORD.\"\"\"
        assert len(raw) == 8, f"Expected 8 bytes, got {len(raw)}"
        date_str = raw.decode('cp037')
        return cls(_raw_date=int(date_str))

    # DATE-FULL view
    @property
    def date_full(self) -> int:
        return self._raw_date

    # DATE-PARTS REDEFINES view — same bytes, different interpretation
    @property
    def date_yyyy(self) -> int:
        return self._raw_date // 10000

    @property
    def date_mm(self) -> int:
        return (self._raw_date // 100) % 100

    @property
    def date_dd(self) -> int:
        return self._raw_date % 100

    def as_date(self) -> datetime.date:
        return datetime.date(self.date_yyyy, self.date_mm, self.date_dd)
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # SIGN HANDLING (overpunched / separate sign)
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
Decode a zoned decimal field with EBCDIC overpunch sign.
Field: PIC S9(5) — 5 bytes EBCDIC, sign embedded in last digit via overpunch.
Overpunch table (last byte):
  Positive: '{' = 0, 'A' = 1, 'B' = 2, ..., 'I' = 9
  Negative: '}' = 0, 'J' = 1, 'K' = 2, ..., 'R' = 9""",
        "output": """\
def decode_zoned_decimal_s9_5(raw: bytes) -> int:
    \"\"\"
    Decode PIC S9(5) EBCDIC zoned decimal with overpunch sign.
    The sign is encoded in the zone (high nibble) of the last byte:
      0xC = positive, 0xD = negative, 0xF = unsigned.
    \"\"\"
    assert len(raw) == 5, f"PIC S9(5) requires 5 bytes, got {len(raw)}"
    digits = ''
    for byte in raw[:-1]:
        # For non-final bytes: low nibble is the digit
        digits += str(byte & 0x0F)
    last_byte = raw[-1]
    zone    = (last_byte >> 4) & 0x0F
    last_digit = last_byte & 0x0F
    digits += str(last_digit)
    value = int(digits)
    # zone 0xD = negative
    if zone == 0xD:
        value = -value
    return value

# OVERPUNCH character lookup (alternative — direct EBCDIC table)
_OVERPUNCH_POS = {0x7B: 0, 0xC1: 1, 0xC2: 2, 0xC3: 3, 0xC4: 4,
                  0xC5: 5, 0xC6: 6, 0xC7: 7, 0xC8: 8, 0xC9: 9}
_OVERPUNCH_NEG = {0x7D: 0, 0xD1: 1, 0xD2: 2, 0xD3: 3, 0xD4: 4,
                  0xD5: 5, 0xD6: 6, 0xD7: 7, 0xD8: 8, 0xD9: 9}
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # COMPLETE BATCH PIPELINE PATTERNS
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
Build a complete batch pipeline that reads a VSAM flat file export,
decodes each record using a copybook dataclass, and writes a CSV output.
Input: binary VSAM KSDS export, EmployeeRecord layout, 39 bytes per record.
Output: CSV with columns emp_id, emp_surname, emp_dept, emp_salary.""",
        "output": """\
import csv
from pathlib import Path
from decimal import Decimal

def vsam_to_csv(input_path: str, output_path: str) -> int:
    \"\"\"
    Convert a VSAM KSDS binary flat file to CSV.
    Returns the number of records processed.
    \"\"\"
    RECORD_LENGTH = 39
    records_processed = 0

    with open(input_path, 'rb') as fin, \\
         open(output_path, 'w', newline='', encoding='utf-8') as fout:

        writer = csv.DictWriter(
            fout,
            fieldnames=['emp_id', 'emp_surname', 'emp_dept', 'emp_salary'],
        )
        writer.writeheader()

        while True:
            raw = fin.read(RECORD_LENGTH)
            if not raw:
                break
            if len(raw) < RECORD_LENGTH:
                raise ValueError(f"Truncated record at offset {fin.tell()}")

            rec = EmployeeRecord.from_bytes(raw)
            writer.writerow({
                'emp_id':      rec.emp_id,
                'emp_surname': rec.emp_surname,
                'emp_dept':    rec.emp_dept,
                'emp_salary':  str(rec.emp_salary),
            })
            records_processed += 1

    return records_processed
""",
    },
    {
        "input": """\
Write a pytest test suite for COMP-3 decode/encode round-trip.
Test the decode_comp3_s9_7v99 and encode_comp3_s9_7v99 functions.""",
        "output": """\
import pytest
from decimal import Decimal
from vsam_decoder import decode_comp3_s9_7v99, encode_comp3_s9_7v99

class TestComp3S9_7V99:
    def test_positive_value(self):
        # 10000.00 → 9 digits: 001000000, packed = 00 10 00 00 0C
        raw = bytes([0x00, 0x10, 0x00, 0x00, 0x0C])
        assert decode_comp3_s9_7v99(raw) == Decimal('1000.00')

    def test_negative_value(self):
        raw = bytes([0x00, 0x10, 0x00, 0x00, 0x0D])
        assert decode_comp3_s9_7v99(raw) == Decimal('-1000.00')

    def test_round_trip_positive(self):
        original = Decimal('12345.67')
        encoded  = encode_comp3_s9_7v99(original)
        assert len(encoded) == 5
        decoded  = decode_comp3_s9_7v99(encoded)
        assert abs(decoded - original) < Decimal('0.0001')

    def test_round_trip_negative(self):
        original = Decimal('-9876.54')
        assert abs(decode_comp3_s9_7v99(encode_comp3_s9_7v99(original)) - original) < Decimal('0.0001')

    def test_zero(self):
        raw = bytes([0x00, 0x00, 0x00, 0x00, 0x0C])
        assert decode_comp3_s9_7v99(raw) == Decimal('0.00')

    def test_wrong_length_raises(self):
        with pytest.raises(AssertionError):
            decode_comp3_s9_7v99(bytes([0x00, 0x01]))
""",
    },
]
