       IDENTIFICATION DIVISION.
       PROGRAM-ID. TXNBATCH.
      *----------------------------------------------------------------
      * TRANSACTION BATCH PROCESSOR
      * Reads a VSAM KSDS flat-file export of binary transaction records.
      * Each record uses COMP-3 (packed decimal) for amounts and dates,
      * and COMP (binary) for the sequence counter.
      * Decodes all records, accumulates totals, outputs summary.
      *----------------------------------------------------------------
       DATA DIVISION.
       FILE SECTION.
       FD  TRANSACTION-FILE
           RECORDING MODE F
           BLOCK CONTAINS 0 RECORDS
           RECORD CONTAINS 24 CHARACTERS.
       01  TRANSACTION-RECORD.
           05 TXN-ID         PIC X(10).
           05 TXN-DATE       PIC 9(8)     COMP-3.
           05 TXN-AMOUNT     PIC S9(9)V99 COMP-3.
           05 TXN-TYPE       PIC X(2).
           05 TXN-STATUS     PIC X(1).

       WORKING-STORAGE SECTION.
       01 WS-EOF-FLAG        PIC X(1)     VALUE 'N'.
          88 WS-EOF                       VALUE 'Y'.
       01 WS-RECORD-COUNT    PIC 9(9)     COMP VALUE ZERO.
       01 WS-TOTAL-AMOUNT    PIC S9(12)V99 COMP-3 VALUE ZERO.
       01 WS-ERROR-COUNT     PIC 9(9)     COMP VALUE ZERO.

       01 WS-SUMMARY-LINE.
          05 SUM-RECORDS     PIC Z(9).
          05 FILLER          PIC X(3)     VALUE ' / '.
          05 SUM-ERRORS      PIC Z(9).
          05 FILLER          PIC X(9)     VALUE ' TOTAL: $'.
          05 SUM-TOTAL       PIC Z(9)9.99.

       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT TRANSACTION-FILE

           PERFORM UNTIL WS-EOF
               READ TRANSACTION-FILE
                   AT END MOVE 'Y' TO WS-EOF-FLAG
                   NOT AT END
                       PERFORM PROCESS-RECORD
               END-READ
           END-PERFORM

           CLOSE TRANSACTION-FILE
           PERFORM WRITE-SUMMARY
           STOP RUN.

       PROCESS-RECORD.
           EVALUATE TXN-STATUS
               WHEN 'A'
                   ADD TXN-AMOUNT TO WS-TOTAL-AMOUNT
                   ADD 1 TO WS-RECORD-COUNT
               WHEN 'E'
                   ADD 1 TO WS-ERROR-COUNT
               WHEN OTHER
                   ADD 1 TO WS-ERROR-COUNT
           END-EVALUATE.

       WRITE-SUMMARY.
           MOVE WS-RECORD-COUNT TO SUM-RECORDS
           MOVE WS-ERROR-COUNT  TO SUM-ERRORS
           MOVE WS-TOTAL-AMOUNT TO SUM-TOTAL
           DISPLAY WS-SUMMARY-LINE.
