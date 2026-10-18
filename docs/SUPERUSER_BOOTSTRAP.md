# Superuser / operator accounts

Borrowers sign in with their phone number (`USERNAME_FIELD`). Back-office
operators sign in with a **display name** — for example `Ken Admin` — through
either:

* the Django admin at `/admin/`, or
* `POST /api/v1/users/login/` with `{"username": "Ken Admin", "password": "…"}`.

Both routes resolve the identifier through `UserManager.get_by_natural_key()`,
which matches a username first and a normalised MSISDN second. A borrower can
keep using `{"phone": "2547…"}` unchanged — the field is additive and the
behaviour for existing clients is identical.

## Roles granted

`bootstrap_superuser` is idempotent (safe to re-run) and grants every
administrative role the platform understands:

| Role | Effect |
| --- | --- |
| `is_staff` | Django admin access |
| `is_superuser` | every model permission via `PermissionsMixin` |
| `is_active` | account can authenticate |
| `Super Admin` group | created if missing; kept in sync with the full permission table |
| every other group | joins them too, so a group-gated view cannot hide an action |
| `verification_status = verified` | the operator is never blocked by a borrower review gate |

## Create or rotate the account

```bash
cd Backend
python manage.py bootstrap_superuser \
  --username "Ken Admin" \
  --password "<password>" \
  --phone 254700000000 \
  --national-id 000000
```

The phone number and national ID are database constraints (both unique), so an
operator account still needs values for them. Use a reserved placeholder
number unless the account should receive SMS codes.

The same command rotates the password of an existing operator: it updates the
account in place and prints what changed, so it doubles as the documented
recovery path.

## Environment variables

Every argument can be supplied by the environment instead of the command line,
which is how a deploy hook would seed the first operator:

| Variable | Argument |
| --- | --- |
| `PLATFORM_SUPERUSER_USERNAME` | `--username` |
| `PLATFORM_SUPERUSER_PASSWORD` | `--password` |
| `PLATFORM_SUPERUSER_PHONE` | `--phone` |
| `PLATFORM_SUPERUSER_NATIONAL_ID` | `--national-id` |

```bash
PLATFORM_SUPERUSER_USERNAME="Ken Admin" \
PLATFORM_SUPERUSER_PASSWORD="…" \
PLATFORM_SUPERUSER_PHONE=254700000000 \
PLATFORM_SUPERUSER_NATIONAL_ID=000000 \
python manage.py bootstrap_superuser
```

With `--no-input` the command never prompts and fails instead when a value is
missing, which keeps it usable from CI.

## Password policy

The account password is run through `AUTH_PASSWORD_VALIDATORS` (minimum 8
characters, not a common password, not all numeric). Pass
`--skip-password-validation` to override — only for a break-glass reset, and
rotate the password immediately afterwards.

## Security notes

* No credential is stored in this repository. The command reads the password
  from the argument, the environment, or an interactive `getpass` prompt.
* `password_changed_at` is bumped on every change, and `JWTAuthentication`
  rejects any access token issued before it — resetting the password ends
  every session the old password opened.
* Keep operator accounts out of the borrower funnel: they are marked verified
  for access, but they still need documents before `can_borrow` is true.
