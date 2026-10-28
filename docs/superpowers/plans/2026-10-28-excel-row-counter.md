# Excel Row Counter Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for inline execution. The user explicitly requested implementation of this scoped first step.

**Goal:** Receive Excel in a Feishu private chat, acknowledge receipt, then report nonempty row counts.

**Architecture:** Dedicated Python sample derived from robot_quick_start's Flask callback and message APIs. A SQLite job state machine separates webhook acceptance, receipt notification, bounded file processing, and final notification. One process runs Waitress and two background threads.

**Tech Stack:** Python 3.11+, Flask, requests, openpyxl, xlrd, pycryptodome, SQLite, Waitress; pytest and xlwt for tests.

**Spec:** docs/superpowers/specs/2026-10-28-excel-row-counter.md

## Global Constraints
- No YonSuite code or business data conversion.
- Count nonempty rows across all sheets, including headers; whitespace-only cells are empty; numeric zero, booleans and formulas count.
- Support .xlsx/.xls and verify 2000 rows. Limit file size, expanded ZIP size, scanned rows/cells and processing time.
- Private user messages only; require event signature/token/app identity. Never log credentials or raw event bodies.
- Persist before ACK. Receipt before processing, stable outgoing message UUIDs, separate notification retries, crash-recoverable job leases.

## Review Focus
- Styled empty rows, formulas without cached values, empty/hidden sheets: counting tests.
- Encrypted callbacks, forged/stale signatures and malformed authenticated events: webhook tests.
- Duplicate delivery, worker crashes and notification failures: job/worker integration tests.
- User attachments must use the message-resource endpoint, not the app-owned files download endpoint: real local HTTP contract test.
- Corrupt/mislabeled Excel, expanded ZIP abuse and oversized download: bounded-parser/API tests.

### Task 1: Count workbooks
Files: excel_row_counter/python/excel_counter/excel.py, tests/test_excel.py.
Interface: count_rows(path, ...) -> dict[str, int]; WorkbookError contains a safe user-facing reason.
- [ ] Write fixtures and assertions for xlsx/xls, 2000 rows, styles, sparse rows, formulas, bad files and limits; observe failures.
- [ ] Implement bounded count_rows and run parser tests.

### Task 2: Receive and persist callbacks
Files: config.py, security.py, jobs.py, web.py, tests/test_workflow.py.
Interfaces: Settings; JobStore.enqueue/claim/advance/retry; create_app(settings, store).
- [ ] Write signed/encrypted callback, bad authorization, deduplication and lease recovery tests; observe failures.
- [ ] Implement webhook and SQLite queue; run workflow tests.

### Task 3: Process and notify
Files: feishu.py, worker.py, __main__.py, tests/test_api.py and workflow integration tests.
Interfaces: FeishuClient.send_text(chat_id,text,uuid), download(message_id,file_key,path); Workers.notify_once/count_once.
- [ ] Write local HTTP contracts and receipt-before-download/final-retry tests; observe failures.
- [ ] Implement API token caching, bounded streaming downloads, job processing and Waitress runtime; run full sample suite.

### Task 4: Make Codespaces runnable
Files: README.md (sample), .env.example, requirements*.txt, .gitignore, root README links, .devcontainer/excel-row-counter/devcontainer.json.
- [ ] Document exact Feishu permissions, event/port configuration, secrets, count semantics, limits and recovery.
- [ ] Run full pytest suite, compile check and git diff --check; conduct final code review.
- [ ] Deliver feature branch and draft PR for Codespaces testing, preserving main.

## Execution notes
- Existing workspace was projectless. Cloned public repository into work/lark-Ucs and created feat/excel-row-counter; this new checkout provides isolation without another worktree.
- No existing project-wide Python suite; this repository contains independent language samples. Run the complete new sample suite.
- User's direct implementation instruction supersedes a further plan approval handoff.
- Implementation and local verification: 28 tests pass on Python 3.12, including actual Waitress startup and local HTTP contracts. No real tenant credentials used.
- Independent review found a streaming deadline defect; a trickling-response regression first failed, then passed with a subprocess-enforced download deadline. Graceful shutdown waits for bounded processing cleanup. xls formula counting uses saved values, as documented, because xlrd does not expose uncached formulas.
- User requested repository migration to D:/Work/Code/lark-Ucs. Copied repository and Git metadata, verified 323 SHA256 file hashes, then retained the source as a migration backup. Active working directory is D:/Work/Code/lark-Ucs.
