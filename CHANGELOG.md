# Changelog

## 1.1.0 — Ledger, free and open source

- Switch Ledger to MIT with explicit owner approval, including commercial use. Preserve earlier releases and third-party notices.
- Audit the running app against public source: the latest planning, allowance, balance-status and paid/unpaid behavior is included.
- Refresh the README, actual-interface screenshots with fictional records, setup and release downloads.
- Publish Ledger on the Animus Machinae releases page. Existing household records remain compatible; no data migration is required.

## 1.0.1 — Paid or unpaid purchases

- Remove the pending purchase stage for daily spending, groceries, and discretionary purchases.
- Stop reserving paid purchases against the manually entered checking balance a second time, including older recorded purchases.
- Remove the checking reconciliation checkbox and pending purchase labels.
- Preserve future unpaid essentials, unspent daily budgets, and recorded discretionary allowance usage.
- Add five paid-purchase regression cases and update the release/setup instructions. No database migration or purchase re-entry is required.

## 1.0.0 — First public Ledger release

- Plan at least six calendar months ahead with dated bills, income, daily spending, and groceries.
- Separate the discretionary plan from the current conservative biweekly allowance.
- Build an adjustable emergency cushion through monthly due-soon contributions that stop at the goal.
- Reserve pending purchases until checking includes them; keep planned and recorded states distinct.
- Use mortgage grace dates conditionally where supplied, while preserving the normal due-date target.
- Show six clear financial totals, an interactive forecast, and collapsed detail sections.
- Simplify navigation to Today, Money, and Advanced.
- Add portable Docker/native setup, locked Python dependencies, refreshed frontend dependencies, privacy guidance, and fictional UI screenshots.
- Publish clean source history under PolyForm Noncommercial 1.0.0; commercial use requires separate permission.

No bank integration or automatic financial transactions are included. Plans depend on accurate, current records.
