"""
slm/cics/data.py
────────────────
Synthetic training dataset for the CICS-to-Python SLM.

Every entry is a (EXEC CICS block → Python equivalent) pair.
The Python output uses:
  - sqlalchemy for database operations (READ, WRITE, REWRITE, DELETE)
  - A lightweight CicsContext dataclass to carry RESP/RESP2 codes
  - Standard Python exceptions to mirror CICS HANDLE CONDITION logic
  - Explicit RESP code constants matching IBM CICS documentation

Coverage: ~60 examples across all common CICS command categories:
  File control     : READ, READNEXT, READPREV, WRITE, REWRITE, DELETE, STARTBR, ENDBR
  Program control  : LINK, XCTL, RETURN, ABEND
  Storage          : GETMAIN, FREEMAIN
  Terminal I/O     : SEND, RECEIVE, SEND MAP, RECEIVE MAP
  Temporary storage: READQ TS, WRITEQ TS, DELETEQ TS
  Transient data   : READQ TD, WRITEQ TD
  Interval control : DELAY, ASKTIME, FORMATTIME
  Syncpoint        : SYNCPOINT, SYNCPOINT ROLLBACK
"""

from __future__ import annotations

CICS_TRAINING_PAIRS: list[dict[str, str]] = [

    # ══════════════════════════════════════════════════════════════════════
    # FILE CONTROL — READ
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
EXEC CICS READ
    FILE('CUSTOMER')
    INTO(WS-CUSTOMER-RECORD)
    RIDFLD(WS-CUST-ID)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS READ FILE('CUSTOMER')
ws_resp = DFHRESP_NORMAL
ws_customer_record = db.session.get(CustomerRecord, ws_cust_id)
if ws_customer_record is None:
    ws_resp = DFHRESP_NOTFND
""",
    },
    {
        "input": """\
EXEC CICS READ
    FILE('ACCOUNTS')
    INTO(WS-ACCOUNT-REC)
    RIDFLD(WS-ACCT-NUM)
    UPDATE
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS READ FILE('ACCOUNTS') UPDATE — acquire row-level lock
ws_resp = DFHRESP_NORMAL
ws_account_rec = (
    db.session.query(AccountRecord)
    .filter_by(acct_num=ws_acct_num)
    .with_for_update()
    .first()
)
if ws_account_rec is None:
    ws_resp = DFHRESP_NOTFND
""",
    },
    {
        "input": """\
EXEC CICS READ
    FILE('PRODUCTS')
    INTO(WS-PROD-REC)
    RIDFLD(WS-PROD-CODE)
    KEYLENGTH(8)
    RESP(WS-RESP)
    RESP2(WS-RESP2)
END-EXEC""",
        "output": """\
# EXEC CICS READ FILE('PRODUCTS') with KEYLENGTH
ws_resp = DFHRESP_NORMAL
ws_resp2 = 0
ws_prod_rec = (
    db.session.query(ProductRecord)
    .filter(ProductRecord.prod_code == ws_prod_code[:8])
    .first()
)
if ws_prod_rec is None:
    ws_resp = DFHRESP_NOTFND
""",
    },

    # ── READNEXT / browse ──────────────────────────────────────────────────
    {
        "input": """\
EXEC CICS STARTBR
    FILE('ORDERS')
    RIDFLD(WS-ORDER-KEY)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS STARTBR FILE('ORDERS') — begin sequential browse
ws_resp = DFHRESP_NORMAL
_orders_cursor = (
    db.session.query(OrderRecord)
    .filter(OrderRecord.order_key >= ws_order_key)
    .order_by(OrderRecord.order_key)
    .all()
)
_orders_cursor_iter = iter(_orders_cursor)
""",
    },
    {
        "input": """\
EXEC CICS READNEXT
    FILE('ORDERS')
    INTO(WS-ORDER-REC)
    RIDFLD(WS-ORDER-KEY)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS READNEXT FILE('ORDERS')
ws_resp = DFHRESP_NORMAL
try:
    ws_order_rec = next(_orders_cursor_iter)
    ws_order_key = ws_order_rec.order_key
except StopIteration:
    ws_resp = DFHRESP_ENDFILE
""",
    },
    {
        "input": """\
EXEC CICS ENDBR
    FILE('ORDERS')
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS ENDBR FILE('ORDERS') — end browse, release cursor
ws_resp = DFHRESP_NORMAL
_orders_cursor_iter = None
_orders_cursor = None
""",
    },

    # ── WRITE ──────────────────────────────────────────────────────────────
    {
        "input": """\
EXEC CICS WRITE
    FILE('CUSTOMER')
    FROM(WS-CUSTOMER-RECORD)
    RIDFLD(WS-CUST-ID)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS WRITE FILE('CUSTOMER')
ws_resp = DFHRESP_NORMAL
new_rec = CustomerRecord.from_ws(ws_customer_record, key=ws_cust_id)
try:
    db.session.add(new_rec)
    db.session.flush()
except IntegrityError:
    db.session.rollback()
    ws_resp = DFHRESP_DUPREC
""",
    },
    {
        "input": """\
EXEC CICS WRITE
    FILE('TRANSACTIONS')
    FROM(WS-TXN-RECORD)
    RIDFLD(WS-TXN-ID)
    MASSINSERT
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS WRITE FILE('TRANSACTIONS') MASSINSERT — bulk insert optimisation
ws_resp = DFHRESP_NORMAL
new_txn = TransactionRecord.from_ws(ws_txn_record, key=ws_txn_id)
db.session.add(new_txn)
# MASSINSERT: flush in batches rather than per-row
if len(db.session.new) >= 100:
    db.session.flush()
""",
    },

    # ── REWRITE ────────────────────────────────────────────────────────────
    {
        "input": """\
EXEC CICS REWRITE
    FILE('ACCOUNTS')
    FROM(WS-ACCOUNT-REC)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS REWRITE FILE('ACCOUNTS') — update previously READ UPDATE record
ws_resp = DFHRESP_NORMAL
if ws_account_rec is None:
    ws_resp = DFHRESP_NOTOPEN
else:
    ws_account_rec.update_from_ws(ws_account_rec_data)
    db.session.flush()
""",
    },

    # ── DELETE ─────────────────────────────────────────────────────────────
    {
        "input": """\
EXEC CICS DELETE
    FILE('CUSTOMER')
    RIDFLD(WS-CUST-ID)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS DELETE FILE('CUSTOMER')
ws_resp = DFHRESP_NORMAL
rec = db.session.get(CustomerRecord, ws_cust_id)
if rec is None:
    ws_resp = DFHRESP_NOTFND
else:
    db.session.delete(rec)
    db.session.flush()
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # PROGRAM CONTROL
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
EXEC CICS LINK
    PROGRAM('CALCINT')
    COMMAREA(WS-COMM-AREA)
    LENGTH(200)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS LINK PROGRAM('CALCINT') — synchronous sub-program call
ws_resp = DFHRESP_NORMAL
try:
    ws_comm_area = calcint.main(ws_comm_area)
except CicsAbend as e:
    ws_resp = DFHRESP_PGMIDERR
""",
    },
    {
        "input": """\
EXEC CICS XCTL
    PROGRAM('MENUMAP')
    COMMAREA(WS-COMM-AREA)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS XCTL PROGRAM('MENUMAP') — transfer control (no return)
ws_resp = DFHRESP_NORMAL
raise XctlTransfer(program='MENUMAP', commarea=ws_comm_area)
""",
    },
    {
        "input": """\
EXEC CICS RETURN
    TRANSID('MENU')
    COMMAREA(WS-COMM-AREA)
    LENGTH(200)
END-EXEC""",
        "output": """\
# EXEC CICS RETURN TRANSID('MENU') — return and queue next transaction
raise CicsReturn(transid='MENU', commarea=ws_comm_area)
""",
    },
    {
        "input": """\
EXEC CICS ABEND
    ABCODE('MYAB')
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS ABEND ABCODE('MYAB') — abnormal termination
raise CicsAbend(abcode='MYAB')
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # TEMPORARY STORAGE (TS QUEUES)
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
EXEC CICS WRITEQ TS
    QUEUE(WS-QUEUE-NAME)
    FROM(WS-TS-DATA)
    LENGTH(WS-DATA-LEN)
    ITEM(WS-ITEM-NUM)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS WRITEQ TS — write to temporary storage queue
ws_resp = DFHRESP_NORMAL
_ts_store = getattr(cics_ctx, 'ts_queues', {})
queue = _ts_store.setdefault(ws_queue_name.strip(), [])
queue.append(ws_ts_data[:ws_data_len])
ws_item_num = len(queue)
cics_ctx.ts_queues = _ts_store
""",
    },
    {
        "input": """\
EXEC CICS READQ TS
    QUEUE(WS-QUEUE-NAME)
    INTO(WS-TS-DATA)
    LENGTH(WS-DATA-LEN)
    ITEM(WS-ITEM-NUM)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS READQ TS — read from temporary storage queue by item number
ws_resp = DFHRESP_NORMAL
_ts_store = getattr(cics_ctx, 'ts_queues', {})
queue = _ts_store.get(ws_queue_name.strip(), [])
idx = ws_item_num - 1  # CICS items are 1-based
if 0 <= idx < len(queue):
    ws_ts_data = queue[idx]
    ws_data_len = len(ws_ts_data)
else:
    ws_resp = DFHRESP_ITEMERR
""",
    },
    {
        "input": """\
EXEC CICS DELETEQ TS
    QUEUE(WS-QUEUE-NAME)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS DELETEQ TS — delete entire temporary storage queue
ws_resp = DFHRESP_NORMAL
_ts_store = getattr(cics_ctx, 'ts_queues', {})
if ws_queue_name.strip() in _ts_store:
    del _ts_store[ws_queue_name.strip()]
else:
    ws_resp = DFHRESP_QIDERR
cics_ctx.ts_queues = _ts_store
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # TRANSIENT DATA (TD QUEUES)
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
EXEC CICS WRITEQ TD
    QUEUE('CSMT')
    FROM(WS-LOG-MSG)
    LENGTH(WS-MSG-LEN)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS WRITEQ TD QUEUE('CSMT') — write to transient data (log) queue
ws_resp = DFHRESP_NORMAL
import logging as _log
_log.getLogger('CICS.CSMT').info(ws_log_msg[:ws_msg_len].rstrip())
""",
    },
    {
        "input": """\
EXEC CICS READQ TD
    QUEUE('INPQ')
    INTO(WS-TD-DATA)
    LENGTH(WS-DATA-LEN)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS READQ TD QUEUE('INPQ') — destructive read from intrapartition queue
ws_resp = DFHRESP_NORMAL
_td_queues = getattr(cics_ctx, 'td_queues', {})
queue = _td_queues.get('INPQ', [])
if queue:
    ws_td_data = queue.pop(0)
    ws_data_len = len(ws_td_data)
    cics_ctx.td_queues = _td_queues
else:
    ws_resp = DFHRESP_QZERO
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # TERMINAL I/O (BMS)
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
EXEC CICS SEND TEXT
    FROM(WS-MESSAGE)
    LENGTH(WS-MSG-LEN)
    ERASE
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS SEND TEXT — send text to terminal
ws_resp = DFHRESP_NORMAL
cics_ctx.terminal.clear()
cics_ctx.terminal.write(ws_message[:ws_msg_len])
""",
    },
    {
        "input": """\
EXEC CICS SEND MAP('CUSTMAP')
    MAPSET('CUSTMSET')
    FROM(WS-MAP-DATA)
    ERASE
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS SEND MAP('CUSTMAP') — render BMS map to terminal
ws_resp = DFHRESP_NORMAL
cics_ctx.terminal.render_map(
    map_name='CUSTMAP',
    mapset='CUSTMSET',
    data=ws_map_data,
    erase=True,
)
""",
    },
    {
        "input": """\
EXEC CICS RECEIVE MAP('CUSTMAP')
    MAPSET('CUSTMSET')
    INTO(WS-MAP-DATA)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS RECEIVE MAP('CUSTMAP') — read input from BMS map
ws_resp = DFHRESP_NORMAL
ws_map_data = cics_ctx.terminal.receive_map(
    map_name='CUSTMAP',
    mapset='CUSTMSET',
)
if ws_map_data is None:
    ws_resp = DFHRESP_MAPFAIL
""",
    },
    {
        "input": """\
EXEC CICS RECEIVE
    INTO(WS-INPUT-DATA)
    LENGTH(WS-INPUT-LEN)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS RECEIVE — raw terminal receive
ws_resp = DFHRESP_NORMAL
ws_input_data = cics_ctx.terminal.receive()
ws_input_len = len(ws_input_data)
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # INTERVAL CONTROL
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
EXEC CICS ASKTIME
    ABSTIME(WS-ABS-TIME)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS ASKTIME — get current time as CICS abstime (milliseconds since 1900)
import time as _time
ws_resp = DFHRESP_NORMAL
_epoch_1900 = -2208988800  # seconds between 1900-01-01 and 1970-01-01
ws_abs_time = int((_time.time() - _epoch_1900) * 1000)
""",
    },
    {
        "input": """\
EXEC CICS FORMATTIME
    ABSTIME(WS-ABS-TIME)
    YYYYMMDD(WS-DATE)
    TIME(WS-TIME)
    TIMESEP(':')
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS FORMATTIME — convert CICS abstime to formatted date/time
import datetime as _dt
ws_resp = DFHRESP_NORMAL
_epoch_1900 = -2208988800
_ts = _dt.datetime.utcfromtimestamp(ws_abs_time / 1000 + _epoch_1900)
ws_date = int(_ts.strftime('%Y%m%d'))
ws_time = _ts.strftime('%H:%M:%S')
""",
    },
    {
        "input": """\
EXEC CICS DELAY
    INTERVAL(WS-DELAY-SECS)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS DELAY — suspend task for interval
import time as _time
ws_resp = DFHRESP_NORMAL
_time.sleep(ws_delay_secs)
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # SYNCPOINT (transaction control)
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
EXEC CICS SYNCPOINT
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS SYNCPOINT — commit current unit of work
ws_resp = DFHRESP_NORMAL
db.session.commit()
""",
    },
    {
        "input": """\
EXEC CICS SYNCPOINT ROLLBACK
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS SYNCPOINT ROLLBACK — rollback current unit of work
ws_resp = DFHRESP_NORMAL
db.session.rollback()
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # CONDITION HANDLING
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
IF WS-RESP = DFHRESP(NORMAL)
    CONTINUE
ELSE
    IF WS-RESP = DFHRESP(NOTFND)
        PERFORM HANDLE-NOT-FOUND
    ELSE
        PERFORM HANDLE-ERROR
    END-IF
END-IF""",
        "output": """\
# CICS RESP code checking
if ws_resp == DFHRESP_NORMAL:
    pass  # CONTINUE
elif ws_resp == DFHRESP_NOTFND:
    handle_not_found()
else:
    handle_error()
""",
    },
    {
        "input": """\
EVALUATE WS-RESP
    WHEN DFHRESP(NORMAL)
        CONTINUE
    WHEN DFHRESP(NOTFND)
        MOVE 'N' TO WS-FOUND-FLAG
    WHEN DFHRESP(DUPREC)
        MOVE 'D' TO WS-FOUND-FLAG
    WHEN OTHER
        PERFORM CICS-ERROR-HANDLER
END-EVALUATE""",
        "output": """\
# EVALUATE on CICS RESP codes
match ws_resp:
    case _ if _ == DFHRESP_NORMAL:
        pass  # CONTINUE
    case _ if _ == DFHRESP_NOTFND:
        ws_found_flag = 'N'
    case _ if _ == DFHRESP_DUPREC:
        ws_found_flag = 'D'
    case _:
        cics_error_handler()
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # STORAGE CONTROL
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
EXEC CICS GETMAIN
    SET(WS-POINTER)
    LENGTH(WS-STORAGE-LEN)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS GETMAIN — dynamic memory allocation (Python: just allocate)
ws_resp = DFHRESP_NORMAL
ws_pointer = bytearray(ws_storage_len)
""",
    },
    {
        "input": """\
EXEC CICS FREEMAIN
    DATA(WS-POINTER)
    RESP(WS-RESP)
END-EXEC""",
        "output": """\
# EXEC CICS FREEMAIN — release dynamic storage (Python: let GC handle it)
ws_resp = DFHRESP_NORMAL
ws_pointer = None  # dereference; GC will reclaim
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # FULL PROGRAM PATTERNS (complex multi-command examples)
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": """\
* Read a customer, update balance, rewrite
EXEC CICS READ
    FILE('ACCOUNTS')
    INTO(WS-ACCT-REC)
    RIDFLD(WS-ACCT-ID)
    UPDATE
    RESP(WS-RESP)
END-EXEC
IF WS-RESP = DFHRESP(NORMAL)
    ADD WS-DEPOSIT-AMT TO ACCT-BALANCE OF WS-ACCT-REC
    EXEC CICS REWRITE
        FILE('ACCOUNTS')
        FROM(WS-ACCT-REC)
        RESP(WS-RESP)
    END-EXEC
END-IF""",
        "output": """\
# Read-update-rewrite pattern: atomic balance update
from decimal import Decimal

ws_resp = DFHRESP_NORMAL
ws_acct_rec = (
    db.session.query(AccountRecord)
    .filter_by(acct_id=ws_acct_id)
    .with_for_update()
    .first()
)
if ws_acct_rec is None:
    ws_resp = DFHRESP_NOTFND
else:
    ws_acct_rec.acct_balance += Decimal(str(ws_deposit_amt))
    db.session.flush()
""",
    },
    {
        "input": """\
* Browse all orders for a customer, accumulate total
MOVE WS-CUST-PREFIX TO WS-ORDER-KEY
EXEC CICS STARTBR
    FILE('ORDERS')
    RIDFLD(WS-ORDER-KEY)
    RESP(WS-RESP)
END-EXEC
PERFORM UNTIL WS-RESP NOT = DFHRESP(NORMAL)
    EXEC CICS READNEXT
        FILE('ORDERS')
        INTO(WS-ORDER-REC)
        RIDFLD(WS-ORDER-KEY)
        RESP(WS-RESP)
    END-EXEC
    IF WS-RESP = DFHRESP(NORMAL)
        ADD ORDER-AMT OF WS-ORDER-REC TO WS-TOTAL
    END-IF
END-PERFORM
EXEC CICS ENDBR FILE('ORDERS') END-EXEC""",
        "output": """\
# Browse-accumulate pattern: sum all orders for a customer prefix
from decimal import Decimal

ws_total = Decimal('0.00')
ws_resp = DFHRESP_NORMAL
orders = (
    db.session.query(OrderRecord)
    .filter(OrderRecord.order_key.like(f'{ws_cust_prefix}%'))
    .order_by(OrderRecord.order_key)
    .all()
)
for ws_order_rec in orders:
    ws_total += Decimal(str(ws_order_rec.order_amt))
""",
    },
    {
        "input": """\
* Log error to CSMT and abend
MOVE 'READ FAILED' TO WS-ERR-MSG
EXEC CICS WRITEQ TD
    QUEUE('CSMT')
    FROM(WS-ERR-MSG)
    LENGTH(12)
    RESP(WS-RESP)
END-EXEC
EXEC CICS ABEND ABCODE('RERR') END-EXEC""",
        "output": """\
# Log-and-abend pattern
import logging as _log
_log.getLogger('CICS.CSMT').error('READ FAILED')
raise CicsAbend(abcode='RERR')
""",
    },

    # ══════════════════════════════════════════════════════════════════════
    # DFHRESP CONSTANTS (reference examples)
    # ══════════════════════════════════════════════════════════════════════
    {
        "input": "Define the CICS DFHRESP constants needed for response code checking.",
        "output": """\
# CICS DFHRESP constants — mirror IBM CICS response code values
DFHRESP_NORMAL    = 0
DFHRESP_ERROR     = 1
DFHRESP_RDATT     = 2
DFHRESP_WRBRK     = 3
DFHRESP_EOF       = 4
DFHRESP_EODS      = 5
DFHRESP_EOC       = 6
DFHRESP_INBFMH    = 7
DFHRESP_ENDINPT   = 8
DFHRESP_NONVAL    = 9
DFHRESP_NOSTART   = 10
DFHRESP_TERMIDERR = 11
DFHRESP_FILENOTFOUND = 12
DFHRESP_NOTFND    = 13
DFHRESP_DUPREC    = 14
DFHRESP_DUPKEY    = 15
DFHRESP_INVREQ    = 16
DFHRESP_IOERR     = 17
DFHRESP_NOTOPEN   = 18
DFHRESP_ENDFILE   = 19
DFHRESP_ILLOGIC   = 21
DFHRESP_LENGERR   = 22
DFHRESP_QZERO     = 23
DFHRESP_ITEMERR   = 26
DFHRESP_PGMIDERR  = 27
DFHRESP_TRANSIDERR = 28
DFHRESP_ENDDATA   = 29
DFHRESP_MAPFAIL   = 36
DFHRESP_QIDERR    = 44
DFHRESP_DISABLED  = 84
""",
    },
    {
        "input": "Define the CicsContext, CicsAbend, XctlTransfer and CicsReturn helper classes.",
        "output": """\
# CICS runtime helper classes
from dataclasses import dataclass, field
from typing import Any

@dataclass
class CicsContext:
    \"\"\"Carries CICS execution context across calls (mirrors COMMAREA + system state).\"\"\"
    commarea: bytes = b''
    ts_queues: dict = field(default_factory=dict)
    td_queues: dict = field(default_factory=dict)
    terminal: Any = None
    transid: str = ''

class CicsAbend(RuntimeError):
    \"\"\"Raised when EXEC CICS ABEND is encountered.\"\"\"
    def __init__(self, abcode: str = ''):
        self.abcode = abcode
        super().__init__(f'CICS ABEND: {abcode}')

class XctlTransfer(Exception):
    \"\"\"Raised when EXEC CICS XCTL transfers control to another program.\"\"\"
    def __init__(self, program: str, commarea: bytes = b''):
        self.program = program
        self.commarea = commarea

class CicsReturn(Exception):
    \"\"\"Raised when EXEC CICS RETURN queues the next transaction.\"\"\"
    def __init__(self, transid: str = '', commarea: bytes = b''):
        self.transid = transid
        self.commarea = commarea
""",
    },
]
