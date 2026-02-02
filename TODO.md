# TODO

Single source of truth for short-term gaps/mismatches found during code review.

## Feature Plan items with no code evidence (gap)
- [x] **Server Invite Protection**: admin-approved allowlist + approval workflow (`FEATURE_PLAN.md` line 11). Implemented in `modules/invite_protection.py` (module: `inviteprotection`, defaults disabled).
- [x] **Search User Art**: channel-restricted image search + GIF filtering + paginated message links (`FEATURE_PLAN.md` line 34). Implemented in `modules/art_search.py` (module: `artsearch`, defaults disabled).
- [x] **Commission Review System**: reviews + dispute workflow (upheld/removed/amended) (`FEATURE_PLAN.md` line 35). Implemented in `modules/commission_reviews.py` (module: `commissionreviews`, defaults disabled).
- [ ] **Plan ↔ Code audit pass**: reconcile `FEATURE_PLAN.md` / `FEATURE_FUTUREPLAN.md` checkboxes vs repo reality; update docs or add stubs so “planned vs shipped” is clear.

## Vouch system (plan vs code mismatch / incomplete workflow)
- [x] Add mod verification flow: `vouch verify <vouch_id>` command (`FEATURE_FUTUREPLAN.md` lines 610–624, esp. line 623).
- [x] Implement/store `verified_by_mod` setting via service/storage (future plan lists `verify_vouch(vouch_id, mod_id)`).
- [ ] Decide scoring behavior: currently trust scoring only counts verified vouches (`services/trust_service.py` lines 170–190), so unverified vouches likely have little/no impact beyond “no vouches” baseline.
- [x] Update docs to reflect current state: vouch create/list/given/remove exist in `modules/trust.py` (lines 30–96), and verify now exists.

## Federation removal (deprecation + cleanup checklist)
- [x] Federation feature removed from runtime, web UI, and docs (2026-02-02).
