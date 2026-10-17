# eduroam CAT installer (third party)

`eduroam-linux-UniBasel.py` is **not AutoOS code**. It is a generated installer
from the GÉANT eduroam Configuration Assistant Tool (CAT), carrying its own
copyright and licence in the file header.

It is vendored here only so the file is not lost. Do not edit it — any change is
overwritten the next time the file is regenerated.

**Prefer downloading a fresh one.** CAT builds the installer per institution and
per user, so a checked-in copy goes stale and may not match your account:

  https://cat.eduroam.org/

If you keep using this copy, run it as your normal user:

```bash
python3 eduroam-linux-UniBasel.py
```

Provenance: GÉANT Association, developed under EU Framework Programme 7 and
Horizon 2020 grants. See the header of the script for the full notice.
