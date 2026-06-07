       IDENTIFICATION DIVISION.
       PROGRAM-ID. CUSTINQ.
      *----------------------------------------------------------------
      * CUSTOMER INQUIRY — reads a customer record via CICS file I/O,
      * updates the balance, and writes an audit entry to a TS queue.
      *----------------------------------------------------------------
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-CUST-ID        PIC X(8)      VALUE '00000001'.
       01 WS-RESP           PIC 9(4) COMP VALUE ZERO.
       01 WS-RESP2          PIC 9(4) COMP VALUE ZERO.
       01 WS-DEPOSIT-AMT    PIC S9(9)V99  VALUE 500.00.
       01 WS-QUEUE-NAME     PIC X(8)      VALUE 'AUDITQ  '.
       01 WS-AUDIT-MSG      PIC X(40)     VALUE SPACES.
       01 WS-MSG-LEN        PIC 9(4) COMP VALUE 40.
       01 WS-ITEM-NUM       PIC 9(4) COMP VALUE ZERO.

       01 WS-CUSTOMER-REC.
          05 CUST-ID        PIC X(8).
          05 CUST-NAME      PIC X(30).
          05 CUST-BALANCE   PIC S9(9)V99.
          05 CUST-STATUS    PIC X(1).

       PROCEDURE DIVISION.
       MAIN-PARA.
           EXEC CICS READ
               FILE('CUSTOMER')
               INTO(WS-CUSTOMER-REC)
               RIDFLD(WS-CUST-ID)
               UPDATE
               RESP(WS-RESP)
           END-EXEC

           IF WS-RESP = DFHRESP(NOTFND)
               MOVE 'CUSTOMER NOT FOUND' TO WS-AUDIT-MSG
               EXEC CICS WRITEQ TD
                   QUEUE('CSMT')
                   FROM(WS-AUDIT-MSG)
                   LENGTH(WS-MSG-LEN)
               END-EXEC
               EXEC CICS RETURN END-EXEC
           END-IF

           ADD WS-DEPOSIT-AMT TO CUST-BALANCE OF WS-CUSTOMER-REC

           EXEC CICS REWRITE
               FILE('CUSTOMER')
               FROM(WS-CUSTOMER-REC)
               RESP(WS-RESP)
           END-EXEC

           MOVE 'BALANCE UPDATED OK' TO WS-AUDIT-MSG
           EXEC CICS WRITEQ TS
               QUEUE(WS-QUEUE-NAME)
               FROM(WS-AUDIT-MSG)
               LENGTH(WS-MSG-LEN)
               ITEM(WS-ITEM-NUM)
               RESP(WS-RESP)
           END-EXEC

           EXEC CICS SYNCPOINT
               RESP(WS-RESP)
           END-EXEC

           EXEC CICS RETURN END-EXEC.
