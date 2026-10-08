# Odoo module migration rules

These rules are appended to Claude's system prompt for every migration run. To use your own
(house style, licensing policy, per-module notes), put a `MIGRATION_RULES.md` in your workspace or
set `MS_RULES_FILE`. Your file then replaces this one.

## Working rules

1. **Edit only the copy.** You work in a copy of the module. Never modify the original module or
   the Odoo source trees.
2. **Verify, don't assume.** Grep and read every model, field, method, decorator, view XML ID,
   `inherit_id`, xpath target and JS import in the *target* Odoo source before relying on it.
   Anything renamed or removed between versions must be found and fixed.
3. **Don't trust the source version.** Custom modules often carry constructs deprecated several
   releases ago (`attrs`, `<tree>`, `_sql_constraints`, `odoo.define`, …). Port to the current idiom
   of the target version, not just the minimum needed to load.
4. **Preserve behaviour.** Keep business logic, field names and XML IDs unless the target version
   forces a change. Document every forced change. Add no new features and do no unrelated
   refactors.
5. **Never loosen security.** Keep group restrictions and record rules at least as strict as the
   source. Flag risky raw SQL, `sudo()` use or public routes under "Needs review" instead of
   silently changing who can do what.
6. **Check core overlap.** If the target version's core now provides the module's feature, say so
   under "Needs review". Don't delete the feature.
7. **Manifest.** Set `version` to `<target>.x.y.z`; a wrong prefix makes the module silently
   uninstallable. Remove unused `depends`, and keep `license` explicit.
8. **Tests.** Port tests to the new APIs. Never delete or weaken a test just to make it pass.
9. **Silent failures.** Some breakages only show when a page renders or a route is hit (see below).
   Prefer fixes you can confirm in the target source over guesses.

## Verified API changes up to Odoo 20

Each item was confirmed against Odoo 20.0 community source. Check the target tree when migrating
to an older version.

### ORM / Python
- `_sql_constraints` is removed. Use class attributes `models.Constraint('sql', 'message')`
  (example: `addons/marketing_card/models/card_campaign_tag.py`).
- `Model._context` / `_uid` / `_cr` are deprecated since 19.0. Use `self.env.context`,
  `self.env.uid` and `self.env.cr`.
- `read_group(domain, fields=[...], groupby=[...])` is gone. Use
  `_read_group(domain, groupby, aggregates)`, which returns `list[tuple]`.
- `stock.move.quantity_done` was renamed to `quantity`.
- `stock.return.picking` (and its `.line` model) is removed. The return flow is now
  `picking._create_return()`, which copies all move lines. For partial returns, adjust the copied
  moves afterwards.
- `account.payment.state` values are `draft/paid/reconciled/canceled/rejected`: `'posted'` no
  longer exists. `account.payment` has no `line_ids` of its own; use `payment.move_id.line_ids`.
- `payment.controllers.post_processing.PaymentPostProcessing` / `poll_status()` are removed.
  Override `payment.transaction._post_process()` instead (see
  `addons/sale/models/payment_transaction.py`).
- Binary fields:
  - Reading returns a `BinaryValue`.
  - Writing `bytes` raises `TypeError`; write a base64 `str` instead.
  - `ir.attachment.create({'datas': …})` is silently ignored; use `raw=` with bytes.
  - In QWeb, render images as `data:image/png;base64,#{rec.field.to_base64()}`.
- `odoo.addons.html_editor.tools.get_video_embed_code` is removed, with no replacement.
- `ir.cron` has no `numbercall` / `doall` fields any more.

### Security
- `ir.model.access` and `ir.rule` are **removed** and unified into **`ir.access`**
  (`odoo/addons/base/models/ir_access.py`).
  - Rename `security/ir.model.access.csv` to `security/ir.access.csv`. The CSV loader takes the
    model from the file name.
  - Header: `id,name,model_id,group_id/id,operation,domain`.
    - `model_id` is the bare model name, e.g. `sale.order`.
    - `group_id/id` is an xmlid, or blank for everyone.
    - `operation` combines `c`/`r`/`u`/`d` (u = write, d = unlink).
    - `domain` is optional.
  - A former record rule becomes a row with a `domain`, or a `<record model="ir.access">`.
  - Odoo ships `odoo/upgrade_code/19.4-00-ir-access.py`. It merges and drops rows it considers
    redundant, so convert by hand if you need a 1:1 mapping.
- A route that creates or writes as `sudo()` bypasses ACLs. Add an explicit `check_access(...)`
  for the real user.

### Controllers / HTTP
- `content_disposition` moved to `odoo.http.stream` and `serialize_exception` to
  `odoo.http.dispatcher`. They are no longer importable from `odoo.http`.
- `request.website` no longer exists. Use `request.env.website`. It fails only when the route is
  hit.
- `type='json'` routes are now `type='jsonrpc'`.
- `CustomerPortal._prepare_home_portal_values(counters)` is removed. Use
  `_prepare_portal_counter_values(counter)`, which returns `(model, domain, access)`.
- Portal home cards now come from `portal.entry` data records (see
  `addons/sale/data/portal_entry_data.xml`), not from xpath inheritance of `portal.portal_my_home`.

### Views / QWeb
- `<tree>` is now `<list>` (also in `view_mode`), and `kanban-box` is now `t-name="card"`.
- `attrs=` / `states=` are removed. Use direct `invisible` / `readonly` / `required` expressions.
- **`t-esc` and `t-raw` do nothing in server-rendered QWeb** (views, reports, website, portal and
  mail templates). The element renders empty with only a log warning. Use `t-out`. Client-side
  OWL templates under `static/src/` are a different engine and are unaffected.
- `t-key` is OWL-only; on server templates it only logs a warning.
- `ir.actions.report` has no `report_file` field; keep `report_name` only.

### JavaScript / OWL
- The `publicWidget` system (`@web/legacy/js/public/public_widget`) is **removed**. Port to the
  Interaction framework:
  - `import { Interaction } from "@web/public/interaction"`
  - a class with `static selector`, `dynamicContent` and `setup()`
  - register it with `registry.category("public.interactions").add(...)`
  - examples: `addons/website_links/static/src/interactions/`.
- `odoo.define(...)` modules must become ES modules.
- Import from `@odoo/owl` rather than using the global `owl` object.
- OWL 3 (verified against `web/static/src/views/fields/char/char_field.js`):
  - refs are `signal.ref()`, used as `t-ref="this.input"`
  - `props = useProps(...)`
  - `useInputField({ ref, getValue })`, not `refName`
  - templates access members through `this.`

### Odoo's own rewriter
`odoo-bin upgrade_code --script <name> --dry-run --addons-path=<parent> --glob '<module>/**/*'`
automates a few mechanical renames (tree→list, sql constraints, ir.access, …). It does **not**
catch render-time breakage (`t-esc`, `request.website`, binary fields) or removed models. Treat
it as a helper, not a substitute for installing and rendering.

## Report format
Finish with:
```
## Changes made
- …
## Remaining TODOs / Needs review
- …
```
